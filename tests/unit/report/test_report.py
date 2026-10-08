"""Report texts, Markdown and plots."""

import math

import numpy as np
import pytest
from scipy import signal

from churchtune.analysis.points import PointResponse, analyze_points
from churchtune.design.alignment import JunctionPoint, analyze_alignment
from churchtune.design.crossover import CrossoverNote, NoteCode
from churchtune.design.proposal import (
    DesignWarning,
    Reason,
    ReasonCode,
    SkipCode,
    TargetSpec,
)
from churchtune.design.system_eq import design
from churchtune.design.system_eq_opt import optimize_design
from churchtune.design.target import target_curve
from churchtune.domain.changes import Change
from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.domain.eq_model import band_sos
from churchtune.mixers.x32.params import X32EqGrid, X32Formats
from churchtune.report import plots
from churchtune.report.markdown import (
    PointRow,
    alignment_summary,
    describe_eq,
    export_for_chat,
    proposal_md,
    rt_interpretation,
    session_report_md,
)
from churchtune.report.texts import (
    crossover_note_text,
    design_warning_text,
    format_changes,
    proposal_note,
    quality_warning_text,
    reason_text,
    target_description,
)

FS_HZ = 48000.0
FMT = X32Formats()
GRID = X32EqGrid()
FLAT = SystemEq(True, tuple(Band(i, EqType.PEQ, 1000.0, 0.0, 2.0) for i in range(1, 7)))
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def decaying_room(rt60_s, peak=None, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(int(3 * FS_HZ)) / FS_HZ
    ir = rng.normal(size=len(t)) * np.exp(-math.log(1000.0) * t / rt60_s) * 0.02
    ir[0] += 1.0
    if peak:
        ir = signal.sosfilt(band_sos(Band(2, EqType.PEQ, *peak), FS_HZ), ir)
    return ir


@pytest.fixture(scope="module")
def an():
    return analyze_points(
        {
            f"p{i}": PointResponse(decaying_room(1.5, (200.0, 6.0, 1.5), seed=i), FS_HZ)
            for i in (1, 2, 3)
        }
    )


@pytest.fixture(scope="module")
def proposal(an):
    return optimize_design(an, FLAT, GRID, FS_HZ)


ROWS = {
    "p1": PointRow(-11.59, -57.3, 34.5, 81.4, 227.12, "ok"),
    "p2": PointRow(-5.1, -40.0, 24.0, 50.0, 219.0, "warn", ("пик -5.1 dBFS — близко к клиппингу",)),
    "p3": PointRow(),
}


# ------------------------------------------------------------ texts
def test_target_description():
    assert (
        target_description(TargetSpec())
        == "низ +3 дБ ниже ~100 Гц, ровно до 1000 Гц, выше -1.0 дБ/окт"
    )
    assert target_description(TargetSpec(0.0, 2000.0, 0.0)) == "ровно до 2000 Гц, выше +0.0 дБ/окт"


def test_every_proposal_reason_has_a_text(an, proposal):
    greedy = design(an, FLAT, GRID, FS_HZ)
    for entry in (*greedy.bands, *proposal.bands):
        text = reason_text(entry.reason, FMT)
        assert text
        assert "nan" not in text
    for skipped in (*greedy.skipped, *proposal.skipped):
        assert reason_text(skipped.reason, FMT)


@pytest.mark.parametrize("code", [c for c in ReasonCode if c is not ReasonCode.LEGACY])
def test_every_reason_code_renders_without_missing_numbers(code):
    params = {
        "freq_hz": 479.8, "gain_db": -9.0, "q": 1.5, "dev_db": 5.9, "n": 6, "n_points": 6,
        "width_oct": 0.9, "approximate": True, "mode_hint": True, "max_boost_db": 3.0,
        "spread_db": 1.2, "replaced_type": "PEQ", "replaced_freq_hz": 496.6, "replaced_gain_db": -4.0,
        "replaced_q": 1.2, "old_gain_db": -3.5, "new_gain_db": -1.75, "before_db": 4.1,
        "after_db": 0.6, "mode_zone": True,
    }  # fmt: skip

    text = reason_text(Reason(code, params), FMT)

    assert text
    assert "nan" not in text.lower()


@pytest.mark.parametrize("code", list(SkipCode))
def test_every_skip_code_renders_without_missing_numbers(code):
    params = {
        "n": 2, "n_points": 6, "cuts_first": True, "width_oct": 0.23, "spread_db": 4.2,
        "gain_db": -0.5, "min_effect_db": 0.75, "freq_hz": 800.0, "q": 4.0, "need": 4,
    }  # fmt: skip

    text = reason_text(Reason(code, params), FMT)

    assert text
    assert "nan" not in text.lower()


def test_reason_texts_say_what_the_original_tool_said():
    cut = Reason(
        ReasonCode.CUT,
        {"freq_hz": 63.0, "gain_db": -5.0, "q": 3.0, "dev_db": 6.2, "n": 6, "n_points": 6,
         "width_oct": 0.4, "approximate": False, "mode_hint": True},
    )  # fmt: skip

    text = reason_text(cut, FMT)

    assert text.startswith("Вырез -5.00 дБ @ 63.0 Гц, Q 3.0: усреднённая АЧХ на +6.2 дБ выше цели")
    assert "пик в 6/6 точках (ширина ≈0.4 окт)" in text
    assert "комнатную моду" in text
    assert "ОРИЕНТИРОВОЧНО" not in text
    assert reason_text(Reason(ReasonCode.LOCKED_CUT_FILTER, {}), FMT) == (
        "фильтр среза — заблокирован, не меняется"
    )
    assert "ОРИЕНТИРОВОЧНО" in reason_text(
        Reason(ReasonCode.CUT, {**cut.params, "approximate": True}), FMT
    )


def test_legacy_reasons_keep_their_original_text():
    assert reason_text(Reason(ReasonCode.LEGACY, {"text": "старый текст"}), FMT) == "старый текст"
    assert reason_text(Reason(SkipCode.NARROW_DIP, {"text": "узкий провал (0.2 окт)"}), FMT) == (
        "узкий провал (0.2 окт)"
    )


@pytest.mark.parametrize("warning", list(DesignWarning))
def test_every_design_warning_has_a_text(warning):
    assert len(design_warning_text(warning)) > 20


def test_proposal_note_names_the_method(an, proposal):
    assert "optimize" in proposal_note(proposal)
    assert "RBJ" in proposal_note(design(an, FLAT, GRID, FS_HZ))


@pytest.mark.parametrize("code", list(NoteCode))
def test_every_crossover_note_has_a_text(code):
    note = CrossoverNote(
        code, 3, Band(6, EqType.HSHV, 751.7, -15.0, 2.0),
        {"fc_hz": 101.39, "slope": "LR24", "old_db": -48.0, "new_db": -12.0, "at_hz": 50.7},
    )  # fmt: skip

    text = crossover_note_text(note, FMT)

    assert "Mtx3" in text
    assert "nan" not in text.lower()


def test_crossover_note_for_the_sub_low_pass_reads_like_the_original():
    note = CrossoverNote(
        NoteCode.SUB_LOW_PASS, 3, Band(6, EqType.HSHV, 751.7, -15.0, 2.0),
        {"fc_hz": 101.39, "slope": "LR24"},
    )  # fmt: skip

    assert crossover_note_text(note, FMT) == (
        "Mtx3 band 6: HShv  751.7 Hz  -15.0 dB Q 2.0 -> LR24 101.4 Гц (ФНЧ саба)"
    )


@pytest.mark.parametrize(
    ("code", "params", "fragment"),
    [
        ("clipping", {"count": 12, "threshold_dbfs": -0.5}, "КЛИППИНГ: 12 сэмплов"),
        ("hot", {"peak_dbfs": -4.0}, "пик -4.0 dBFS"),
        ("quiet", {"peak_dbfs": -50.0}, "слишком тихо"),
        ("low-snr", {"snr_db": 24.1, "noise_dbfs": -41.0}, "низкий SNR 24.1 дБ"),
        ("low-pnr", {"pnr_db": 30.0, "needed_db": 45.0, "unreliable": True}, "замер ненадёжен"),
        (
            "low-pnr",
            {"pnr_db": 40.0, "needed_db": 45.0, "unreliable": False},
            "T30 будет недоступен",
        ),
        ("xrun", {"flags": "input overflow"}, "input overflow"),
        ("legacy", {"text": "как было"}, "как было"),
        ("something-new", {"x": 1}, "something-new"),
    ],
)
def test_quality_warning_texts(code, params, fragment):
    assert fragment in quality_warning_text(code, params)


def test_format_changes_is_a_diff_table():
    changes = [
        Change("/main/st/eq/2/g", 0.37, 0.2, "-4.00 dB", "-9.00 dB"),
        Change("/main/st/eq/2/f", 0.465, 0.46, "496.6 Hz", "479.8 Hz"),
    ]

    lines = format_changes(changes, "Изменения (только /main/st/eq/*)").splitlines()

    assert lines[0] == "Изменения (только /main/st/eq/*) (2):"
    assert lines[2] == "  /main/st/eq/2/g      -4.00 dB  ->  -9.00 dB    "
    assert format_changes([], "Вернуть") == "Вернуть: нет (пульт уже в целевом состоянии)"


# ------------------------------------------------------------ markdown
def test_rt_interpretation_grades_the_mid_band_decay(an):
    notes = rt_interpretation(an)

    # RT60 1.5 s in every band: "boomy for speech", bass ratio about 1.
    assert notes[0].startswith("RT60 на 500–1000 Гц ≈ 1.")
    assert "гулко для речи" in notes[0]
    assert notes[1].startswith("Bass ratio (125–250 / 500–1k) = ")
    assert notes[1].endswith(".")


@pytest.mark.parametrize(
    ("rt60_s", "fragment"),
    [(0.5, "сухой зал"), (1.1, "хороший компромисс"), (2.6, "очень гулкий зал")],
)
def test_rt_interpretation_bands(rt60_s, fragment):
    analysis = analyze_points({"p1": PointResponse(decaying_room(rt60_s), FS_HZ)})

    assert fragment in rt_interpretation(analysis)[0]


def test_rt_interpretation_is_empty_without_decay_times():
    impulse = np.zeros(4800)
    impulse[0] = 1.0

    assert rt_interpretation(analyze_points({"p1": PointResponse(impulse, FS_HZ)})) == []


def test_describe_eq():
    text = describe_eq(FLAT.with_on(False), FMT, "Main LR EQ")

    assert text.splitlines()[0] == "Main LR EQ: OFF"
    assert text.splitlines()[2] == "  band 2: PEQ    1k00 Hz  +0.00 dB Q 2.0"


def test_session_report_has_every_section_and_a_row_per_point(an):
    report = session_report_md(
        "after-sub",
        an,
        ROWS,
        FMT,
        generated="2026-10-02 19:19",
        mic_label="ECM8000 (вход 10)",
        eq_meas=FLAT,
    )

    assert report.startswith("# Отчёт замера — сессия `after-sub`")
    assert "Сформирован: 2026-10-02 19:19. Точек: 3. Микрофон: ECM8000 (вход 10)." in report
    for heading in (
        "## Качество замеров",
        "## Усреднённая АЧХ",
        "## RT60 по октавам",
        "## Интерпретация",
        "## Графики",
    ):
        assert heading in report
    assert "| p1 | -11.59 | -57.3 | 34.5 | 81.4 | 227.12 | ok |  |" in report
    assert (
        "| p2 | -5.1 | -40.0 | 24.0 | 50.0 | 219.0 | warn | пик -5.1 dBFS — близко к клиппингу |"
        in report
    )
    assert "| p3 | — | — | — | — | — | ? |  |" in report
    assert "Main LR EQ: ON" in report
    assert report.count("\n| ") > 3 + 28 + 8  # points, third-octave rows, octave rows
    assert "| 1000 | 1." in report  # RT60 around 1.5 s at 1 kHz
    assert "![rt60](rt60.png)" in report


def test_session_report_without_eq_leaves_the_eq_block_out(an):
    report = session_report_md("s", an, {}, FMT, generated="t", mic_label="mic")

    assert "EQ во время замера" not in report


def test_proposal_section_lists_bands_prediction_and_skips(an, proposal):
    text = proposal_md(proposal, FMT)

    assert "## Предложение EQ Main LR" in text
    assert "Цель: низ +3 дБ ниже ~100 Гц, ровно до 1000 Гц, выше -1.0 дБ/окт." in text
    assert text.count("\n| ") == 2 + 6 - 1  # header rows and six bands
    assert "Прогноз (модель, " in text
    assert (
        f"{proposal.prediction.rms_dev_before_db} → {proposal.prediction.rms_dev_after_db} дБ"
        in text
    )
    assert "![proposal](proposal.png)" in text
    changed = [e for e in proposal.bands if e.action != "keep"]
    assert changed
    assert f"`{FMT.band(changed[0].band)}`" in text


def test_proposal_section_shows_warnings_and_approximate_flags(an):
    proposal = design(an, FLAT.with_on(False), GRID, FS_HZ, eq_now=FLAT)

    text = proposal_md(proposal, FMT)

    assert "**Предупреждения:**" in text
    assert "был ВЫКЛЮЧЕН" in text


def test_alignment_summary_reads_like_the_original():
    click = np.zeros(1 << 15)
    click[4800] = 1.0
    late = np.roll(click, 144) * 0.5  # the sub arrives 3 ms late, 6 dB down
    result = analyze_alignment(
        {"p1-center": JunctionPoint(late, click, late + click)}, 100.0, FS_HZ
    )

    lines = alignment_summary(result).splitlines()

    assert lines[0] == "Кроссовер 100 Гц, точек: 1"
    assert lines[1].startswith("  p1-center: минимум суммы в зоне стыка сейчас ")
    assert "Рекомендация: задержка сателлитов 3.0 мс, полярность саба НЕ менять" in lines[2]
    assert lines[3] == "Уровень на 100 Гц: саб − сателлиты = -6.0 дБ (при LR24 должно быть ~0)"


def test_export_for_chat_is_compact_and_complete(an, proposal):
    text = export_for_chat(
        "after-sub", an, ROWS, FMT, generated="2026-10-02 21:59", setup="mic=ECM8000 uncalibrated fs=48k sweep=ESS 20-20k 6s",
        proposal=proposal, proposal_created="2026-10-02T19:19:31", eq_now=FLAT, verify="RMS deviation: before 2.81 dB -> after 2.36 dB",
    )  # fmt: skip

    assert text.startswith("# X32 church room measurement — export for chat (2026-10-02 21:59)\n")
    assert "session=after-sub points=3 (p1, p2, p3) mic=ECM8000 uncalibrated fs=48k" in text
    assert "p1: -11.59 / 34.5 / 81.4 / ok" in text
    for heading in (
        "## Averaged response, 1/6 oct", "## RT60 (octave: T20 / T30 / EDT s)", "## EQ at measurement time",
        "## Current Main LR EQ (read via OSC now): ON", "## Proposal (2026-10-02T19:19:31", "## Verify",
    ):  # fmt: skip
        assert heading in text
    assert "predicted RMS dev" in text
    assert text.endswith("leave to acoustics?\n")
    assert len(text.encode()) < 8000


def test_export_for_chat_without_proposal_or_console(an):
    text = export_for_chat("s", an, {}, FMT, generated="t", setup="mic=?")

    assert "## Proposal" not in text
    assert "## Current Main LR EQ" not in text
    assert "## Verify" not in text


# ------------------------------------------------------------ plots
def assert_png(path):
    data = path.read_bytes()
    assert data.startswith(PNG_MAGIC)
    assert len(data) > 5000


def test_session_plots_are_written_as_png(an, tmp_path):
    target = target_curve(an.grid_hz, -1.0, 1000.0, 3.0, 100.0)

    plots.plot_points(an, tmp_path / "out" / "fr_points.png")
    plots.plot_average(an, tmp_path / "fr_average.png", target)
    plots.plot_average(an, tmp_path / "fr_average_no_target.png")
    plots.plot_rt60(an, tmp_path / "rt60.png")

    for name in ("out/fr_points.png", "fr_average.png", "fr_average_no_target.png", "rt60.png"):
        assert_png(tmp_path / name)


def test_proposal_and_verify_plots_are_written_as_png(an, proposal, tmp_path):
    target = target_curve(an.grid_hz, -1.0, 1000.0, 3.0, 100.0)

    plots.plot_proposal(an, proposal, tmp_path / "proposal.png", target, FS_HZ)
    plots.plot_verify(an, an, tmp_path / "verify.png", target)
    plots.plot_verify(an, an, tmp_path / "verify_no_target.png")

    for name in ("proposal.png", "verify.png", "verify_no_target.png"):
        assert_png(tmp_path / name)


def test_alignment_plot_is_written_as_png(tmp_path):
    click = np.zeros(1 << 15)
    click[4800] = 1.0
    result = analyze_alignment(
        {"p1": JunctionPoint(-click, click, click * 0)}, 100.0, FS_HZ, step_ms=1.0
    )

    plots.plot_alignment(result, tmp_path / "sub_align.png")

    assert_png(tmp_path / "sub_align.png")


def test_plotting_leaves_the_global_matplotlib_style_alone(an, tmp_path):
    import matplotlib as mpl  # noqa: PLC0415

    before = dict(mpl.rcParams)

    plots.plot_points(an, tmp_path / "p.png")

    assert dict(mpl.rcParams) == before
