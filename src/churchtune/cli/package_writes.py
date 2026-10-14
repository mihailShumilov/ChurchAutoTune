"""Planning, checking and sending package plans on an X32/M32 (SPEC S12, ADR 0010).

Shared by ``ctune apply-packages`` / ``ctune undo`` and by the wizard's write port. The proposal
and undo files are not trusted: every plan starts from a fresh read, is ordered never louder,
validated per scope, planned again right before sending and compared with what was shown, and
every run of one scope is authorized on its own with the typed word, bound to the undo file
that is on disk before the first write. No user-facing text here: refusals are ``UnsafeWrite``.
"""

import dataclasses
import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from churchtune.cli import records
from churchtune.cli.context import FS_HZ, Console
from churchtune.cli.mute_writes import (
    X32MuteSession,
    plan_pending_restore,
    restore_pending,
    start_junction_mutes,
)
from churchtune.domain.changes import WireValue, WriteScope
from churchtune.domain.console import ConsoleState, Roles
from churchtune.domain.snapshot import SignalRef, SourceKind
from churchtune.errors import UnsafeWrite
from churchtune.mixers.x32 import params
from churchtune.mixers.x32.laws import X32ValueLaws
from churchtune.mixers.x32.nodes import read_console_state
from churchtune.mixers.x32.plan_writes import (
    PlannedChange,
    describe_path,
    plan_writes,
    runs_by_scope,
    strip_of,
    wire_value,
    with_wire_value,
)
from churchtune.mixers.x32.topology import detect_roles
from churchtune.safety.authorize import TypedWord, authorize_typed
from churchtune.safety.cut_filters import PathRole, check_cut_changes
from churchtune.safety.order import order_steps
from churchtune.safety.plan import same_step
from churchtune.safety.policy import WritePolicy
from churchtune.storage import files
from churchtune.storage.models import ChangeModel, PackageModel, UndoFile
from churchtune.storage.paths import Workspace
from churchtune.wizard.applying import (
    CROSSOVER,
    collapsed_changes,
    combined_target,
    ordered_steps,
    role_of,
    same_band,
    speaker_chains,
    strip_states,
    writable_packages,
    written_values,
)
from churchtune.wizard.services import PlannedWrite, WriteOutcome, WritePlan

LAWS = X32ValueLaws()
# Scopes a package plan writes; an undo file naming any other is refused.
PACKAGE_SCOPES = frozenset({WriteScope.SYSTEM_EQ, WriteScope.LEVELS, WriteScope.TEMP_MUTES})
PACKAGES, UNDO_OF_PACKAGES = "packages", "undo of packages"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_policy(
    state: ConsoleState, roles: Roles, *, allow_cut_filters: bool = False
) -> WritePolicy:
    """The policy of a package plan: the speaker matrices, the sub's fader as it is now.

    Cut filters may be written only by a plan that holds the crossover package, or by the undo
    of one.
    """
    baselines = tuple((f"/mtx/{n:02d}/mix/fader", state.matrices[n].fader_db) for n in roles.sub)
    # A speaker matrix may be muted for a change that has no never-louder order otherwise, and
    # only switched back to the state it has now.
    speakers = (*roles.sub, *roles.mains)
    switches = tuple((f"/mtx/{n:02d}/mix/on", int(state.matrices[n].on)) for n in speakers)
    return WritePolicy(
        LAWS,
        frozenset(roles.all),
        sub_matrices=frozenset(roles.sub),
        top_matrices=frozenset(roles.mains),
        mute_originals=switches,
        level_baselines=baselines,
        allow_cut_filters=allow_cut_filters,
    )


def undo_policy(undo: UndoFile, policy: WritePolicy) -> WritePolicy:
    """The policy of an undo: a matrix switch goes back to the value the undo file recorded.

    After a stop half-way a matrix may still be muted; its original is the old value of the
    first write, not what the console has now.
    """
    recorded = {c.path: int(c.old_value) for c in collapsed_changes(undo.changes)
                if c.path.endswith("/mix/on")}  # fmt: skip
    switches = dict(policy.mute_originals) | recorded
    return dataclasses.replace(policy, mute_originals=tuple(sorted(switches.items())))


def fresh_inputs(
    console: Console, *, allow_cut_filters: bool
) -> tuple[ConsoleState, Roles, WritePolicy]:
    """A fresh read of the console, its speaker roles and the package policy built on them.

    Raises:
        UnsafeWrite: The sub and top matrices cannot be told apart.
        ConsoleUnreachable: The console did not answer.
    """
    state = read_console_state(console.client)
    try:
        roles = detect_roles(state)
    except ValueError as error:
        raise UnsafeWrite(f"speaker roles unclear: {error}", roles=str(error)) from error
    return state, roles, package_policy(state, roles, allow_cut_filters=allow_cut_filters)


