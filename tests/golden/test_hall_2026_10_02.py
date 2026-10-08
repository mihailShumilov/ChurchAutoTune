"""Golden tests on the real measurements of the reference hall (2026-10-02).

Inputs are the impulse responses and recordings taken that evening; expected values are what the
original tool computed and what was applied to the console and verified by ear. The new code
must reproduce them: the EQ proposal, the response tables, the sub / top alignment analysis and
the exact console writes.

Tolerances: proposed bands lie on the console's parameter grid, so they are compared to 1e-9 (a
difference would be a whole grid step); tables are compared as printed.
"""

import json
import re
from dataclasses import replace

import numpy as np
import pytest

from churchtune.analysis.points import PointResponse, analyze_points
from churchtune.analysis.response import GRID_HZ, interpolate_at, magnitude_db
from churchtune.design.alignment import JunctionPoint, analyze_alignment, recommend_level
from churchtune.design.crossover import plan_crossover
from churchtune.design.proposal import Method
from churchtune.design.system_eq_opt import optimize_design
from churchtune.domain.changes import WriteScope
from churchtune.domain.eq import EqType
from churchtune.measure.config import QualityGates, SweepConfig
from churchtune.measure.deconvolution import deconvolve, extract_ir
from churchtune.measure.gates import Verdict, assess
from churchtune.measure.stimulus import build_stimulus, make_sweep
from churchtune.mixers.x32.laws import X32ValueLaws
from churchtune.mixers.x32.nodes import read_console_state
from churchtune.mixers.x32.params import X32EqGrid
from churchtune.mixers.x32.topology import detect_roles
from churchtune.safety.plan import plan_changes, same_step
from churchtune.safety.policy import WritePolicy
from churchtune.safety.sub_plan import alignment_changes, crossover_changes
from churchtune.storage import files
from tests.fixtures.paths import DUMP_2026_10_02, GOLDEN_HALL, DumpReader

FS_HZ = 48000.0
GRID = X32EqGrid()
LAWS = X32ValueLaws()
SWEEP = SweepConfig()
MEASUREMENTS = GOLDEN_HALL / "measurements"
RESULTS = GOLDEN_HALL / "results"
UNDO = GOLDEN_HALL / "undo"
POINTS = ["p1-center", "p2-left", "p3-right", "p4-mid", "p5-side", "p6-back"]


def analysed(session):
    points = files.load_points(MEASUREMENTS / session)
    assert list(points) == POINTS
    assert all(p.fs_hz == FS_HZ for p in points.values())
    return analyze_points({n: PointResponse(p.ir, p.fs_hz) for n, p in points.items()})


@pytest.fixture(scope="module")
def after_sub():
    return analysed("after-sub")


@pytest.fixture(scope="module")
def reference_proposal():
    return files.read_proposal(RESULTS / "after-sub" / "eq_proposal.json")


@pytest.fixture(scope="module")
def proposal(after_sub):
    eq_meas = files.read_session(MEASUREMENTS / "after-sub").eq_at_measurement.to_domain()
    return optimize_design(after_sub, eq_meas, GRID, FS_HZ)


# ------------------------------------------------------------ G1: the EQ proposal
def test_proposal_reproduces_the_eq_that_was_applied(proposal, reference_proposal):
    assert proposal.method is Method.OPTIMIZE
    assert proposal.n_points == reference_proposal.points == 6
    for new, old in zip(proposal.bands, reference_proposal.bands, strict=True):
        assert new.band.band == old.band
        assert new.band.type == old.type
        assert new.band.freq_hz == pytest.approx(old.freq, abs=1e-9)
        assert new.band.gain_db == pytest.approx(old.gain, abs=1e-9)
        assert new.band.q == pytest.approx(old.q, abs=1e-9)
        assert new.action.value == old.action
        assert new.approximate == old.approximate


