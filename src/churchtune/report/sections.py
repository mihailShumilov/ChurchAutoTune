"""The session report's sections (SPEC §10) from ``ReportData``.

Every text is a message key; numbers go through value messages (``report-value-*``), so the
renderer's language also decides the decimal separator. Charts are drawn here, labelled with the
same translate function the document will be rendered with. Places are named as the wizard names
them, and nothing claims more than was measured.
"""

from churchtune.analysis.bands import OCTAVES_HZ
from churchtune.analysis.points import Analysis
from churchtune.analysis.verdict import VerifyResult
from churchtune.design.alignment import AlignmentResult
from churchtune.report import figures
from churchtune.report.findings import (
    BASS_LONG_RATIO,
    EDT_LONG_RATIO,
    EDT_SHORT_RATIO,
    LARGE_SPREAD_DB,
    bass_ratio,
    edt_ratio,
    findings,
    mid_rt60_s,
    mid_spread_db,
    rt60_verdict,
)
from churchtune.report.model import (
    Block,
    Bullets,
    Cell,
    Document,
    Figure,
    Msg,
    Paragraph,
    ReportData,
    Section,
    Table,
    Translate,
)

# Section ids, in order (anchors in HTML, file names in Markdown, structure tests).
SECTION_IDS = (
    "summary",
    "system",
    "quality",
    "response",
    "junction",
    "decay",
    "changes",
    "verify",
    "audit",
    "acoustics",
    "checklist",
    "appendix",
)
# Octaves whose decay a measurement cannot pin down well (modes, short octave filters).
APPROXIMATE_OCTAVES = frozenset({63})
SAME_LEVEL_DB = 1.0
KILOHERTZ = 1000.0
NO_CHANGE_DB = 0.05


def kebab(value: str) -> str:
    """A stored value (``top_l``) as the tail of a message key (``top-l``)."""
    return value.replace("_", "-")


def output_cell(key: str) -> Cell:
    """A roles key (``"main 8"``) as the console's word for that output."""
    kind, _, number = key.partition(" ")
    return Msg(f"output-{kind}", {"number": int(number)}) if number.isdecimal() else key


def output_order(key: str) -> tuple[str, int]:
    """Outputs by kind, then by number (output 3 before output 10)."""
    kind, _, number = key.partition(" ")
    return kind, int(number) if number.isdecimal() else 0


def _db(value: float) -> Msg:
    return Msg("report-value-db", {"value": round(value, 1) + 0.0})


def _s(value: float | None, approximate: bool = False) -> Cell:
    if value is None:
        return "—"
    key = "report-value-s-approx" if approximate else "report-value-s"
    return Msg(key, {"value": round(value, 2)})


def _hz(value: float) -> Msg:
    return Msg("report-value-hz", {"value": round(value)})


def _freq(value: float) -> Msg:
    """A frequency as people say it: "125 Hz", "2 kHz", "3.15 kHz"."""
    if value >= KILOHERTZ:
        return Msg("report-value-khz", {"value": round(value / KILOHERTZ, 2)})
    return _hz(value)


def _place(data: ReportData, name: str) -> Cell:
    """A point as the wizard names it ("p3 — front rows, right, …"), or its name alone."""
    where = data.places.get(name)
    return Msg("report-place", {"name": name, "where": where}) if where is not None else name


def _summary(data: ReportData) -> Section:
    blocks: list[Block] = []
    if data.verify is not None and data.applied_state == "reverted":
        verdict = kebab(data.verify.verdict.value)
        blocks.append(Paragraph(Msg(f"report-verify-result-{verdict}")))
        blocks.append(Paragraph(Msg("report-summary-reverted-after-check")))
    elif data.verify is not None:
        blocks.append(Paragraph(Msg(f"report-summary-verdict-{kebab(data.verify.verdict.value)}")))
    elif data.applied_state is not None:
        blocks.append(Paragraph(Msg(f"report-summary-applied-{data.applied_state}")))
    else:
        blocks.append(Paragraph(Msg("report-summary-measured-only")))
    if data.applied_state == "applied":
        blocks.append(Paragraph(Msg("report-summary-next")))
    if data.analysis is not None:
        blocks.append(Paragraph(Msg("report-summary-findings-before")))
        blocks.append(Bullets(tuple(findings(data.analysis, data.target_db))))
    return Section("summary", Msg("report-section-summary"), tuple(blocks))