def path_roles(policy: WritePolicy) -> dict[SignalRef, PathRole]:
    """The sub and top matrices of the policy (from the fresh read) as path roles."""
    roles = {SignalRef(SourceKind.MATRIX, n): PathRole.SUB for n in policy.sub_matrices}
    roles.update({SignalRef(SourceKind.MATRIX, n): PathRole.TOPS for n in policy.top_matrices})
    return roles


def has_crossover(packages: Sequence[PackageModel]) -> bool:
    """True when the plan holds the crossover package (the only one that sets cut filters)."""
    return any(p.kind == CROSSOVER for p in packages)


def plan_package_writes(
    packages: Sequence[PackageModel], state: ConsoleState, policy: WritePolicy
) -> tuple[PlannedChange, ...]:
    """The scoped writes of the packages from a fresh read, each validated by the policy.

    Raises:
        UnsafeWrite: The console changed since the measurement, no safe order exists, or a
            write is outside the policy.
    """
    fresh = strip_states(state)
    target = combined_target(packages, fresh, LAWS, path_roles(policy))
    steps = ordered_steps(target, fresh, FS_HZ, mutable=path_roles(policy).keys())
    planned = plan_writes(steps, fresh, LAWS)
    for item in planned:
        policy.validate(item.change.path, item.change.new_value, item.scope)
    return planned


def plan_differences(
    shown: Sequence[PlannedChange], fresh: Sequence[PlannedChange]
) -> tuple[str, ...]:
    """Writes that differ between the plan the person saw and one made from a fresh read."""
    if [(p.scope, p.change.path) for p in shown] != [(p.scope, p.change.path) for p in fresh]:
        return tuple(sorted({p.change.path for p in (*shown, *fresh)}))
    differ = []
    for a, b in zip(shown, fresh, strict=True):
        kind = a.change.path.rsplit("/", 1)[1]
        if not (
            same_step(LAWS, kind, a.change.old_value, b.change.old_value)
            and same_step(LAWS, kind, a.change.new_value, b.change.new_value)
        ):
            differ.append(a.change.path)
    return tuple(differ)


def require_same_plan(shown: Sequence[PlannedChange], fresh: Sequence[PlannedChange]) -> None:
    """Refuse when the console changed while the plan was on the screen.

    Raises:
        UnsafeWrite: The fresh plan differs (``mismatches`` lists the writes).
    """
    differ = plan_differences(shown, fresh)
    if differ:
        raise UnsafeWrite(
            "the console changed while the plan was shown: " + ", ".join(differ),
            mismatches=", ".join(differ),
        )


