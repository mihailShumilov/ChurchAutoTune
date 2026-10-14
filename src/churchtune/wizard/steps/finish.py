"""Steps S11 and S14 (SPEC §7): what the measurement says, and what to do next.

S11 explains the analysis in plain words. Change proposals (crossover, alignment, system EQ) and
applying them arrive with M4 and M5; until then the wizard says so honestly and keeps the data.
"""

from dataclasses import dataclass

import numpy as np

from churchtune.analysis.points import Analysis, PointResponse, analyze_points
from churchtune.analysis.response import GRID_HZ
from churchtune.domain.arrays import FloatArray
from churchtune.storage.files import load_points, read_session_mic_calibration
from churchtune.storage.models import MicCalibrationFile, WizardStateFile
from churchtune.wizard.protocol import Text
from churchtune.wizard.services import WizardContext
from churchtune.wizard.state import Outcome
from churchtune.wizard.steps.setup import measuring
from churchtune.wizard.ui import info, t, task

# SPEC room-measurement skill: RT60 500 Hz-1 kHz.
RT60_DRY_S, RT60_GOOD_S, RT60_BOOMY_S = 0.8, 1.4, 2.0
MID_OCTAVES = (500, 1000)
HUMP_MIN_DB = 3.0
HUMP_BAND_HZ = (80.0, 4000.0)
SPREAD_BAND_HZ = (100.0, 4000.0)
LARGE_SPREAD_DB = 3.0


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


def findings(analysis: Analysis) -> list[Text]:
    """The most important things the analysis says, in plain words, most important first."""
    found: list[Text] = []
    mids = [analysis.rt60[o].t20.mean_s for o in MID_OCTAVES if o in analysis.rt60]
    values = [v for v in mids if v is not None]
    if values:
        rt60 = float(np.mean(values))
        verdict = (
            "dry" if rt60 < RT60_DRY_S else "good" if rt60 < RT60_GOOD_S
            else "boomy" if rt60 < RT60_BOOMY_S else "treatment"
        )  # fmt: skip
        found.append(t(f"finding-rt60-{verdict}", rt60_s=round(rt60, 1)))
        found.append(t("glossary-rt60"))
    grid = analysis.grid_hz
    band = (grid >= max(HUMP_BAND_HZ[0], analysis.f_low_hz)) & (grid <= HUMP_BAND_HZ[1])
    if band.any():
        index = int(np.argmax(np.where(band, analysis.avg_db, -np.inf)))
        if analysis.avg_db[index] >= HUMP_MIN_DB:
            found.insert(0, t("finding-hump", freq_hz=round(float(grid[index])),
                              gain_db=round(float(analysis.avg_db[index]), 1)))  # fmt: skip
    spread_band = (grid >= SPREAD_BAND_HZ[0]) & (grid <= SPREAD_BAND_HZ[1])
    spread = float(np.mean(analysis.spread_db[spread_band]))
    key = "finding-spread-large" if spread > LARGE_SPREAD_DB else "finding-spread-small"
    found.append(t(key, spread_db=round(spread, 1)))
    return found


@dataclass(frozen=True, slots=True)
class Results:
    """S11: the analysis in plain words."""

    id: str = "results"
    title: str = "step-results-title"
    changes_console: bool = False

    def applies(self, state: WizardStateFile) -> bool:
        """Every goal with measured points."""
        return measuring(state)

    def run(self, ctx: WizardContext) -> Outcome:
        """Analyse the stored points of this session and explain the findings."""
        stored = load_points(ctx.session_dir)
        if not stored:
            info(ctx.prompter, t("step-results-no-points"))
            return Outcome.BACK
        state = ctx.box.state
        calibration = None
        if state.calibration_set is not None:
            # The session's own copy, whatever its age: the points were measured with it.
            calibration = read_session_mic_calibration(ctx.session_dir)
        mics = {
            name: point.meta.mic_input if point.meta else None for name, point in stored.items()
        }
        analysis = analyze_points(
            {name: PointResponse(p.ir, p.fs_hz) for name, p in stored.items()},
            point_cal_db=_point_corrections(calibration, mics),
        )
        info(ctx.prompter, t("step-results-what", count=len(stored)), *findings(analysis))
        info(ctx.prompter, t("step-results-next-milestones"), t("glossary-crossover"))
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
        """The checklist for the person."""
        task(
            ctx.prompter,
            t("step-finish-what"),
            t("step-finish-undo-by-hand"),
            t("step-finish-listen"),
            t("step-finish-mics", count=len(ctx.box.state.mic_inputs)),
        )
        info(ctx.prompter, t("step-finish-saved", path=str(ctx.session_dir)))
        return Outcome.DONE