def _system(data: ReportData) -> Section:
    rows: list[tuple[Cell, ...]] = [
        (Msg("report-system-console"), data.console_model or "—"),
        (Msg("report-system-mics"), str(data.mic_count)),
    ]
    if data.variant is not None:
        rows.append((Msg("report-system-variant"), Msg(f"variant-{kebab(data.variant)}")))
    speakers = sorted((k for k, r in data.roles.items() if r != "unused"), key=output_order)
    rows += [(output_cell(key), Msg(f"role-{kebab(data.roles[key])}")) for key in speakers]
    head = (Msg("report-column-what"), Msg("report-column-value"))
    return Section("system", Msg("report-section-system"), (Table(head, tuple(rows)),))


def _quality(data: ReportData) -> Section:
    if not data.points:
        return Section("quality", Msg("report-section-quality"), (Paragraph(Msg("report-none")),))
    head = (
        Msg("report-column-point"),
        Msg("report-column-verdict"),
        Msg("report-column-snr"),
        Msg("report-column-repeat"),
        Msg("report-column-why"),
    )
    rows = tuple(
        (
            _place(data, p.name),
            Msg(f"report-quality-{p.verdict}"),
            _db(p.snr_db),
            _db(p.repeat_db) if p.repeat_db is not None else "—",
            Msg(f"gate-{p.warnings[0]}") if p.warnings else "",
        )
        for p in data.points
    )
    blocks: list[Block] = [Paragraph(Msg("report-quality-what")), Table(head, rows)]
    if data.calibration is None and data.mic_count > 1:
        blocks.append(Paragraph(Msg("report-quality-calibration-none")))
    return Section("quality", Msg("report-section-quality"), tuple(blocks))


def _response(data: ReportData, tr: Translate) -> Section:
    an = data.analysis
    if an is None:
        return Section("response", Msg("report-section-response"), (Paragraph(Msg("report-none")),))
    blocks: list[Block] = [
        Paragraph(Msg("report-response-what")),
        Figure("response-points.png", figures.points_png(an, tr), Msg("report-figure-points")),
    ]
    after = data.verify_analysis
    caption = Msg("report-figure-before-after") if after else Msg("report-figure-average")
    png = figures.average_png(an, data.target_db, tr, after)
    blocks.append(Figure("response-average.png", png, caption))
    return Section("response", Msg("report-section-response"), tuple(blocks))


def _junction(alignment: AlignmentResult | None, tr: Translate) -> Section:
    title = Msg("report-section-junction")
    if alignment is None:
        return Section("junction", title, (Paragraph(Msg("report-junction-not-measured")),))
    level = alignment.sub_minus_tops_at_fc_db
    if abs(level) < SAME_LEVEL_DB:
        level_text = Msg("report-junction-level-same")
    else:
        key = "report-junction-level-louder" if level > 0 else "report-junction-level-quieter"
        level_text = Msg(key, {"db": round(abs(level), 1)})
    delay = round(abs(alignment.delay_ms), 1)
    if delay < NO_CHANGE_DB:
        delay_text = Msg("report-junction-delay-none")
    else:
        key = (
            "report-junction-delay-sub" if alignment.delay_ms > 0 else "report-junction-delay-tops"
        )
        delay_text = Msg(key, {"ms": delay})
    polarity = (
        Msg("report-polarity-inverted") if alignment.polarity < 0 else Msg("report-polarity-normal")
    )
    head = (Msg("report-column-what"), Msg("report-column-value"))
    rows: tuple[tuple[Cell, ...], ...] = (
        (Msg("report-junction-fc"), _hz(alignment.fc_hz)),
        (Msg("report-junction-level"), level_text),
        (Msg("report-junction-delay"), delay_text),
        (Msg("report-junction-polarity"), polarity),
    )
    figure = Figure(
        "junction.png", figures.junction_png(alignment, tr), Msg("report-figure-junction")
    )
    blocks: tuple[Block, ...] = (
        Paragraph(Msg("report-junction-what")),
        Paragraph(Msg("report-junction-gloss")),
        Table(head, rows),
        Paragraph(Msg("report-junction-advice")),
        figure,
    )
    return Section("junction", title, blocks)


