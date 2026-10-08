"""Markdown session report, proposal section, alignment summary and the export for a chat.

Texts are Russian literals until M3 (docs/adr/0003); the export for a chat is English, as in
the original tool.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from churchtune.analysis.bands import OCTAVES_HZ
from churchtune.analysis.points import Analysis, DecayStat
from churchtune.analysis.response import (
    interpolate_at,
    sixth_octave_centres,
    third_octave_centres,
)
from churchtune.design.alignment import AlignmentResult
from churchtune.design.proposal import Action, Proposal
from churchtune.domain.eq import SystemEq
from churchtune.report.texts import (
    ValueFormats,
    design_warning_text,
    reason_text,
    target_description,
)

DRY_BELOW_S = 0.8
GOOD_UP_TO_S = 1.4
BOOMY_UP_TO_S = 2.0
BASS_RATIO_HIGH = 1.3
CHAT_ROW = 6
MISSING = "—"


@dataclass(frozen=True, slots=True)
class PointRow:
    """Quality numbers of one point, as shown in reports; None where not recorded."""

    peak_dbfs: float | None = None
    noise_dbfs: float | None = None
    snr_db: float | None = None
    pnr_db: float | None = None
    latency_ms: float | None = None
    verdict: str | None = None
    warnings: tuple[str, ...] = ()


def _value(value: float | None, digits: int = 2) -> str:
    return MISSING if value is None else f"{value:.{digits}f}"


def _plain(value: object) -> str:
    return MISSING if value is None else str(value)


def _at(freq_hz: float, curve: np.ndarray) -> float:
    return float(interpolate_at(np.array([freq_hz]), curve)[0])


def _mean_of(stats: Sequence[DecayStat]) -> float | None:
    values = [s.mean_s for s in stats if s.mean_s]
    return sum(values) / len(values) if values else None


def rt_interpretation(an: Analysis) -> list[str]:
    """What the mid-band RT60 and the bass ratio mean for speech and singing."""
    best = {f: an.rt60[f].t20 if an.rt60[f].t20.mean_s else an.rt60[f].t30 for f in an.rt60}
    mid = _mean_of([best[500], best[1000]])
    if mid is None:
        return []
    if mid < DRY_BELOW_S:
        verdict = "сухой зал — речь разборчива, пение звучит «мёртво»"
    elif mid <= GOOD_UP_TO_S:
        verdict = (
            "хороший компромисс для речи и пения (типично для церквей под проповедь и "
            "современное прославление)"
        )
    elif mid <= BOOMY_UP_TO_S:
        verdict = (
            "гулко для речи: разборчивость страдает, громкость PA поднимать нельзя — лучше "
            "направленность и вырезы гула"
        )
    else:
        verdict = (
            "очень гулкий зал: EQ не решает проблему, нужна акустическая обработка "
            "(панели, ткани, ковёр)"
        )
    notes = [f"RT60 на 500–1000 Гц ≈ {mid:.2f} с — {verdict}."]
    low = _mean_of([best[125], best[250]])
    if low is not None:
        ratio = low / mid
        tail = (
            " — бас звенит дольше середины, отсюда гул; вырезы в НЧ уместны."
            if ratio > BASS_RATIO_HIGH
            else "."
        )
        notes.append(f"Bass ratio (125–250 / 500–1k) = {ratio:.2f}{tail}")
    return notes


def describe_eq(eq: SystemEq, fmt: ValueFormats, title: str = "EQ") -> str:
    """An EQ block as text: the switch and one line per band."""
    lines = [f"{title}: {'ON' if eq.on else 'OFF'}"]
    lines += [f"  band {b.band}: {fmt.band(b)}" for b in eq.bands]
    return "\n".join(lines)


def session_report_md(  # noqa: PLR0913 - the report gathers everything about a session
    session: str,
    an: Analysis,
    rows: Mapping[str, PointRow],
    fmt: ValueFormats,
    *,
    generated: str,
    mic_label: str,
    eq_meas: SystemEq | None = None,
) -> str:
    """The Markdown report of a measured session.

    Args:
        session: Session name.
        an: Its analysis.
        rows: Quality numbers per point.
        fmt: Console value formats.
        generated: When the report was made, as text.
        mic_label: Measurement microphone and input, for the header.
        eq_meas: EQ that was active during the measurement.
    """
    lines = [
        f"# Отчёт замера — сессия `{session}`",
        "",
        f"Сформирован: {generated}. Точек: {len(an.norm_db)}. Микрофон: {mic_label}. "
        "Сглаживание 1/6 окт, окно ИХ 500 мс, каждая точка нормирована к 0 дБ в полосе "
        "500–2000 Гц, усреднение энергетическое.",
        "",
    ]
    if eq_meas is not None:
        lines += ["EQ во время замера:", "```", describe_eq(eq_meas, fmt, "Main LR EQ"), "```", ""]
    lines += [
        "## Качество замеров",
        "",
        "| точка | пик, dBFS | шум, dBFS | SNR, дБ | PNR ИХ, дБ | задержка, мс | оценка "
        "| замечания |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, row in rows.items():
        lines.append(
            f"| {name} | {_plain(row.peak_dbfs)} | {_plain(row.noise_dbfs)} | "
            f"{_plain(row.snr_db)} | {_plain(row.pnr_db)} | {_plain(row.latency_ms)} | "
            f"{row.verdict or '?'} | {'; '.join(row.warnings)} |"
        )
    lines += [
        "",
        "## Усреднённая АЧХ (1/3 окт для обзора)",
        "",
        f"Нижняя граница коррекции (спад колонок/HPF): ≈{an.f_low_hz:.0f} Гц.",
        "",
        "| Гц | среднее, дБ | σ между точками | мин | макс |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {fc:.0f} | {_at(fc, an.avg_db):+.1f} | {_at(fc, an.spread_db):.1f} | "
        f"{_at(fc, an.min_db):+.1f} | {_at(fc, an.max_db):+.1f} |"
        for fc in third_octave_centres(25, 16000)
    ]
    lines += [
        "",
        "Выше ~10 кГц данные ориентировочные (измерительный микрофон без индивидуальной "
        "калибровки; файл `mic_cal.txt` подхватывается автоматически, если он есть).",
        "",
        "## RT60 по октавам (среднее по точкам)",
        "",
        "| октава, Гц | T20, с | T30, с | EDT, с | σ T20 | точек T20/T30 |",
        "|---|---|---|---|---|---|",
    ]
    for centre in OCTAVES_HZ:
        decay = an.rt60[centre]
        lines.append(
            f"| {centre} | {_value(decay.t20.mean_s)} | {_value(decay.t30.mean_s)} | "
            f"{_value(decay.edt.mean_s)} | {_value(decay.t20.sd_s)} | "
            f"{decay.t20.n_points}/{decay.t30.n_points} |"
        )
    lines += [
        "",
        "T30 считается только при динамическом диапазоне ≥ 45 дБ, T20 — ≥ 35 дБ (иначе «—»). "
        "Если T30 и T20 расходятся больше чем в 1.5 раза (нелинейный спад, обычно ниже "
        "125 Гц), T30 отброшен.",
        "",
        "## Интерпретация",
        "",
    ]
    lines += [f"- {note}" for note in rt_interpretation(an)]
    lines += [
        "- Узкие провалы, разные от точки к точке — интерференция (колонки L/R, отражения). "
        "EQ их не лечит.",
        "- Пики, общие для всех точек, — кандидаты на вырез (`ctune propose`).",
        "",
        "## Графики",
        "",
        "![points](fr_points.png)",
        "",
        "![average](fr_average.png)",
        "",
        "![rt60](rt60.png)",
        "",
    ]
    return "\n".join(lines)


def proposal_md(proposal: Proposal, fmt: ValueFormats) -> str:
    """The proposal section appended to the session report."""
    lines = [
        "",
        "## Предложение EQ Main LR",
        "",
        f"Цель: {target_description(proposal.target)}. Ниже {proposal.f_low_hz:.0f}·1.25 Гц "
        "не корректируем.",
        "",
        "| полоса | действие | было | станет | обоснование |",
        "|---|---|---|---|---|",
    ]
    for entry in proposal.bands:
        flag = " **(ориентировочно)**" if entry.approximate else ""
        lines.append(
            f"| {entry.band.band} | {entry.action} | `{fmt.band(entry.was)}` | "
            f"`{fmt.band(entry.band)}` | {reason_text(entry.reason, fmt)}{flag} |"
        )
    pr = proposal.prediction
    lines += [
        "",
        f"Прогноз (модель, {pr.range_hz[0]:.0f}–{pr.range_hz[1]:.0f} Гц): RMS отклонения от "
        f"цели {pr.rms_dev_before_db} → {pr.rms_dev_after_db} дБ, максимум "
        f"{pr.max_dev_before_db} → {pr.max_dev_after_db} дБ.",
        "",
    ]
    if proposal.skipped:
        lines += ["Сознательно НЕ исправляем:", ""]
        lines += [
            f"- {s.kind} {s.freq_hz:.0f} Гц ({s.dev_db:+.1f} дБ, {s.width_oct} окт): "
            f"{reason_text(s.reason, fmt)}"
            for s in proposal.skipped
        ]
    if proposal.warnings:
        lines += ["", "**Предупреждения:**", ""]
        lines += [f"- {design_warning_text(w)}" for w in proposal.warnings]
    lines += ["", "![proposal](proposal.png)", ""]
    return "\n".join(lines)


def alignment_summary(result: AlignmentResult) -> str:
    """The text summary of a sub / top alignment analysis."""
    lines = [f"Кроссовер {result.fc_hz:.0f} Гц, точек: {len(result.points)}"]
    lines += [
        f"  {name}: минимум суммы в зоне стыка сейчас {p.min_now_db:+.1f} дБ -> после "
        f"{p.min_best_db:+.1f} дБ; проверка блока «вместе» vs расчёт: {p.check_mismatch_db:.1f} дБ"
        for name, p in result.points.items()
    ]
    who = "саба" if result.delay_ms > 0 else "сателлитов"
    polarity = "НЕ менять" if result.polarity == 1 else "ИНВЕРТИРОВАТЬ"
    lines.append(
        f"Рекомендация: задержка {who} {abs(result.delay_ms):.1f} мс, полярность саба "
        f"{polarity}; оценка {result.score_now_db:+.1f} -> {result.score_best_db:+.1f} дБ"
    )
    lines.append(
        f"Уровень на {result.fc_hz:.0f} Гц: саб − сателлиты = "
        f"{result.sub_minus_tops_at_fc_db:+.1f} дБ (при LR24 должно быть ~0)"
    )
    return "\n".join(lines)


def export_for_chat(  # noqa: PLR0913 - the export gathers everything about a session
    session: str,
    an: Analysis,
    rows: Mapping[str, PointRow],
    fmt: ValueFormats,
    *,
    generated: str,
    setup: str,
    proposal: Proposal | None = None,
    proposal_created: str = "",
    eq_now: SystemEq | None = None,
    verify: str | None = None,
) -> str:
    """A compact English text of a session for pasting into a chat or a forum.

    Args:
        session: Session name.
        an: Its analysis.
        rows: Quality numbers per point.
        fmt: Console value formats.
        generated: When the export was made, as text.
        setup: Microphone, sample rate and sweep in one line.
        proposal: The proposal made for this session, if any.
        proposal_created: When that proposal was made.
        eq_now: EQ on the console right now, if it could be read.
        verify: Text of the latest verify summary, if any.
    """
    names = ", ".join(an.norm_db)
    lines = [
        f"# X32 church room measurement — export for chat ({generated})",
        f"session={session} points={len(an.norm_db)} ({names}) {setup} "
        "window=500ms smoothing=1/6oct norm=500-2k=0dB avg=energy",
        f"f_low≈{an.f_low_hz:.0f}Hz (speaker/HPF roll-off; no correction below)",
        "",
        "## Quality per point (peak dBFS / SNR dB / IR PNR dB / verdict)",
    ]
    lines += [
        f"{name}: {row.peak_dbfs} / {row.snr_db} / {row.pnr_db} / {row.verdict}"
        for name, row in rows.items()
    ]
    lines += ["", "## Averaged response, 1/6 oct (Hz: avg dB, spread σ dB)"]
    cells = [
        f"{fc:.0f}:{_at(fc, an.avg_db):+.1f}/{_at(fc, an.spread_db):.1f}"
        for fc in sixth_octave_centres(31.5, 16000)
    ]
    lines += ["  ".join(cells[i : i + CHAT_ROW]) for i in range(0, len(cells), CHAT_ROW)]
    lines += ["", "## RT60 (octave: T20 / T30 / EDT s)"]
    lines.append(
        "  ".join(
            f"{c}:{_value(an.rt60[c].t20.mean_s)}/{_value(an.rt60[c].t30.mean_s)}/"
            f"{_value(an.rt60[c].edt.mean_s)}"
            for c in OCTAVES_HZ
        )
    )
    if proposal is not None:
        lines += ["", "## EQ at measurement time"]
        lines += [f"  {b.band}: {fmt.band(b)}" for b in proposal.eq_at_measurement.bands]
    if eq_now is not None:
        lines += ["", f"## Current Main LR EQ (read via OSC now): {'ON' if eq_now.on else 'OFF'}"]
        lines += [f"  {b.band}: {fmt.band(b)}" for b in eq_now.bands]
    if proposal is not None:
        lines += [
            "",
            f"## Proposal ({proposal_created}, target: {target_description(proposal.target)})",
        ]
        lines += [
            f"  band {e.band.band} {e.action}: {fmt.band(e.was)}  ->  {fmt.band(e.band)}"
            f"{'  [APPROX]' if e.approximate else ''}"
            for e in proposal.bands
            if e.action is not Action.KEEP
        ]
        pr = proposal.prediction
        lines.append(f"  predicted RMS dev {pr.rms_dev_before_db} -> {pr.rms_dev_after_db} dB")
        lines += [
            f"  skipped {s.kind} {s.freq_hz:.0f}Hz {s.dev_db:+.1f}dB: {reason_text(s.reason, fmt)}"
            for s in proposal.skipped
        ]
    if verify:
        lines += ["", "## Verify (after vs before, same points)", verify]
    lines += [
        "",
        "Question for reviewer: is this EQ reasonable for a church PA (speech + worship)? "
        "Anything to cut more/less, or to leave to acoustics?",
    ]
    return "\n".join(lines) + "\n"
