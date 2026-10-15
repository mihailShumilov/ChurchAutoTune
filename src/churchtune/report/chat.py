"""A compact text of the session to paste into a chat or a forum (SPEC §10, ``--for-chat``).

Plain text in the report's language, 4-6 KB: the facts a helper needs to give advice, without
pictures. Responses are given in thirds of an octave from 63 Hz to 8 kHz.
"""

from churchtune.report.findings import findings
from churchtune.report.html import render_msg
from churchtune.report.model import Cell, Msg, ReportData, Translate
from churchtune.report.sections import kebab, output_cell
from churchtune.report.session_json import THIRDS_HZ, thirds_db

CHAT_THIRDS = tuple(f for f in THIRDS_HZ if 63 <= f <= 8000)  # noqa: PLR2004 - the shown range
MAX_CHAT_POINTS = 8


def _curve(values: list[float | None]) -> str:
    shown = [v for f, v in zip(THIRDS_HZ, values, strict=True) if f in CHAT_THIRDS]
    return " ".join("—" if v is None else f"{v:+.1f}" for v in shown)


def for_chat(data: ReportData, tr: Translate) -> str:
    """The session in a few kilobytes of text."""
    lines = [
        tr("report-chat-intro", {"venue": data.venue_name, "created": data.created}),
        tr("report-chat-console", {"model": data.console_model or "—", "mics": data.mic_count}),
    ]
    speakers = [(k, r) for k, r in sorted(data.roles.items()) if r != "unused"]
    if speakers:
        roles = ", ".join(
            f"{_cell(output_cell(k), tr)} = {tr(f'role-{kebab(r)}', None)}" for k, r in speakers
        )
        lines.append(tr("report-chat-roles", {"roles": roles}))
    an = data.analysis
    if an is not None:
        lines.append(tr("report-chat-findings", None))
        lines += [
            "- " + render_msg(m, tr)
            for m in findings(an, data.target_db)
            if m.key != "glossary-rt60"
        ]
        lines.append(tr("report-chat-thirds", {"thirds": " ".join(_label(f) for f in CHAT_THIRDS)}))
        lines.append(tr("report-chat-average", {"curve": _curve(thirds_db(an.grid_hz, an.avg_db))}))
        lines.append(
            tr("report-chat-spread", {"curve": _curve(thirds_db(an.grid_hz, an.spread_db))})
        )
        for name, curve in list(an.norm_db.items())[:MAX_CHAT_POINTS]:
            lines.append(f"  {name}: {_curve(thirds_db(an.grid_hz, curve))}")
        decay = [
            f"{c}:{an.rt60[c].t20.mean_s:.2f}"
            for c in sorted(an.rt60)
            if an.rt60[c].t20.mean_s is not None
        ]
        if decay:
            lines.append(tr("report-chat-decay", {"values": " ".join(decay)}))
    if data.alignment is not None:
        a = data.alignment
        lines.append(
            tr(
                "report-chat-junction",
                {
                    "fc_hz": round(a.fc_hz),
                    "sub_minus_tops_db": round(a.sub_minus_tops_at_fc_db, 1),
                    "delay_ms": round(a.delay_ms, 1),
                    "polarity": tr(
                        "report-polarity-inverted" if a.polarity < 0 else "report-polarity-normal",
                        None,
                    ),
                },
            )
        )
    if data.changes:
        lines.append(tr("report-chat-changes", None))
        lines += [
            f"  {_cell(c.what, tr)}: {_cell(c.before, tr)} -> {_cell(c.after, tr)}"
            for c in data.changes
        ]
    if data.verify is not None:
        lines.append(
            tr(
                "report-chat-verify",
                {
                    "verdict": tr(f"report-chat-verdict-{kebab(data.verify.verdict.value)}", None),
                    "before": round(data.verify.m_before_db, 1),
                    "after": round(data.verify.m_after_db, 1),
                },
            )
        )
    lines.append(tr("report-chat-question", None))
    return "\n".join(lines) + "\n"


def _cell(cell: Cell, tr: Translate) -> str:
    return render_msg(cell, tr) if isinstance(cell, Msg) else cell


def _label(freq_hz: float) -> str:
    return f"{freq_hz / 1000:g}k" if freq_hz >= 1000 else f"{freq_hz:g}"  # noqa: PLR2004 - kHz
