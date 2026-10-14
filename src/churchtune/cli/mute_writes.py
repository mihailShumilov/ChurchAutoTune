"""Temporary matrix mutes for the sub / tops junction measurement (SPEC S10, §9.2 temp_mutes).

Only matrices are muted (the owner's decision, ADR 0010). The original switch states are read
fresh and written to the pending-restore file before the first mute; every switch is authorized
with the typed word, bound to that file, and may only mute (0) or put back the exact original
value. The originals come back after the measurement whatever happens; a restore that fails
leaves the file, and the next run puts them back only after a fresh read, a table and the word.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from churchtune.cli.context import Console
from churchtune.domain.changes import Change, WriteScope
from churchtune.domain.snapshot import SignalRef, SourceKind
from churchtune.errors import ConsoleUnreachable, UnsafeWrite
from churchtune.measure.junction import JunctionBlock
from churchtune.mixers.x32.laws import X32ValueLaws
from churchtune.mixers.x32.nodes import read_console_state
from churchtune.mixers.x32.topology import detect_roles
from churchtune.safety.authorize import TypedWord, authorize_typed
from churchtune.safety.policy import WritePolicy
from churchtune.storage import files
from churchtune.storage.models import PendingMutes
from churchtune.storage.paths import Workspace
from churchtune.wizard.services import PlannedWrite, WritePlan

logger = logging.getLogger(__name__)
LAWS = X32ValueLaws()
MATRICES = range(1, 7)


def mute_path(matrix: int) -> str:
    """The switch of a matrix."""
    return f"/mtx/{matrix:02d}/mix/on"


def mute_policy(
    originals: Mapping[int, bool], sub: Sequence[int], tops: Sequence[int]
) -> WritePolicy:
    """Only these speaker matrices, only muted or back to their recorded value."""
    recorded = tuple((mute_path(n), int(on)) for n, on in sorted(originals.items()))
    return WritePolicy(
        LAWS,
        frozenset(originals),
        sub_matrices=frozenset(sub),
        top_matrices=frozenset(tops),
        mute_originals=recorded,
    )


def _switch_text(on: bool) -> str:
    return "ON" if on else "OFF"


def mute_changes(current: Mapping[int, bool], target: Mapping[int, bool]) -> list[Change]:
    """The switch writes from one state to another (only where they differ)."""
    return [
        Change(mute_path(n), int(current[n]), int(target[n]),
               _switch_text(current[n]), _switch_text(target[n]))
        for n in sorted(target)
        if current[n] != target[n]
    ]  # fmt: skip


def block_target(
    block: JunctionBlock, originals: Mapping[int, bool], sub: Sequence[int], tops: Sequence[int]
) -> dict[int, bool]:
    """The switch states of one junction block: the other side muted, the rest as it was."""
    if block is JunctionBlock.SUB_ONLY:
        return {n: on and n not in tops for n, on in originals.items()}
    if block is JunctionBlock.TOPS_ONLY:
        return {n: on and n not in sub for n, on in originals.items()}
    return dict(originals)


def send_mutes(
    console: Console, changes: Sequence[Change], pending: Path, word: TypedWord,
    policy: WritePolicy,
) -> list[str]:  # fmt: skip
    """Write switch changes; the paths that were confirmed.

    Raises:
        UnsafeWrite: The writes are outside the temporary-mute scope or were not confirmed.
    """
    from churchtune.mixers.x32.writer import ScopedWriter  # noqa: PLC0415 - lazy by design

    if not changes:
        return []
    auth = authorize_typed(
        WriteScope.TEMP_MUTES, changes, pending, word, policy, undo_sha256=files.sha256_of(pending)
    )
    results = list(ScopedWriter(console.transport, console.client, auth).apply())
    confirmed = [r.path for r in results if r.ok]
    if len(confirmed) != len(changes):
        raise UnsafeWrite("a matrix mute did not read back", confirmed=", ".join(confirmed))
    return confirmed


@dataclass(slots=True)
class X32MuteSession:
    """The mutes of one junction round (``wizard.services.MuteSession``)."""

    console: Console
    workspace: Workspace
    word: TypedWord
    originals: dict[int, bool]
    sub: tuple[int, ...]
    tops: tuple[int, ...]
    current: dict[int, bool] = field(default_factory=dict)

    def switch(self, block: JunctionBlock) -> None:
        """Put the mutes of a block in place, confirmed by readback.

        Raises:
            UnsafeWrite: A switch was refused or did not read back.
            ConsoleUnreachable: The console did not answer.
        """
        target = block_target(block, self.originals, self.sub, self.tops)
        changes = mute_changes(self.current, target)
        pending = self.workspace.pending_mutes_file
        try:
            confirmed = send_mutes(self.console, changes, pending, self.word,
                                   mute_policy(self.originals, self.sub, self.tops))  # fmt: skip
        except UnsafeWrite as error:
            # What did read back is on the console: remember it before giving up.
            self._take(target, str(error.context.get("confirmed", "")).split(", "))
            raise
        self._take(target, confirmed)

    def _take(self, target: Mapping[int, bool], confirmed: Sequence[str]) -> None:
        for matrix in target:
            if mute_path(matrix) in confirmed:
                self.current[matrix] = target[matrix]

    def restore(self) -> bool:
        """Put every original back from a fresh read; True (and no pending file) when confirmed.

        The changes are worked out from what the console has now, not from what this session
        believes it wrote: a switch that half failed must still come back.
        """
        try:
            state = read_console_state(self.console.client)
            self.current = {n: state.matrices[n].on for n in self.originals}
            send_mutes(self.console, mute_changes(self.current, self.originals),
                       self.workspace.pending_mutes_file, self.word,
                       mute_policy(self.originals, self.sub, self.tops))  # fmt: skip
        except (UnsafeWrite, ConsoleUnreachable) as error:
            logger.warning("temporary mutes not restored yet: %s", error)
            return False
        self.current = dict(self.originals)
        files.clear_pending_mutes(self.workspace)
        return True


def start_junction_mutes(
    console: Console,
    workspace: Workspace,
    created: str,
    host: str | None,
    sub: Sequence[int],
    tops: Sequence[int],
    word: TypedWord,
) -> X32MuteSession:
    """Read the switches fresh, write the pending-restore file, return the session.

    Nothing is muted yet: the junction measurement switches block by block.

    Raises:
        UnsafeWrite: The word does not match, the matrices are not distinct speaker matrices,
            or mutes of an earlier measurement are still pending.
        ConsoleUnreachable: The console did not answer.
    """
    if not word.matches():
        raise UnsafeWrite("the confirmation word does not match; nothing is muted")
    chosen = (*sub, *tops)
    if (
        not sub
        or not tops
        or len(set(chosen)) != len(chosen)
        or any(n not in MATRICES for n in chosen)
    ):
        raise UnsafeWrite(f"the junction needs distinct sub and top matrices, got {sub} / {tops}")
    if files.read_pending_mutes(workspace) is not None:
        raise UnsafeWrite("mutes of an earlier measurement are still pending", pending="yes")
    state = read_console_state(console.client)
    missing = [n for n in chosen if n not in state.matrices]
    if missing:
        raise UnsafeWrite(f"the console has no matrix {missing}")
    originals = {n: state.matrices[n].on for n in chosen}
    record = PendingMutes(created=created, mtx_on=originals, ip=host, sub=tuple(sub),
                          tops=tuple(tops))  # fmt: skip
    files.write_pending_mutes(workspace, record)
    return X32MuteSession(console, workspace, word, originals, tuple(sub), tuple(tops),
                          dict(originals))  # fmt: skip


@dataclass(frozen=True, slots=True)
class _RestorePlan:
    """The port's plan to put pending mutes back."""

    changes: tuple[Change, ...]
    originals: dict[int, bool]
    sub: tuple[int, ...]
    tops: tuple[int, ...]


