"""The session report (SPEC S14, §10): gather the facts of a session and write the report files.

Everything comes from the session's own files (points, junction, packages, undo, verify), so the
report can be made again later from the same directory (``ctune report``). Writes and places are
described with the wizard's own words, the ones the person saw on the screens.
"""

import json
import logging
import math
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from churchtune import __version__
from churchtune.analysis.verdict import verify
from churchtune.design.alignment import JunctionPoint, analyze_alignment
from churchtune.design.crossover import DEFAULT_FC_HZ
from churchtune.measure.config import SweepConfig
from churchtune.report.chat import for_chat
from churchtune.report.html import render_html
from churchtune.report.model import (
    AuditRow,
    Cell,
    ChangeRow,
    Msg,
    PointQuality,
    ReportData,
    Translate,
)
from churchtune.report.sections import build_document
from churchtune.report.session_json import report_json
from churchtune.report.session_md import figures_of, render_markdown
from churchtune.storage.files import (
    JUNCTION_DIR,
    load_junction_points,
    read_packages,
    read_plan,
    session_levels,
    write_report_files,
)
from churchtune.storage.models import PackagesFile, WizardStateFile
from churchtune.storage.paths import Workspace
from churchtune.wizard.applying import chosen_packages
from churchtune.wizard.labels import short_path
from churchtune.wizard.proposals import plan_from_file
from churchtune.wizard.protocol import Text
from churchtune.wizard.services import FS_HZ, PlannedWrite
from churchtune.wizard.steps.apply import advice_lines, value_text, write_text
from churchtune.wizard.steps.finish import analysis_of, target_of
from churchtune.wizard.steps.measure import place_text
from churchtune.wizard.steps.verify import VERIFY_DIR

logger = logging.getLogger(__name__)
REPORT_DIR = "report"
HTML_FILE, MD_FILE, JSON_FILE, CHAT_FILE = "report.html", "report.md", "report.json", "for-chat.txt"
BANDS_LAST = 99
Describe = Callable[[Path], tuple[PlannedWrite, ...]]


class ReportFiles:
    """Where the report was written."""

    def __init__(self, directory: Path) -> None:
        """The files of a report in ``directory``."""
        self.html = directory / HTML_FILE
        self.markdown = directory / MD_FILE
        self.data = directory / JSON_FILE
        self.chat = directory / CHAT_FILE


def as_msg(text: Text | str) -> Cell:
    """A wizard text as a report text (the same keys and arguments; lists joined)."""
    if isinstance(text, str):
        return text
    args: dict[str, str | int | float | Msg] = {}
    for name, value in text.args.items():
        if isinstance(value, Text):
            args[name] = as_msg(value)
        elif isinstance(value, tuple):
            args[name] = ", ".join(v if isinstance(v, str) else v.key for v in value)
        else:
            args[name] = value
    return Msg(text.key, args)


def _date(created: str) -> Cell:
    """The session's time in words of the report's language ("14 October 2026, 10:00")."""
    try:
        when = datetime.fromisoformat(created)
    except ValueError:
        return created
    return Msg(
        "report-date",
        {
            "day": when.day,
            "month": Msg(f"report-month-{when.month}"),
            "year": str(when.year),  # a number would get a thousands separator
            "time": when.strftime("%H:%M"),
        },
    )


def _changes(
    root: Path, state: WizardStateFile, describe: Describe | None
) -> tuple[ChangeRow, ...]:
    if state.applied_state != "applied" or state.applied_undo is None or describe is None:
        return ()
    try:
        writes = describe(root / state.applied_undo)
    except (ValueError, OSError) as error:
        logger.warning("the undo file of the report could not be read: %s", error)
        return ()
    ordered = sorted(writes, key=_write_order)
    return tuple(
        ChangeRow(
            w.path,
            as_msg(write_text(w)),
            as_msg(value_text(w.param, w.before_value)),
            as_msg(value_text(w.param, w.after_value)),
            w.before_value,
            w.after_value,
        )
        for w in ordered
    )


PARAM_ORDER = ("type", "freq", "q", "gain", "eq_on", "on", "fader")


def _write_order(write: PlannedWrite) -> tuple[int, int, int, int]:
    """The order of the change table: Main LR first, then the matrices by number.

    Bands in order, their settings as on the console (type, frequency, width, gain); faders and
    switches last.
    """
    main = 0 if write.strip.kind.value.startswith("main") else 1
    param = PARAM_ORDER.index(write.param) if write.param in PARAM_ORDER else len(PARAM_ORDER)
    band = write.band if write.band is not None else BANDS_LAST
    return main, write.strip.number or 0, band, param


def _packages(root: Path, state: WizardStateFile) -> PackagesFile | None:
    if state.proposal_file is None:
        return None
    try:
        return read_packages(root / state.proposal_file)
    except (ValueError, OSError) as error:
        logger.warning("the proposal of the report could not be read: %s", error)
        return None


