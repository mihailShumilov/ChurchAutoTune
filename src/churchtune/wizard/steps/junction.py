"""Step S10b (SPEC §7 S10, §9.2): the sub / tops junction, measured with temporary mutes.

At the junction points every microphone hears three blocks in one stream: the sub alone, the
tops alone, and everything as it normally plays. Only matrices are muted, through the write
port: the original switches are recorded before the first mute, every switch is confirmed by
readback and the originals come back whatever happens. Without this measurement no crossover is
proposed (a crossover set blind can open a hole in the bass).
"""

from dataclasses import dataclass

from churchtune.domain.snapshot import SourceKind
from churchtune.errors import ConsoleUnreachable, MeasurementAborted, UnsafeWrite
from churchtune.measure.junction import measure_junction_round
from churchtune.measure.rounds import plan_rounds
from churchtune.storage.files import JUNCTION_DIR, save_junction_point
from churchtune.storage.models import JunctionMeta, JunctionState, RolesModel, WizardStateFile
from churchtune.wizard.services import FS_HZ, MuteSession, WizardContext, WritePort
from churchtune.wizard.state import Outcome
from churchtune.wizard.steps.apply import WRITING_GOALS, ask_word, write_table
from churchtune.wizard.steps.measure import (
    MeasurePoints,
    ensure_backend,
    venue_plan,
    warn_before_sound,
)
from churchtune.wizard.steps.setup import ensure_driver, load_snapshot, measuring
from churchtune.wizard.ui import info, t

MUTE_WORD = "mute-word"
RESTORE_TRIES = 5
RESTORE_EVERY_S = 2.0  # SPEC §9.4: try again every 2 s while the console does not answer
QUIET_BLOCK_DBFS = -50.0


def junction_matrices(ctx: WizardContext) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    """The sub and top matrices of the confirmed topology; None when mutes cannot separate them.

    Only matrices are muted: a top or sub path on Main LR or a bus cannot be isolated.
    """
    from churchtune.domain.topology import with_roles  # noqa: PLC0415 - one use

    driver = ensure_driver(ctx)
    topology = with_roles(driver.infer_topology(load_snapshot(ctx)), ctx.box.state.roles)
    if not topology.subs or not topology.tops:
        return None
    if any(r.kind is not SourceKind.MATRIX for r in (*topology.subs, *topology.tops)):
        return None
    sub = tuple(dict.fromkeys(r.number for r in topology.subs))
    tops = tuple(dict.fromkeys(r.number for r in topology.tops))
    if set(sub) & set(tops):
        return None
    return sub, tops


def restore_pending_mutes(ctx: WizardContext) -> bool:  # noqa: PLR0911 - one exit per case
    """Put back the mutes of an interrupted measurement (fresh read, table, typed word).

    Returns:
        True when nothing is pending any more.
    """
    writes = ctx.services.writes
    if writes is None:
        return True
    try:
        plan = writes.pending_mutes()
    except ConsoleUnreachable:
        info(ctx.prompter, t("apply-console-lost"))
        return False
    if plan is None:
        return True
    if not plan.writes:  # the console already has every original switch: nothing is written
        try:
            return writes.restore_pending_mutes(plan, "", "")
        except (UnsafeWrite, ConsoleUnreachable):
            return False
    answer = ask_word(
        ctx, MUTE_WORD, t("mutes-pending-what"), t("mutes-pending-type",
                                                   word=ctx.services.words(MUTE_WORD)),
        table=write_table(plan),
    )  # fmt: skip
    if answer is None:
        info(ctx.prompter, t("mutes-pending-left"))
        return False
    try:
        writes.restore_pending_mutes(plan, *answer)
    except UnsafeWrite:
        info(ctx.prompter, t("mutes-pending-changed"))
        return False
    except ConsoleUnreachable:
        info(ctx.prompter, t("apply-console-lost"))
        return False
    info(ctx.prompter, t("mutes-pending-done"))
    return True


def _restore(ctx: WizardContext, session: MuteSession, matrices: tuple[int, ...]) -> None:
    """Put the originals back, trying again every 2 s; say what to do by hand if it fails."""
    for attempt in range(RESTORE_TRIES):
        if session.restore():
            return
        if attempt < RESTORE_TRIES - 1:
            ctx.services.sleep(RESTORE_EVERY_S)
    numbers = ", ".join(str(n) for n in matrices)
    info(ctx.prompter, t("junction-restore-by-hand", matrices=numbers))


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
            info(ctx.prompter, t("junction-not-possible"))
            return Outcome.DONE
        if not restore_pending_mutes(ctx):
            info(ctx.prompter, t("junction-skipped-pending"))
            return Outcome.DONE
        matrices = junction_matrices(ctx)
        if matrices is None:
            info(ctx.prompter, t("junction-not-possible"))
            return Outcome.DONE
        sub, tops = matrices
        word = ctx.services.words(MUTE_WORD)
        answer = ask_word(
            ctx, MUTE_WORD, t("junction-what", count=len(todo)), t("junction-mutes"),
            t("junction-type", word=word), table=None,
        )  # fmt: skip
        if answer is None:
            info(ctx.prompter, t("junction-cancelled"))
            return Outcome.DONE
        self._rounds(ctx, writes, todo, (sub, tops), answer)
        count = len(ctx.box.state.junction_measured)
        info(ctx.prompter, t("junction-done", count=count))
        return Outcome.DONE

    @staticmethod
    def _rounds(
        ctx: WizardContext,
        writes: WritePort,
        todo: list[str],
        matrices: tuple[tuple[int, ...], tuple[int, ...]],
        answer: tuple[str, str],
    ) -> None:
        sub, tops = matrices
        plan = venue_plan(ctx)
        ctx.mics_checked = False  # read the console again before the first sound
        level = ctx.box.state.sweep_level_dbfs
        for one in plan_rounds(todo, list(ctx.box.state.mic_inputs)):
            MeasurePoints.place(ctx, one, plan)
            warn_before_sound(ctx, t("junction-sweeps"), countdown=True)
            session = writes.start_junction_mutes(sub, tops, *answer)
            try:
                mics = measure_junction_round(
                    ensure_backend(ctx),
                    list(one.inputs),
                    session.switch,
                    fs_hz=FS_HZ,
                    sweep=ctx.services.sweep,
                    level_dbfs=level if level is not None else -24.0,
                    stop=ctx.services.stop,
                )
            except MeasurementAborted:
                _restore(ctx, session, (*sub, *tops))
                raise
            except (UnsafeWrite, ConsoleUnreachable):
                _restore(ctx, session, (*sub, *tops))
                info(ctx.prompter, t("junction-mute-failed"))
                return
            _restore(ctx, session, (*sub, *tops))
            where = {p.usb_input: p.point for p in one.placements}
            for mic in mics:
                point = where[mic.usb_input]
                meta = JunctionMeta(
                    point=point,
                    time=ctx.services.now().isoformat(timespec="seconds"),
                    level_dbfs=level if level is not None else -24.0,
                    peaks_dbfs=mic.peaks_dbfs,
                    roles=RolesModel(sub=sub, mains=tops),
                    state=JunctionState(sub_fader=None),
                )
                save_junction_point(ctx.session_dir / JUNCTION_DIR, meta,
                                    (mic.sub, mic.tops, mic.both), FS_HZ)  # fmt: skip
                ctx.box.update(junction_measured={**ctx.box.state.junction_measured,
                                                  point: point})  # fmt: skip
                if min(mic.peaks_dbfs) < QUIET_BLOCK_DBFS:
                    info(ctx.prompter, t("junction-quiet-block", point=point))