def _pending_for(workspace: Workspace, host: str | None) -> PendingMutes | None:
    """The pending record, when it belongs to this console.

    Raises:
        UnsafeWrite: The record was written for another console (``other_console``).
    """
    pending = files.read_pending_mutes(workspace)
    if pending is not None and pending.ip and host and pending.ip != host:
        raise UnsafeWrite(f"the pending mutes belong to the console at {pending.ip}",
                          other_console=pending.ip)  # fmt: skip
    return pending


def plan_pending_restore(
    console: Console, workspace: Workspace, host: str | None = None
) -> WritePlan | None:
    """The writes that put pending mutes back, from a fresh read; None without pending mutes.

    A matrix whose switch already has its original value is left out; only the sub's and the
    tops' matrices of the record (or, for an older record, of a fresh role detection) are ever
    switched.

    Raises:
        UnsafeWrite: The record belongs to another console.
        ConsoleUnreachable: The console did not answer.
    """
    pending = _pending_for(workspace, host)
    if pending is None:
        return None
    state = read_console_state(console.client)
    sub, tops = pending.sub, pending.tops
    if not sub and not tops:
        try:
            roles = detect_roles(state)
            sub, tops = roles.sub, roles.mains
        except ValueError:
            sub, tops = (), ()
    speakers = {*sub, *tops}
    originals = {n: on for n, on in pending.mtx_on.items() if n in state.matrices and n in speakers}
    current = {n: state.matrices[n].on for n in originals}
    changes = tuple(mute_changes(current, originals))
    rows = tuple(
        PlannedWrite(mute_path(n), SignalRef(SourceKind.MATRIX, n), None, "on",
                     _switch_text(current[n]), _switch_text(on), current[n], on)
        for n, on in sorted(originals.items())
        if current[n] != on
    )  # fmt: skip
    return WritePlan(rows, _RestorePlan(changes, originals, tuple(sub), tuple(tops)))


def clear_settled_mutes(console: Console, workspace: Workspace, host: str | None = None) -> bool:
    """Remove the pending record when a fresh read shows every original switch; True if so.

    Nothing is written, so no word is needed.

    Raises:
        ConsoleUnreachable: The console did not answer.
    """
    plan = plan_pending_restore(console, workspace, host)
    if plan is None:
        return True
    if plan.writes:
        return False
    files.clear_pending_mutes(workspace)
    return True


def restore_pending(console: Console, workspace: Workspace, plan: WritePlan, word: TypedWord,
                    host: str | None = None) -> None:  # fmt: skip
    """Put pending mutes back as shown, after a fresh read; the record is removed after it.

    Raises:
        UnsafeWrite: The word does not match, the plan is not this port's, or the console
            changed since the plan was shown (``mismatches``).
        ConsoleUnreachable: The console did not answer.
    """
    token = plan.token
    if type(token) is not _RestorePlan:
        raise UnsafeWrite("the plan was not made by this console's write port")
    if not word.matches():
        raise UnsafeWrite("the confirmation word does not match; nothing is written")
    fresh = plan_pending_restore(console, workspace, host)
    now = fresh.token.changes if fresh is not None and type(fresh.token) is _RestorePlan else ()
    if [(c.path, c.old_value) for c in now] != [(c.path, c.old_value) for c in token.changes]:
        raise UnsafeWrite("the console changed while the list was shown", mismatches="mutes")
    send_mutes(console, token.changes, workspace.pending_mutes_file, word,
               mute_policy(token.originals, token.sub, token.tops))  # fmt: skip
    files.clear_pending_mutes(workspace)