def _places(
    workspace: Workspace, state: WizardStateFile, names: tuple[str, ...]
) -> dict[str, Cell]:
    try:
        stored = read_plan(workspace, state.venue) if state.venue else None
    except (ValueError, OSError) as error:
        logger.warning("the point plan of the report could not be read: %s", error)
        return {}
    if stored is None:  # the places are then named by their point names alone
        return {}
    plan = plan_from_file(stored)
    places: dict[str, Cell] = {}
    for name in names:
        try:
            places[name] = as_msg(place_text(plan, name))
        except (KeyError, ValueError):
            continue
    return places


def _advice(packages: PackagesFile | None, state: WizardStateFile) -> tuple[Msg, ...]:
    if packages is None or state.applied_state not in ("applied", "nothing"):
        return ()
    lines = (as_msg(t) for t in advice_lines(chosen_packages(packages)))
    return tuple(m for m in lines if isinstance(m, Msg))


def report_data(
    state: WizardStateFile,
    session_dir: Path,
    workspace: Workspace,
    *,
    sweep: SweepConfig | None = None,
    describe: Describe | None = None,
) -> ReportData:
    """Everything the report tells about a session, read from its files.

    Args:
        state: The session's wizard state.
        session_dir: The session directory.
        workspace: The workspace (undo, proposal and the point plan are found through it).
        sweep: The measurement sweep of the run, when known (``ctune report`` does not know it).
        describe: Describes an undo file's writes in words (the console's write port).
    """
    root = workspace.root
    cal = state.calibration_set
    analysis = analysis_of(session_dir, cal, session_dir)
    points = tuple(
        PointQuality(
            name,
            meta.verdict,
            meta.snr_db,
            meta.pnr_db,
            meta.peak_dbfs,
            meta.repeat_db,
            tuple(w.code for w in meta.warnings),
        )
        for name, meta in sorted(session_levels(session_dir).items())
    )
    packages = _packages(root, state)
    audit = tuple(
        AuditRow(a.code, a.channels, a.freq_hz, a.gain_db)
        for a in (packages.audit if packages else ())
    )
    fc_hz = packages.fc_hz if packages else DEFAULT_FC_HZ
    measured = load_junction_points(session_dir / JUNCTION_DIR)
    alignment = None
    if measured:
        junction = {name: JunctionPoint(p.sub, p.tops, p.both) for name, p in measured.items()}
        alignment = analyze_alignment(junction, fc_hz, FS_HZ)
    result = after = None
    if state.verify_verdict is not None and state.verify_points:
        before = analysis_of(session_dir, cal, session_dir, state.verify_points)
        after = analysis_of(session_dir, cal, session_dir / VERIFY_DIR, state.verify_points)
        if before is not None and after is not None:
            result = verify(before, after, target_of(before))
    measurement: dict[str, str | float | int] = {"fs_hz": int(FS_HZ)}
    if sweep is not None:
        measurement |= {"sweep_s": sweep.duration_s, "f1_hz": sweep.f1_hz, "f2_hz": sweep.f2_hz}
    if state.sweep_level_dbfs is not None:
        measurement["level_dbfs"] = state.sweep_level_dbfs
    names = tuple(sorted({p.name for p in points} | set(state.verify_points)))
    return ReportData(
        venue_name=state.venue_name,
        created=state.created,
        created_text=_date(state.created),
        tool_version=__version__,
        console_model=state.console_model,
        roles=dict(state.roles),
        variant=state.variant,
        analysis=analysis,
        target_db=target_of(analysis) if analysis is not None else None,
        points=points,
        alignment=alignment,
        applied_state=state.applied_state,
        changes=_changes(root, state, describe),
        undo_file=short_path(root / state.applied_undo) if state.applied_undo else None,
        verify=result,
        verify_analysis=after,
        audit=audit,
        sweep=measurement,
        calibration=cal,
        mic_count=state.mic_count,
        places=_places(workspace, state, names),
        advice=_advice(packages, state),
    )


def _finite(value: Any) -> Any:  # noqa: ANN401 - any JSON value
    """JSON values with NaN and infinities as null (strict JSON has no such numbers)."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Mapping):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_finite(v) for v in value]
    return value


def write_report(data: ReportData, directory: Path, tr: Translate, lang: str) -> ReportFiles:
    """Write the HTML, Markdown (with its PNG charts), JSON and chat text of a report.

    Charts of an earlier report that this one does not have are removed, so the Markdown never
    shows a stale picture.
    """
    document = build_document(data, tr)
    files: dict[str, str | bytes] = {
        HTML_FILE: render_html(document, tr, lang),
        MD_FILE: render_markdown(document, tr),
        JSON_FILE: json.dumps(
            _finite(report_json(data)), ensure_ascii=False, indent=2, allow_nan=False
        )
        + "\n",
        CHAT_FILE: for_chat(data, tr),
    }
    files.update({figure.name: figure.png for figure in figures_of(document)})
    if directory.is_dir():
        for old in directory.glob("*.png"):
            if old.name not in files:
                old.unlink()
    write_report_files(directory, files)
    return ReportFiles(directory)
