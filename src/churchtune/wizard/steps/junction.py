"""Step S10b (SPEC §7 S10, §9.2): the sub / tops junction, measured with temporary mutes.

At the junction points every microphone hears three blocks in one stream: the sub alone, the
tops alone, and everything as it normally plays. Only matrices are muted, through the write
port: the original switches are recorded before the first mute, every switch is confirmed by
readback and the originals come back in ``finally``, whatever happens. A restore that is not
confirmed stops the measurement and tells the person which Mute buttons to check. Without this
measurement no crossover is proposed (a crossover set blind can open a hole in the bass).
"""

import logging
from dataclasses import dataclass, replace
from enum import StrEnum

from churchtune.domain.snapshot import SignalRef, SourceKind
from churchtune.errors import ConsoleUnreachable, UnsafeWrite
from churchtune.measure.junction import JunctionMic, junction_problem, measure_junction_round
from churchtune.measure.plan import PointPlan
from churchtune.measure.rounds import Round, plan_rounds
from churchtune.storage.files import JUNCTION_DIR, save_junction_point
from churchtune.storage.models import JunctionMeta, JunctionState, RolesModel, WizardStateFile
from churchtune.wizard.protocol import Text
from churchtune.wizard.services import FS_HZ, MuteSession, WizardContext, WritePort
from churchtune.wizard.state import Outcome
from churchtune.wizard.steps.apply import (
    MUTE_WORD,
    WRITING_GOALS,
    ask_word,
    restore_pending_mutes,
)
from churchtune.wizard.steps.measure import (
    MeasurePoints,
    ensure_backend,
    place_text,
    venue_plan,
    warn_before_sound,
)
from churchtune.wizard.steps.proposal import path_text
from churchtune.wizard.steps.setup import ensure_driver, load_snapshot, measuring
from churchtune.wizard.ui import info, t

logger = logging.getLogger(__name__)
RESTORE_TRIES = 5
RESTORE_EVERY_S = 2.0  # SPEC §9.4: try again every 2 s while the console does not answer
# The mute switches between blocks happen in the silent pre-roll of the next block; a longer
# pre-roll than the ordinary sweep's leaves time for a slow readback before the sweep starts.
JUNCTION_PREROLL_S = 1.0


class RoundsEnd(StrEnum):
    """How the junction rounds ended."""

    DONE = "done"
    MUTE_FAILED = "mute_failed"  # a mute was not confirmed; the switches came back
    NOT_RESTORED = "not_restored"  # the switches are not confirmed back: the person checks


@dataclass(frozen=True, slots=True)
class JunctionMatrices:
    """The matrices the junction mutes, with their names on the console."""

    sub: tuple[int, ...]
    tops: tuple[int, ...]
    names: dict[int, str]

    def texts(self) -> tuple[Text, ...]:
        """Every muted matrix in words: "matrix 3 (Sub buffer)"."""
        texts = []
        for n in (*self.sub, *self.tops):
            matrix = path_text(SignalRef(SourceKind.MATRIX, n))
            name = self.names.get(n, "").strip()
            texts.append(t("junction-matrix", matrix=matrix, name=name) if name else matrix)
        return tuple(texts)


def junction_matrices(ctx: WizardContext) -> JunctionMatrices | None:
    """The sub and top matrices of the confirmed topology; None when mutes cannot separate them.

    Only matrices are muted: a top or sub path on Main LR or a bus cannot be isolated.
    """
    from churchtune.domain.topology import with_roles  # noqa: PLC0415 - one use

    driver = ensure_driver(ctx)
    snapshot = load_snapshot(ctx)
    topology = with_roles(driver.infer_topology(snapshot), ctx.box.state.roles)
    refs = (*topology.subs, *topology.tops)
    if not topology.subs or not topology.tops:
        return None
    if any(r.kind is not SourceKind.MATRIX for r in refs):
        return None
    sub = tuple(dict.fromkeys(r.number for r in topology.subs))
    tops = tuple(dict.fromkeys(r.number for r in topology.tops))
    if set(sub) & set(tops):
        return None
    names = {n: m.name for n, m in snapshot.matrices.items() if n in (*sub, *tops)}
    return JunctionMatrices(sub, tops, names)


def _restore(ctx: WizardContext, session: MuteSession, matrices: JunctionMatrices) -> bool:
    """Put the originals back, trying again every 2 s; tell the person if it never confirms."""
    for attempt in range(1, RESTORE_TRIES + 1):
        if session.restore():
            return True
        logger.warning("junction mutes not restored, attempt %d of %d", attempt, RESTORE_TRIES)
        if attempt < RESTORE_TRIES:
            ctx.services.sleep(RESTORE_EVERY_S)
    info(ctx.prompter, t("junction-restore-by-hand", matrices=matrices.texts()))
    return False


@dataclass(frozen=True, slots=True)
class _Round:
    """One round of the junction and what it needs to be saved."""

    one: Round
    plan: PointPlan
    matrices: JunctionMatrices
    level_dbfs: float