def _decay_notes(an: Analysis) -> list[Msg]:
    notes: list[Msg] = []
    rt60 = mid_rt60_s(an)
    if rt60 is not None:
        verdict = rt60_verdict(round(rt60, 2))
        notes += [
            Msg(f"report-decay-speech-{verdict}", {"rt60_s": round(rt60, 2)}),
            Msg(f"report-decay-music-{verdict}"),
        ]
    ratio = edt_ratio(an)
    if ratio is not None and rt60 is not None:
        edt_s = round(ratio * rt60, 2)
        kind = (
            "short" if ratio < EDT_SHORT_RATIO else "long" if ratio > EDT_LONG_RATIO else "normal"
        )
        notes.append(Msg(f"report-decay-edt-{kind}", {"edt_s": edt_s}))
    bass = bass_ratio(an)
    if bass is not None:
        key = "report-decay-bass-long" if bass > BASS_LONG_RATIO else "report-decay-bass-ok"
        notes.append(Msg(key, {"ratio": round(bass, 2)}))
    return notes


def _decay(an: Analysis | None, tr: Translate) -> Section:
    title = Msg("report-section-decay")
    if an is None or not an.rt60:
        return Section("decay", title, (Paragraph(Msg("report-none")),))
    head = (Msg("report-column-octave"), Msg("report-column-t20"), Msg("report-column-edt"))
    rows = tuple(
        (
            _freq(c),
            _s(an.rt60[c].t20.mean_s, c in APPROXIMATE_OCTAVES),
            _s(an.rt60[c].edt.mean_s, c in APPROXIMATE_OCTAVES),
        )
        for c in OCTAVES_HZ
        if c in an.rt60
    )
    blocks: list[Block] = [Paragraph(Msg("report-decay-what")), Table(head, rows)]
    notes = _decay_notes(an)
    if notes:
        blocks.append(Bullets(tuple(notes)))
    blocks.append(Paragraph(Msg("report-decay-limits")))
    blocks.append(Figure("decay.png", figures.decay_png(an, tr), Msg("report-figure-decay")))
    return Section("decay", title, tuple(blocks))


def _changes(data: ReportData) -> Section:
    title = Msg("report-section-changes")
    blocks: list[Block] = []
    if data.applied_state != "applied" or not data.changes:
        blocks.append(Paragraph(Msg(f"report-changes-{data.applied_state or 'none'}")))
    else:
        head = (
            Msg("report-column-parameter"),
            Msg("report-column-before"),
            Msg("report-column-after"),
        )
        rows = tuple((c.what, c.before, c.after) for c in data.changes)
        blocks += [Paragraph(Msg("report-changes-what")), Table(head, rows)]
        if any(c.path.endswith("/type") for c in data.changes):
            blocks.append(Paragraph(Msg("report-changes-types")))
        if data.undo_file is not None:
            blocks.append(Paragraph(Msg("report-changes-undo")))
    if data.advice:
        blocks += [Paragraph(Msg("report-changes-advice")), Bullets(data.advice)]
    return Section("changes", title, tuple(blocks))


def _direction(delta_db: float) -> str:
    if abs(delta_db) < NO_CHANGE_DB:
        return "same"
    return "worse" if delta_db > 0 else "better"


def _verify(data: ReportData) -> Section:
    title = Msg("report-section-verify")
    result: VerifyResult | None = data.verify
    if result is None:
        return Section("verify", title, (Paragraph(Msg("report-verify-not-done")),))
    head = (Msg("report-column-what"), Msg("report-column-value"))
    worst = Msg(
        "report-verify-worst-value",
        {
            "place": _place(data, result.worst_point),
            "direction": _direction(result.worst_delta_db),
            "delta_db": round(abs(result.worst_delta_db), 1),
        },
    )
    band = Msg(
        "report-verify-band-value",
        {
            "delta_db": round(max(result.band_delta_db, 0.0), 1),
            "freq": _freq(result.band_hz),
            "place": _place(data, result.band_point),
        },
    )
    rows: tuple[tuple[Cell, ...], ...] = (
        (Msg("report-verify-m-before"), _db(result.m_before_db)),
        (Msg("report-verify-m-after"), _db(result.m_after_db)),
        (Msg("report-verify-worst"), worst),
        (Msg("report-verify-band"), band),
        (Msg("report-verify-spread"), _db(result.spread_delta_db)),
    )
    verdict = Paragraph(Msg(f"report-verify-result-{kebab(result.verdict.value)}"))
    blocks = (verdict, Paragraph(Msg("report-verify-what")), Table(head, rows))
    return Section("verify", title, blocks)