def test_applied_bands_are_the_three_known_ones(proposal):
    changed = {b.band.band: b.band for b in proposal.bands if b.action.value != "keep"}

    assert sorted(changed) == [2, 4, 6]
    # Main LR EQ applied on 2026-10-02: 479.8 Hz -9 dB Q1.5 / 1k97 -1.75 dB Q6.4 / 50.8 Hz -6 dB Q3.7
    assert (round(changed[2].freq_hz, 1), changed[2].gain_db, round(changed[2].q, 1)) == (
        479.8,
        -9.0,
        1.5,
    )
    assert (round(changed[4].freq_hz, -1), changed[4].gain_db, round(changed[4].q, 1)) == (
        1980.0,
        -1.75,
        6.4,
    )
    assert (round(changed[6].freq_hz, 1), changed[6].gain_db, round(changed[6].q, 1)) == (
        50.8,
        -6.0,
        3.7,
    )
    assert all(band.type is EqType.PEQ for band in changed.values())


def test_proposal_prediction_and_range_match(proposal, reference_proposal):
    expected = reference_proposal.prediction

    assert proposal.prediction.rms_dev_before_db == expected.rms_dev_before_db == 2.81
    assert proposal.prediction.rms_dev_after_db == expected.rms_dev_after_db == 2.36
    assert proposal.prediction.max_dev_before_db == expected.max_dev_before_db == 5.86
    assert proposal.prediction.max_dev_after_db == expected.max_dev_after_db == 4.33
    assert proposal.prediction.range_hz == expected.range_hz == (35.1, 10000.0)
    assert proposal.f_low_hz == reference_proposal.f_low_hz == 26.3


def test_proposal_has_no_warnings_and_was_made_for_the_eq_on_the_console(
    proposal, reference_proposal
):
    assert proposal.warnings == ()
    assert reference_proposal.warnings == ()
    assert reference_proposal.eq_current == reference_proposal.eq_at_measurement


def test_proposal_leaves_the_same_features_alone(proposal, reference_proposal):
    got = [(s.kind.value, s.freq_hz, s.dev_db, s.width_oct, s.points) for s in proposal.skipped]
    expected = [
        (s.kind, s.freq_hz, s.dev_db, s.width_oct, s.points) for s in reference_proposal.skipped
    ]

    assert got == expected
    assert len(got) == 6


# ------------------------------------------------------------ G1b: responses and tables
@pytest.mark.parametrize("session", ["after-sub", "2026-10-02"])
@pytest.mark.parametrize("point", POINTS)
def test_response_of_every_point_matches_the_stored_curve(session, point):
    stored = np.loadtxt(MEASUREMENTS / session / point / "fr.csv", delimiter=",", skiprows=1)
    ir = files.load_points(MEASUREMENTS / session)[point].ir

    response = magnitude_db(ir, FS_HZ)

    np.testing.assert_allclose(stored[:, 0], GRID_HZ, atol=5e-4)
    # The file holds three decimals.
    np.testing.assert_allclose(response, stored[:, 1], atol=6e-4)


def _table(report, heading):
    section = report.split(heading, 1)[1]
    rows = []
    for line in section.splitlines()[3:]:
        if not line.startswith("|"):
            if rows:
                break
            continue
        if not line.startswith("|---"):
            rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows[1:] if rows and not rows[0][0].replace(".", "").isdigit() else rows


@pytest.mark.parametrize("session", ["after-sub", "2026-10-02"])
def test_third_octave_table_matches_the_original_report(session):
    an = analysed(session)
    report = (RESULTS / session / "report.md").read_text(encoding="utf-8")

    rows = _table(report, "## Усреднённая АЧХ")

    assert len(rows) == 29
    for centre, avg, spread, low, high in rows:
        freq = np.array([1000 * 2 ** (round(np.log2(float(centre) / 1000) * 3) / 3)])
        assert f"{interpolate_at(freq, an.avg_db)[0]:+.1f}" == avg, centre
        assert f"{interpolate_at(freq, an.spread_db)[0]:.1f}" == spread, centre
        assert f"{interpolate_at(freq, an.min_db)[0]:+.1f}" == low, centre
        assert f"{interpolate_at(freq, an.max_db)[0]:+.1f}" == high, centre