def _save(ctx: WizardContext, round_: _Round, mics: tuple[JunctionMic, ...]) -> None:
    where = {p.usb_input: p.point for p in round_.one.placements}
    plan = round_.plan
    for mic in mics:
        point = where[mic.usb_input]
        meta = JunctionMeta(
            point=point,
            time=ctx.services.now().isoformat(timespec="seconds"),
            level_dbfs=round_.level_dbfs,
            peaks_dbfs=mic.peaks_dbfs,
            roles=RolesModel(sub=round_.matrices.sub, mains=round_.matrices.tops),
            state=JunctionState(sub_fader=None),
        )
        problem = junction_problem(mic, FS_HZ)
        if problem is not None:  # never designed on: a mute did not switch, or a block was off
            number = plan.ids.index(point) + 1 if point in plan.ids else 0
            place = place_text(plan, point)
            info(ctx.prompter, t(f"junction-rejected-{problem.value}", number=number,
                                 place=place))  # fmt: skip
            continue
        irs = (mic.sub, mic.tops, mic.both)
        save_junction_point(ctx.session_dir / JUNCTION_DIR, meta, irs, FS_HZ)
        measured = {**ctx.box.state.junction_measured, point: point}
        ctx.box.update(junction_measured=measured)


def measure_rounds(
    ctx: WizardContext,
    writes: WritePort,
    todo: list[str],
    matrices: JunctionMatrices,
    answer: tuple[str, str],
) -> RoundsEnd:
    """Every round: place, warn, record the switches, three blocks, the switches back.

    The switches come back in ``finally``: a stop, a failed mute, an audio error or Ctrl+C;
    a restore that is not confirmed ends the rounds.
    """
    plan = venue_plan(ctx)
    ctx.mics_checked = False  # read the console again before the first sound
    stored = ctx.box.state.sweep_level_dbfs
    level = stored if stored is not None else ctx.services.sweep.level_dbfs
    sweep = replace(ctx.services.sweep,
                    preroll_s=max(ctx.services.sweep.preroll_s, JUNCTION_PREROLL_S))  # fmt: skip
    for one in plan_rounds(todo, list(ctx.box.state.mic_inputs)):
        MeasurePoints.place(ctx, one, plan)
        warn_before_sound(ctx, t("junction-sweeps"), countdown=True)
        try:
            session = writes.start_junction_mutes(matrices.sub, matrices.tops, *answer)
        except (UnsafeWrite, ConsoleUnreachable):
            return RoundsEnd.MUTE_FAILED  # nothing was muted
        mics: tuple[JunctionMic, ...] = ()
        failed = False
        try:
            mics = measure_junction_round(
                ensure_backend(ctx),
                list(one.inputs),
                session.switch,
                fs_hz=FS_HZ,
                sweep=sweep,
                level_dbfs=level,
                stop=ctx.services.stop,
            )
        except (UnsafeWrite, ConsoleUnreachable):
            failed = True
        finally:
            restored = _restore(ctx, session, matrices)
        if not restored:
            return RoundsEnd.NOT_RESTORED
        if failed:
            return RoundsEnd.MUTE_FAILED
        _save(ctx, _Round(one, plan, matrices, level), mics)
    return RoundsEnd.DONE


@dataclass(frozen=True, slots=True)
class Junction:
    """S10b: the junction points, sub and tops measured apart with temporary matrix mutes."""

    id: str = "junction"
    title: str = "step-junction-title"
    changes_console: bool = True

    def applies(self, state: WizardStateFile) -> bool:
        """Goals that tune the system, when the topology has a subwoofer."""
        return (
            measuring(state)
            and state.goal in WRITING_GOALS
            and any(role == "sub" for role in state.roles.values())
        )

    def run(self, ctx: WizardContext) -> Outcome:
        """Check pending mutes, find the matrices, ask, then measure round by round."""
        state = ctx.box.state
        writes = ctx.services.writes
        todo = [p for p in state.junction_points if p not in state.junction_measured]
        if not todo:
            return Outcome.DONE
        if writes is None:
            info(ctx.prompter, t("junction-no-writes"))
            return Outcome.DONE
        if not restore_pending_mutes(ctx):
            info(ctx.prompter, t("junction-skipped-pending"))
            return Outcome.DONE
        matrices = junction_matrices(ctx)
        if matrices is None:
            info(ctx.prompter, t("junction-not-possible"))
            return Outcome.DONE
        word = ctx.services.words(MUTE_WORD)
        answer = ask_word(
            ctx,
            MUTE_WORD,
            t("junction-what", count=len(todo)),
            t("junction-mutes"),
            t("junction-type", word=word),
            table=None,
        )
        if answer is None:
            info(ctx.prompter, t("junction-cancelled"))
            return Outcome.DONE
        end = measure_rounds(ctx, writes, todo, matrices, answer)
        done, total = len(ctx.box.state.junction_measured), len(state.junction_points)
        partial = t("junction-partial", done=done, total=total)
        if end is RoundsEnd.MUTE_FAILED:
            info(ctx.prompter, t("junction-mute-failed"), partial)
        elif end is RoundsEnd.DONE:
            info(ctx.prompter, t("junction-done", count=done) if done == total else partial)
        return Outcome.DONE
