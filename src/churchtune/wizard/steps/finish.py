"""Steps S11 and S14 (SPEC §7): what the measurement says, and what to do next.

S11 explains the analysis in plain words and offers the change packages; S14 is the checklist
for the person, which depends on whether something was written, put back, or nothing at all.
"""

import logging
import shlex
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from churchtune.analysis.points import Analysis, PointResponse, analyze_points
from churchtune.analysis.response import GRID_HZ
from churchtune.design.system_eq import DEFAULT_TARGET
from churchtune.design.target import target_curve
from churchtune.domain.arrays import FloatArray
from churchtune.report.findings import findings as report_findings
from churchtune.report.model import Msg
from churchtune.storage.files import load_points, read_session_mic_calibration, read_undo
from churchtune.storage.models import MicCalibrationFile, WizardStateFile
from churchtune.wizard.labels import short_path, undo_command
from churchtune.wizard.protocol import Text
from churchtune.wizard.services import WizardContext
from churchtune.wizard.state import Outcome
from churchtune.wizard.steps.apply import restore_pending_mutes
from churchtune.wizard.steps.proposal import propose
from churchtune.wizard.steps.setup import measuring
from churchtune.wizard.ui import info, t, task

logger = logging.getLogger(__name__)


def _point_corrections(
    calibration: MicCalibrationFile | None, mics: dict[str, int | None]
) -> dict[str, FloatArray]:
    """Correction curves per point; a rejected microphone gets none (its points stay raw)."""
    if calibration is None:
        return {}
    curves = {}
    by_input = {m.usb_input: m for m in calibration.mics if not m.problems}
    for point, usb in mics.items():
        mic = by_input.get(usb) if usb is not None else None
        if mic is None:
            continue
        shape = np.interp(np.log(GRID_HZ), np.log(calibration.centres_hz), mic.shape_db, 0.0, 0.0)
        curves[point] = np.asarray(shape + mic.sensitivity_db, dtype=np.float64)
    return curves


TARGET = DEFAULT_TARGET  # the proposals aim at the same curve


def target_of(analysis: Analysis) -> FloatArray:
    """The house curve the proposals aimed at, on the analysis grid."""
    return target_curve(
        analysis.grid_hz,
        TARGET.tilt_db_per_oct,
        TARGET.tilt_from_hz,
        TARGET.bass_lift_db,
        TARGET.bass_from_hz,
    )


def findings(analysis: Analysis) -> list[Text]:
    """The most important things the analysis says, in plain words, most important first."""
    return [
        Text(m.key, {k: v for k, v in m.args.items() if not isinstance(v, Msg)})
        for m in report_findings(analysis, target_of(analysis))
    ]


def session_analysis(
    ctx: WizardContext, directory: Path, names: Collection[str] | None = None
) -> Analysis | None:
    """The analysis of the points stored in a directory of this session (``analysis_of``)."""
    return analysis_of(ctx.session_dir, ctx.box.state.calibration_set, directory, names)


def analysis_of(
    session_dir: Path,
    calibration_set: str | None,
    directory: Path,
    names: Collection[str] | None = None,
) -> Analysis | None:
    """The analysis of the points stored in a directory (only ``names``, when given).

    Each point is corrected by the microphone that measured it, from the session's own copy of
    the calibration, whatever its age: the points were measured with it.

    Returns:
        None when there is no such point.
    """
    stored = {
        name: point
        for name, point in load_points(directory).items()
        if names is None or name in names
    }
    if not stored:
        return None
    calibration = None
    if calibration_set is not None:
        calibration = read_session_mic_calibration(session_dir)
    mics = {name: point.meta.mic_input if point.meta else None for name, point in stored.items()}
    return analyze_points(
        {name: PointResponse(p.ir, p.fs_hz) for name, p in stored.items()},
        point_cal_db=_point_corrections(calibration, mics),
    )


@dataclass(frozen=True, slots=True)
class Results:
    """S11: the analysis in plain words, then the change packages to choose from."""

    id: str = "results"
    title: str = "step-results-title"
    changes_console: bool = False

    def applies(self, state: WizardStateFile) -> bool:
        """Every goal with measured points."""
        return measuring(state)

    def run(self, ctx: WizardContext) -> Outcome:
        """Analyse the stored points of this session and explain the findings."""
        analysis = session_analysis(ctx, ctx.session_dir)
        if analysis is None:
            info(ctx.prompter, t("step-results-no-points"))
            return Outcome.BACK
        count = len(analysis.point_names)
        info(ctx.prompter, t("step-results-what", count=count), *findings(analysis))
        propose(ctx, analysis)
        info(ctx.prompter, t("step-results-next-apply"))
        return Outcome.DONE


@dataclass(frozen=True, slots=True)
class Finish:
    """S14: what to do now (save the scene, listen at the next service)."""

    id: str = "finish"
    title: str = "step-finish-title"
    changes_console: bool = False

    def applies(self, state: WizardStateFile) -> bool:  # noqa: ARG002 - always
        """Always the last step."""
        return True

    def run(self, ctx: WizardContext) -> Outcome:
        """The checklist for the person; never while temporary mutes are still pending."""
        if not restore_pending_mutes(ctx):
            info(ctx.prompter, t("mutes-pending-cannot-finish"))
            return Outcome.QUIT
        state = ctx.box.state
        # Mutes and sends back first, then the scene: otherwise it is saved with them muted.
        lines = [t("step-finish-what"), t("step-finish-undo-by-hand"),
                 t("step-finish-mics", count=len(state.mic_inputs))]  # fmt: skip
        if state.applied_state == "applied" and state.applied_undo is not None:
            undo = ctx.services.workspace.root / state.applied_undo
            try:
                console = read_undo(undo).ip
            except (ValueError, OSError):
                console = None
            lines += [
                t("step-finish-scene"),
                t("step-finish-listen"),
                t(
                    "step-finish-undo-later",
                    command=undo_command(ctx.services.workspace.root, undo, console),
                ),
            ]
        elif state.applied_state == "reverted":
            lines += [t("step-finish-reverted"), t("step-finish-listen")]
        else:
            lines.append(t("step-finish-listen"))
        task(ctx.prompter, *lines)
        saved = t("step-finish-saved", path=str(ctx.session_dir))
        info(ctx.prompter, *_write_report(ctx), saved)
        return Outcome.DONE


def _write_report(ctx: WizardContext) -> list[Text]:
    """S14: write the report (SPEC §10); what to tell the person about it.

    The report is not worth losing the end of the wizard over: any failure while making it is
    logged and the person is told to make it again with ``ctune report``.
    """
    services = ctx.services
    if services.translate is None:
        return []
    # The report reads what the steps above stored; imported here, it does not import back.
    from churchtune.wizard.reporting import REPORT_DIR, report_data, write_report  # noqa: PLC0415

    state = ctx.box.state
    describe = services.writes.describe_undo if services.writes is not None else None
    try:
        data = report_data(
            state, ctx.session_dir, services.workspace, sweep=services.sweep, describe=describe
        )
        files = write_report(
            data, ctx.session_dir / REPORT_DIR, services.translate, services.language
        )
    except Exception:
        logger.exception("the report could not be written")
        return [t("step-finish-report-failed")]
    command = f"open {shlex.quote(str(files.html))}"
    return [t("step-finish-report", path=short_path(files.html), command=command)]