@pytest.mark.parametrize("session", ["after-sub", "2026-10-02"])
def test_rt60_table_matches_the_original_report(session):
    an = analysed(session)
    report = (RESULTS / session / "report.md").read_text(encoding="utf-8")

    rows = _table(report, "## RT60 по октавам")

    def shown(value):
        return "—" if value is None else f"{value:.2f}"

    assert [int(r[0]) for r in rows] == [63, 125, 250, 500, 1000, 2000, 4000, 8000]
    for octave, t20, t30, edt, sd, counts in rows:
        decay = an.rt60[int(octave)]
        assert shown(decay.t20.mean_s) == t20, octave
        assert shown(decay.t30.mean_s) == t30, octave
        assert shown(decay.edt.mean_s) == edt, octave
        assert shown(decay.t20.sd_s) == sd, octave
        assert f"{decay.t20.n_points}/{decay.t30.n_points}" == counts, octave


def test_baseline_hall_is_reverberant_in_the_mid_band():
    an = analysed("2026-10-02")

    # RT60 about 1.5 s at 250..2000 Hz, measured in the empty hall.
    for octave in (250, 500, 1000, 2000):
        assert an.rt60[octave].t20.mean_s == pytest.approx(1.52, abs=0.06)


# ------------------------------------------------------------ G2: sub / top junction
def junction(session):
    stored = files.load_junction_points(MEASUREMENTS / session)
    assert list(stored) == ["p1-center", "p4-mid"]
    return analyze_alignment(
        {n: JunctionPoint(p.sub, p.tops, p.both) for n, p in stored.items()}, 100.0, FS_HZ
    )


def test_junction_before_the_level_change_matches_the_original_analysis():
    result = junction("sub-2026-10-02")

    # results/sub-2026-10-02/sub_align.txt
    assert (result.delay_ms, result.polarity) == (9.2, 1)
    # The search grid itself: 9.2 and -1.8 ms also lie on a coarser grid.
    assert len(result.delays_ms) == 301
    assert np.allclose(np.diff(result.delays_ms), 0.1, atol=1e-9)
    assert f"{result.score_now_db:+.1f} -> {result.score_best_db:+.1f}" == "-11.4 -> -10.6"
    assert result.sub_minus_tops_at_fc_db == -8.6
    p1, p4 = result.points["p1-center"], result.points["p4-mid"]
    assert (p1.min_now_db, p1.min_best_db, f"{p1.check_mismatch_db:.1f}") == (-15.8, -16.1, "0.1")
    assert (p4.min_now_db, p4.min_best_db, f"{p4.check_mismatch_db:.1f}") == (-16.0, -14.8, "0.2")
    assert recommend_level(result) == 6.0  # capped; applied as -37.5 -> -31.5 dB


def test_junction_after_the_first_level_change_matches_the_original_analysis():
    result = junction("lvl")

    # results/lvl/sub_align.txt
    assert (result.delay_ms, result.polarity) == (-1.8, 1)
    assert f"{result.score_now_db:+.1f} -> {result.score_best_db:+.1f}" == "-8.0 -> -7.5"
    assert result.sub_minus_tops_at_fc_db == -2.2
    p1, p4 = result.points["p1-center"], result.points["p4-mid"]
    assert (p1.min_now_db, p1.min_best_db) == (-14.3, -12.2)
    assert (p4.min_now_db, p4.min_best_db) == (-12.0, -9.4)
    assert recommend_level(result) == pytest.approx(2.2)  # applied as -31.5 -> -29.3 dB


@pytest.mark.parametrize("session", ["sub-2026-10-02", "lvl"])
def test_delay_would_gain_less_than_1_db_so_none_was_set(session):
    """The decision taken in the hall: fix filters and level first; the delay search then shows
    less than 1 dB to gain, so no delay is set."""
    assert junction(session).gain_db < 1.0


def test_junction_measurements_are_consistent():
    for session in ("sub-2026-10-02", "lvl"):
        for point in junction(session).points.values():
            assert point.check_mismatch_db < 0.5  # the "both" block agrees with sub + tops