def send_runs(
    console: Console,
    planned: Sequence[PlannedChange],
    undo_path: Path,
    word: TypedWord,
    policy: WritePolicy,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[list[records.WriteResultLike], bool]:
    """Send the runs of one scope each, in order; stop at the first write that fails.

    The undo file gets the results after every run, so an interruption leaves an honest record.

    Returns:
        The results so far and whether every planned write was confirmed. A run refused after
        an earlier run was written ends the plan there as incomplete (the undo file holds what
        was written).

    Raises:
        UnsafeWrite: The first run was refused: nothing was written.
    """
    from churchtune.mixers.x32.writer import ScopedWriter  # noqa: PLC0415 - lazy by design

    results: list[records.WriteResultLike] = []
    for scope, changes in runs_by_scope(planned):
        try:
            auth = authorize_typed(
                scope, changes, undo_path, word, policy, undo_sha256=_sha256(undo_path)
            )
        except UnsafeWrite:
            if not results:
                raise
            return results, False
        sent = list(ScopedWriter(console.transport, console.client, auth).apply(should_stop))
        results.extend(sent)
        undo = files.read_undo(undo_path)
        update = {"results": records.result_models(results)}
        files.write_json(undo_path, undo.model_copy(update=update))
        if len(sent) != len(changes) or not all(r.ok for r in sent):
            return results, False
    return results, True


def package_undo_file(
    created: str,
    ip: str | None,
    planned: Sequence[PlannedChange],
    *,
    proposal: Path | None = None,
    reverts: Path | None = None,
) -> UndoFile:
    """The undo file of a package plan (or of its undo): every write with its scope.

    The file's own ``scope`` is the first write's, for information only: each change carries the
    scope it is validated in.
    """
    return UndoFile(
        created=created,
        ip=ip,
        scope=planned[0].scope,
        what=PACKAGES if reverts is None else UNDO_OF_PACKAGES,
        proposal=None if proposal is None else str(proposal),
        reverts=None if reverts is None else str(reverts),
        changes=tuple(ChangeModel.from_domain(p.change, p.scope) for p in planned),
    )


def is_package_undo(undo: UndoFile) -> bool:
    """True for an undo file written by ``apply-packages`` (every change carries its scope)."""
    return undo.what in (PACKAGES, UNDO_OF_PACKAGES) or any(c.scope for c in undo.changes)


@dataclass(frozen=True, slots=True)
class UndoPlan:
    """What a package undo does to each stored write.

    Attributes:
        planned: Writes that put old values back, in a never-louder order.
        already_old: Paths that have their old value already.
        changed_by_hand: Paths that hold neither the old nor the new value; left alone.
    """

    planned: tuple[PlannedChange, ...]
    already_old: tuple[str, ...]
    changed_by_hand: tuple[str, ...]


def plan_package_undo(undo: UndoFile, state: ConsoleState, policy: WritePolicy) -> UndoPlan:
    """Three-way undo: revert what still has the new value, skip what was changed by hand.

    Raises:
        UnsafeWrite: A stored write has no package scope, names a path or value no plan may
            write, or the way back is outside the policy.
    """
    fresh = strip_states(state)
    target = dict(fresh)
    already_old: list[str] = []
    by_hand: list[str] = []
    written = written_values(undo.changes)
    for stored in collapsed_changes(undo.changes):
        if stored.scope not in PACKAGE_SCOPES:
            raise UnsafeWrite(f"undo file names a write outside the packages: {stored.path}")
        if stored.path.endswith("/eq/on"):
            raise UnsafeWrite(f"a package never switches an EQ on or off: {stored.path}")
        strip = strip_of(stored.path)
        if strip not in fresh:
            raise UnsafeWrite(f"the console has no {strip.label()}", strip=strip.label())
        policy.validate(stored.path, stored.old_value, stored.scope)
        kind = stored.path.rsplit("/", 1)[1]
        now = wire_value(target[strip], stored.path, LAWS)
        if same_step(LAWS, kind, now, stored.old_value):
            already_old.append(stored.path)
        elif any(same_step(LAWS, kind, now, value) for value in written[stored.path]):
            # The last value, or an earlier one when the plan stopped half-way.
            target[strip] = with_wire_value(target[strip], stored.path, stored.old_value)
        else:
            by_hand.append(stored.path)
    roles = path_roles(policy)
    for ref, wanted in target.items():
        if wanted.eq != fresh[ref].eq:
            check_cut_changes(
                ref.label(), role_of(ref, roles), fresh[ref].eq, wanted.eq,
                lambda a, b: same_band(LAWS, a, b), may_remove=True,
            )  # fmt: skip
    steps = order_steps(fresh, target, speaker_chains(fresh), FS_HZ, mutable=roles.keys())
    planned = plan_writes(steps, fresh, LAWS)
    for item in planned:
        policy.validate(item.change.path, item.change.new_value, item.scope)
    return UndoPlan(planned, tuple(already_old), tuple(by_hand))


# A plan recipe: from a fresh read and its policy to the scoped writes.
Recipe = Callable[[ConsoleState, WritePolicy], tuple[PlannedChange, ...]]


@dataclass(frozen=True, slots=True)
class _PortPlan:
    """What the port hands the wizard inside a ``WritePlan``: how to plan again, and the plan."""

    recipe: Recipe
    planned: tuple[PlannedChange, ...]
    allow_cut_filters: bool
    adjust: Callable[[WritePolicy], WritePolicy] = lambda policy: policy


@dataclass(slots=True)
class X32WritePort:
    """The wizard's write port on an X32/M32 (``wizard.services.WritePort``).

    Attributes:
        connection: The console the wizard connected last, as (address, transport and client).
        now: Wall clock for the undo files.
        workspace: Where the pending-restore file of temporary mutes lives.
    """

    connection: Callable[[], tuple[str, Console]]
    now: Callable[[], datetime]
    workspace: Workspace

    def start_junction_mutes(
        self, sub: Sequence[int], tops: Sequence[int], typed: str, expected: str
    ) -> X32MuteSession:
        """Temporary matrix mutes for a junction round (``cli.mute_writes``)."""
        _, console = self.connection()
        created = self.now().isoformat(timespec="seconds")
        return start_junction_mutes(console, self.workspace, created, sub, tops,
                                    TypedWord(typed, expected))  # fmt: skip

    def pending_mutes(self) -> WritePlan | None:
        """Mutes of an interrupted measurement to put back, from a fresh read."""
        return plan_pending_restore(self.connection()[1], self.workspace)

    def restore_pending_mutes(self, plan: WritePlan, typed: str, expected: str) -> bool:
        """Put pending mutes back as shown."""
        return restore_pending(self.connection()[1], self.workspace, plan,
                               TypedWord(typed, expected))  # fmt: skip

    def _fresh(self, allow_cut_filters: bool) -> tuple[str, Console, ConsoleState, WritePolicy]:
        host, console = self.connection()
        state, _, policy = fresh_inputs(console, allow_cut_filters=allow_cut_filters)
        return host, console, state, policy

    def plan_packages(self, packages: Sequence[PackageModel]) -> WritePlan:
        """The writes of the chosen packages from a fresh read (delay and polarity are advice)."""
        writable, _ = writable_packages(packages)
        cut = has_crossover(writable)
        _, _, state, policy = self._fresh(cut)

        def recipe(fresh: ConsoleState, rules: WritePolicy) -> tuple[PlannedChange, ...]:
            return plan_package_writes(writable, fresh, rules) if writable else ()

        planned = recipe(state, policy)
        return WritePlan(_rows(planned), _PortPlan(recipe, planned, cut))

    def plan_undo(self, undo_path: Path) -> WritePlan:
        """Three-way undo of a package undo file."""
        try:
            undo = files.read_undo(undo_path)
        except (ValueError, OSError) as error:
            raise UnsafeWrite(f"damaged undo file: {error}") from error
        host, _, state, policy = self._fresh(True)
        if undo.ip and host and undo.ip != host:
            raise UnsafeWrite(f"the undo file was written for another console ({undo.ip})")
        plan = plan_package_undo(undo, state, undo_policy(undo, policy))

        def recipe(fresh: ConsoleState, rules: WritePolicy) -> tuple[PlannedChange, ...]:
            return plan_package_undo(undo, fresh, rules).planned

        token = _PortPlan(recipe, plan.planned, True, lambda rules: undo_policy(undo, rules))
        return WritePlan(_rows(plan.planned), token, plan.already_old, plan.changed_by_hand)

    def apply(
        self,
        plan: WritePlan,
        typed: str,
        expected: str,
        undo_path: Path,
        should_stop: Callable[[], bool],
        *,
        reverts: Path | None = None,
    ) -> WriteOutcome:
        """Plan again from a fresh read, send only the plan shown; undo file first."""
        from churchtune.mixers.x32.writer import STOPPED  # noqa: PLC0415 - lazy by design

        word = TypedWord(typed, expected)
        if not word.matches():
            raise UnsafeWrite("the confirmation word does not match; nothing is written")
        token = _planned_of(plan)
        host, console, state, fresh_policy = self._fresh(token.allow_cut_filters)
        policy = token.adjust(fresh_policy)
        require_same_plan(token.planned, token.recipe(state, policy))
        created = self.now().isoformat(timespec="seconds")
        files.write_json(
            undo_path, package_undo_file(created, host, token.planned, reverts=reverts)
        )
        results, complete = send_runs(console, token.planned, undo_path, word, policy, should_stop)
        stopped = any(r.error == STOPPED for r in results)
        failed = next((r.path for r in results if not r.ok and r.error != STOPPED), None)
        sent = sum(1 for r in results if r.ok)
        return WriteOutcome(undo_path, sent, complete, stopped, failed)


def plain_value(param: str, value: WireValue) -> float | bool | str:
    """A wire value in plain units for the wizard (Hz, dB, Q, type name, on/off)."""
    if param in ("on", "eq_on"):
        return bool(value)
    if param == "type":
        return params.EQ_TYPES[int(value)].value
    convert = {
        "freq": params.norm_to_freq,
        "gain": params.norm_to_gain,
        "q": params.norm_to_q,
        "fader": params.norm_to_fader,
    }[param]
    return float(convert(float(value)))


def _rows(planned: Sequence[PlannedChange]) -> tuple[PlannedWrite, ...]:
    rows = []
    for item in planned:
        change = item.change
        strip, band, param = describe_path(change.path)
        rows.append(
            PlannedWrite(
                change.path,
                strip,
                band,
                param,
                change.old_display,
                change.new_display,
                plain_value(param, change.old_value),
                plain_value(param, change.new_value),
            )
        )
    return tuple(rows)


def _planned_of(plan: WritePlan) -> _PortPlan:
    """The port's own plan back from the wizard; anything else is refused."""
    token = plan.token
    if type(token) is not _PortPlan:
        raise UnsafeWrite("the plan was not made by this console's write port")
    if not token.planned:
        raise UnsafeWrite("an empty plan is never sent")
    return token
