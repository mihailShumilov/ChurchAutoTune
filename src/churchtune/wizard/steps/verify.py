"""Step S13 (SPEC §7, §8.6): measure again and say whether it got better.

At least four of the baseline points are measured again, among them the baseline's worst point
and the point nearest the middle of the hall; with more microphones, one round with all of them.
The verdict follows §8.6. When it got worse, the step offers the way back right away (with the
same table and typed word as any write).
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from churchtune.analysis.points import Analysis
from churchtune.analysis.response import interpolate_at, third_octave_centres
from churchtune.analysis.verdict import Verdict, VerifyResult, verify, worst_point
from churchtune.design.proposal import TargetSpec
from churchtune.design.target import target_curve
from churchtune.domain.arrays import FloatArray
from churchtune.measure.plan import PointPlan
from churchtune.storage.models import WizardStateFile
from churchtune.wizard.protocol import Text
from churchtune.wizard.services import WizardContext
from churchtune.wizard.state import Outcome
from churchtune.wizard.steps.apply import WRITING_GOALS, undo_applied
from churchtune.wizard.steps.finish import session_analysis
from churchtune.wizard.steps.measure import measure_rounds, venue_plan
from churchtune.wizard.steps.setup import measuring
from churchtune.wizard.ui import confirm, info, t

VERIFY_DIR = "verify"
MIN_VERIFY_POINTS = 4
TARGET = TargetSpec()
# The before/after picture: one column per third octave, 63 Hz-8 kHz.
CHART_RANGE_HZ = (63.0, 8000.0)
CHART_LEVELS = " ▁▂▃▄▅▆▇█"
CHART_SPAN_DB = 8.0


def target_of(analysis: Analysis) -> FloatArray:
    """The house curve the proposals aimed at, on the analysis grid."""
    return target_curve(
        analysis.grid_hz,
        TARGET.tilt_db_per_oct,
        TARGET.tilt_from_hz,
        TARGET.bass_lift_db,
        TARGET.bass_from_hz,
    )


def centre_point(plan: PointPlan, points: Sequence[str]) -> str:
    """The point nearest the middle of the listening area."""
    middle = (plan.width_m / 2, plan.depth_m / 2)

    def distance(point: str) -> float:
        where = plan.point(point)
        return float(np.hypot(where.x_m - middle[0], where.y_m - middle[1]))

    return min(points, key=distance)


def verify_points(baseline: Analysis, plan: PointPlan, mic_count: int) -> tuple[str, ...]:
    """The points to measure again: the worst, the centre, then the rest in walking order.

    At least four (or all the baseline has), and as many as there are microphones, so a
    multi-microphone check is one round.
    """
    measured = [p for p in plan.ids if p in baseline.norm_db] or list(baseline.point_names)
    worst = worst_point(baseline, target_of(baseline), measured)
    chosen = [worst]
    known = [p for p in measured if p in plan.ids]
    if known:
        centre = centre_point(plan, known)
        if centre not in chosen:
            chosen.append(centre)
    wanted = min(len(measured), max(MIN_VERIFY_POINTS, mic_count))
    chosen.extend(p for p in measured if p not in chosen)
    return tuple(chosen[:wanted])


def chart(before: Analysis, after: Analysis) -> str:
    """Deviation from the target before and after, as two rows of blocks (higher = more off)."""
    centres = third_octave_centres(*CHART_RANGE_HZ)
    target = target_of(before)
    rows = []
    for label, analysis in (("1", before), ("2", after)):
        off = np.abs(interpolate_at(centres, analysis.avg_db - target, analysis.grid_hz))
        steps = np.clip(off / CHART_SPAN_DB * (len(CHART_LEVELS) - 1), 0, len(CHART_LEVELS) - 1)
        rows.append(label + " " + "".join(CHART_LEVELS[round(float(s))] for s in steps))
    rows.append("  63" + " " * max(len(centres) - 4, 1) + "8k")
    return "\n".join(rows)


def _numbers(result: VerifyResult) -> list[Text]:
    return [
        t("verify-m", before_db=round(result.m_before_db, 1), after_db=round(result.m_after_db, 1)),
        t("verify-worst", point=result.worst_point, delta_db=round(result.worst_delta_db, 1)),
        t(
            "verify-band",
            point=result.band_point,
            freq_hz=round(result.band_hz),
            delta_db=round(result.band_delta_db, 1),
        ),
    ]


@dataclass(frozen=True, slots=True)
class Verify:
    """S13: the check measurement and the verdict."""

    id: str = "verify"
    title: str = "step-verify-title"
    changes_console: bool = False

    def applies(self, state: WizardStateFile) -> bool:
        """Goals that change the system (counted from the start, so the total stays put)."""
        return measuring(state) and state.goal in WRITING_GOALS

    def run(self, ctx: WizardContext) -> Outcome:
        """Pick the points, measure them again, judge, offer the way back when worse."""
        state = ctx.box.state
        if state.applied_undo is None:
            info(ctx.prompter, t("verify-nothing-applied"))
            return Outcome.DONE
        baseline = session_analysis(ctx, ctx.session_dir)
        if baseline is None:
            info(ctx.prompter, t("verify-nothing-applied"))
            return Outcome.DONE
        plan = venue_plan(ctx)
        points = state.verify_points or verify_points(baseline, plan, len(state.mic_inputs))
        ctx.box.update(verify_points=points)
        info(ctx.prompter, t("verify-what", count=len(points)), t("verify-same-places"))
        directory = ctx.session_dir / VERIFY_DIR
        ctx.mics_checked = False  # read the console again before the first sound

        def record(point: str) -> None:
            ctx.box.update(verify_measured={**ctx.box.state.verify_measured, point: point})

        todo = [p for p in points if p not in ctx.box.state.verify_measured]
        if todo:
            measure_rounds(ctx, todo, plan, directory, record)
        before = session_analysis(ctx, ctx.session_dir, points)
        after = session_analysis(ctx, directory, points)
        if before is None or after is None:
            info(ctx.prompter, t("verify-no-points"))
            return Outcome.DONE
        result = verify(before, after, target_of(before))
        ctx.box.update(verify_verdict=result.verdict.value)
        verdict = t(f"verify-{result.verdict.value.replace('_', '-')}")
        info(ctx.prompter, verdict, *_numbers(result), t("verify-chart"),
             picture=chart(before, after))  # fmt: skip
        if result.verdict is not Verdict.WORSE:
            return Outcome.DONE
        undo = ctx.services.workspace.root / state.applied_undo
        if confirm(ctx.prompter, t("verify-offer-undo"), default=True):
            undo_applied(ctx, undo)
        return Outcome.DONE