# ------------------------------------------------------------ G3: recording -> impulse response
def test_deconvolving_the_real_recording_reproduces_the_stored_impulse_response():
    """``p1-center`` of the baseline session was measured with one sweep, so its recording and
    its impulse response belong together exactly."""
    point_dir = MEASUREMENTS / "2026-10-02" / "p1-center"
    recording, fs_hz = files.read_wav(point_dir / "recording.wav")
    stored = files.load_points(MEASUREMENTS / "2026-10-02")["p1-center"]
    meta = stored.meta
    stimulus = build_stimulus(
        make_sweep(fs_hz, SWEEP, meta.level_dbfs), fs_hz, SWEEP.preroll_s, SWEEP.tail_s
    )
    ref = stimulus[:, 0].astype(np.float64)
    assert len(recording) == len(ref) == 480000

    ir_full = deconvolve(recording, ref, fs_hz, SWEEP.f1_hz, SWEEP.f2_hz)
    ir, peak_idx = extract_ir(ir_full, fs_hz, SWEEP.ir_length_s, SWEEP.ir_pre_ms)
    quality = assess(
        recording, np.abs(recording), ir_full, peak_idx, fs_hz=fs_hz, sweep=SWEEP, flags=[],
        gates=QualityGates(),
    )  # fmt: skip

    assert len(ir) == len(stored.ir) == 153840
    # Both sides went through float32 on disk; the peak of the response is about 0.1.
    np.testing.assert_allclose(ir, stored.ir, atol=2e-7)
    assert quality.latency_ms == meta.latency_ms == 227.12
    assert quality.peak_dbfs == meta.peak_dbfs == -11.59
    assert quality.noise_dbfs == meta.noise_dbfs == -59.66
    assert quality.sweep_rms_dbfs == meta.sweep_rms_dbfs == -25.11
    assert (quality.snr_db, quality.pnr_db) == (meta.snr_db, meta.pnr_db) == (34.5, 81.4)
    assert quality.verdict is Verdict.OK
    assert quality.clipped_samples == 0


def test_every_measured_point_was_clean():
    for session in ("after-sub", "2026-10-02"):
        for name, point in files.load_points(MEASUREMENTS / session).items():
            assert point.meta.verdict == "ok", (session, name)
            assert point.meta.warnings == ()
            assert point.meta.snr_db >= 30


# ------------------------------------------------------------ G4: the console writes
def reference_changes(name):
    return files.read_undo(UNDO / name).changes


def assert_same_writes(planned, reference):
    assert [c.path for c in planned] == [c.path for c in reference]
    for new, old in zip(planned, reference, strict=True):
        kind = new.path.rsplit("/", 1)[1]
        assert same_step(LAWS, kind, new.new_value, old.new_value), new.path
        assert same_step(LAWS, kind, new.old_value, old.old_value), new.path
        assert type(new.new_value) is type(old.new_value), new.path
        assert (new.old_display, new.new_display) == (old.old_display, old.new_display), new.path


@pytest.fixture(scope="module")
def console_before():
    return read_console_state(DumpReader(DUMP_2026_10_02))


def test_crossover_plan_reproduces_the_writes_made_in_the_hall(console_before):
    roles = detect_roles(console_before)
    assert (roles.sub, roles.mains, roles.others) == ((3,), (1, 2), (5, 6))
    policy = WritePolicy(LAWS, frozenset(roles.all))

    plan = plan_crossover(console_before, roles, GRID, FS_HZ, fc_hz=100.0)
    changes = crossover_changes(console_before, plan.targets, policy)

    # undo_sub_20261002_184249.json: "crossover LR24 100Hz", LR24 at 101.4 Hz on Mtx 1/2/3
    assert_same_writes(changes, reference_changes("undo_sub_20261002_184249.json"))
    assert plan.fc_hz == pytest.approx(101.4, abs=0.05)
    assert len(changes) == 12


@pytest.mark.parametrize(
    ("fader_before_db", "change_db", "undo_name", "fader_after"),
    [
        (-37.5, 6.0, "undo_sub_20261002_185031.json", "-31.5"),
        (-31.5, 2.2, "undo_sub_20261002_185632.json", "-29.3"),
    ],
)
def test_level_changes_reproduce_the_fader_writes_made_in_the_hall(
    console_before, fader_before_db, change_db, undo_name, fader_after
):
    roles = detect_roles(console_before)
    sub = replace(console_before.matrices[3], fader_db=fader_before_db)
    state = replace(console_before, matrices={**console_before.matrices, 3: sub})
    policy = WritePolicy(LAWS, frozenset(roles.all))

    changes = alignment_changes(
        state, roles, policy, delay_ms=0.0, polarity=1, fader_change_db=change_db
    )

    assert_same_writes(changes, reference_changes(undo_name))
    assert changes[0].new_display == fader_after