def _audit(data: ReportData) -> Section:
    title = Msg("report-section-audit")
    if not data.audit:
        return Section("audit", title, (Paragraph(Msg("report-audit-none")),))
    items = tuple(
        Msg(
            f"audit-{a.code}",
            {
                "channels": ", ".join(str(c) for c in a.channels),
                "count": len(a.channels),
                "freq_hz": a.freq_hz or 0,
                "gain_db": a.gain_db or 0.0,
            },
        )
        for a in data.audit
    )
    return Section("audit", title, (Paragraph(Msg("report-audit-what")), Bullets(items)))


def _acoustics(an: Analysis | None) -> Section:
    title = Msg("report-section-acoustics")
    items: list[Msg] = []
    dry = False
    if an is not None:
        rt60 = mid_rt60_s(an)
        if rt60 is not None:
            verdict = rt60_verdict(round(rt60, 2))
            dry = verdict == "dry"
            items.append(Msg(f"report-acoustics-{verdict}"))
        if mid_spread_db(an) > LARGE_SPREAD_DB:
            items.append(Msg("report-acoustics-spread"))
    if not dry:
        items.append(Msg("report-acoustics-reflections"))
    blocks = (Bullets(tuple(items)), Paragraph(Msg("report-acoustics-no-eq-promise")))
    return Section("acoustics", title, blocks)


def _checklist(data: ReportData) -> Section:
    applied = data.applied_state == "applied"
    items = [Msg("report-check-hand"), Msg("report-check-mics")]
    if applied:
        items.append(Msg("report-check-scene"))
    items.append(Msg("report-check-listen"))
    if applied and data.undo_file is not None:
        items.append(Msg("report-check-undo", {"command": f"ctune undo {data.undo_file}"}))
    if data.audit:
        items.append(Msg("report-check-channels"))
    blocks = (Bullets(tuple(items), checklist=True),)
    return Section("checklist", Msg("report-section-checklist"), blocks)


MEASUREMENT_VALUES = {
    "fs_hz": "report-value-hz",
    "sweep_s": "report-value-s",
    "f1_hz": "report-value-hz",
    "f2_hz": "report-value-hz",
    "level_dbfs": "report-value-dbfs",
}


def _measurement_value(name: str, value: str | float | int) -> Cell:
    if isinstance(value, str):
        return value
    return Msg(MEASUREMENT_VALUES[name], {"value": round(float(value), 1)})


def _appendix(data: ReportData) -> Section:
    rows: list[tuple[Cell, ...]] = [
        (Msg("report-appendix-version"), data.tool_version),
        (Msg("report-appendix-created"), data.created_text or data.created),
        (Msg("report-appendix-calibration"), data.calibration or Msg("report-appendix-none")),
    ]
    rows += [
        (Msg(f"report-appendix-{kebab(name)}"), _measurement_value(name, value))
        for name, value in data.sweep.items()
        if name in MEASUREMENT_VALUES
    ]
    head = (Msg("report-column-what"), Msg("report-column-value"))
    blocks: list[Block] = [Table(head, tuple(rows))]
    if data.points:
        details_head = (
            Msg("report-column-point"),
            Msg("report-column-pnr"),
            Msg("report-column-peak"),
        )
        details = tuple(
            (
                _place(data, p.name),
                _db(p.pnr_db),
                Msg("report-value-dbfs", {"value": round(p.peak_dbfs, 1)}),
            )
            for p in data.points
        )
        blocks += [Paragraph(Msg("report-appendix-details")), Table(details_head, details)]
    return Section("appendix", Msg("report-section-appendix"), tuple(blocks))


def build_document(data: ReportData, tr: Translate) -> Document:
    """All sections of SPEC §10, in order (``SECTION_IDS``)."""
    sections = (
        _summary(data),
        _system(data),
        _quality(data),
        _response(data, tr),
        _junction(data.alignment, tr),
        _decay(data.analysis, tr),
        _changes(data),
        _verify(data),
        _audit(data),
        _acoustics(data.analysis),
        _checklist(data),
        _appendix(data),
    )
    return Document(
        title=Msg("report-title", {"venue": data.venue_name}),
        subtitle=Msg("report-subtitle", {"created": data.created_text or data.created}),
        sections=sections,
    )