def test_main_eq_plan_reproduces_the_writes_made_in_the_hall(reference_proposal):
    undo = files.read_undo(UNDO / "undo_20261002_191941.json")
    current = undo.eq_before.to_domain()
    target = replace(
        current,
        on=reference_proposal.eq_on,
        bands=tuple(b.to_domain() for b in reference_proposal.bands),
    )
    policy = WritePolicy(LAWS, frozenset())

    changes = plan_changes(current, target, policy)

    # 479.8 Hz -9 dB Q1.5, 1k97 -1.75 dB, 50.8 Hz -6 dB Q3.7; gain first because it goes down.
    assert_same_writes(changes, undo.changes)
    assert [c.path for c in changes][:3] == [
        "/main/st/eq/2/g",
        "/main/st/eq/2/f",
        "/main/st/eq/2/q",
    ]
    for change in changes:
        policy.validate(change.path, change.new_value, WriteScope.MAIN)
    assert all(result.ok for result in undo.results)


@pytest.mark.parametrize("name", ["undo_20261002_150418.json", "undo_20261002_150425.json"])
def test_afternoon_main_eq_plans_reproduce_the_order_of_the_writes(name):
    """Two earlier Main EQ changes of the same day: gain before frequency, and PEQ -> VEQ."""
    undo = files.read_undo(UNDO / name)

    changes = plan_changes(
        undo.eq_before.to_domain(), undo.eq_after.to_domain(), WritePolicy(LAWS, frozenset())
    )

    assert_same_writes(changes, undo.changes)


def test_console_readbacks_of_that_evening_land_on_the_planned_grid_steps():
    for name in (
        "undo_20261002_191941.json",
        "undo_sub_20261002_184249.json",
        "undo_sub_20261002_185632.json",
    ):
        undo = files.read_undo(UNDO / name)
        assert undo.results, name
        for result in undo.results:
            kind = result.path.rsplit("/", 1)[1]
            assert same_step(LAWS, kind, result.readback, result.wanted), result.path


# ------------------------------------------------------------ the stored files themselves
def test_every_file_of_the_original_tool_in_the_fixture_is_readable():
    for session in ("after-sub", "2026-10-02"):
        stored = files.read_session(MEASUREMENTS / session)
        assert stored.kind == "baseline"
        assert stored.eq_at_measurement.to_domain().band(1).type is EqType.BU18
    for name in sorted(p.name for p in UNDO.glob("*.json")):
        undo = files.read_undo(UNDO / name)
        assert undo.scope is (WriteScope.SUB if "_sub_" in name else WriteScope.MAIN), name
        assert undo.ip in ("192.0.2.80", None)
    for session in ("sub-2026-10-02", "lvl"):
        for point in files.load_junction_points(MEASUREMENTS / session).values():
            assert point.meta.roles.sub == (3,)
            assert point.meta.state.sub_fader in (-37.5, -31.5)


def test_fixture_holds_no_personal_or_site_data():
    private_address = re.compile(
        r"192\.168\.|\b10\.\d+\.\d+\.\d+\b|\b172\.(1[6-9]|2\d|3[01])\.\d+\.\d+\b"
    )
    texts = [p for p in GOLDEN_HALL.rglob("*") if p.suffix in (".json", ".md", ".txt", ".csv")]
    for path in [*texts, *DUMP_2026_10_02.parent.glob("*.txt")]:
        assert not private_address.search(path.read_text(encoding="utf-8")), path
    for dump in DUMP_2026_10_02.parent.glob("*.txt"):
        prefs = [
            ln for ln in dump.read_text(encoding="utf-8").splitlines() if ln.startswith("/-prefs ")
        ]
        assert all('"ChurchTune"' in line for line in prefs), dump
    assert json.loads((UNDO / "undo_20261002_191941.json").read_text())["ip"] == "192.0.2.80"
