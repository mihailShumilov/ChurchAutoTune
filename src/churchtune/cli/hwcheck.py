"""``hwcheck``: the two writes that were never verified on hardware, one parameter at a time.

For rows 1 and 2 of docs/hardware-checklist.md. A human at the console sets a known delay on one
output whose delay is off, or flips the polarity of the sub matrix, compares the console screen
with the diff, and puts the value back with ``undo``. Running the command is the explicit consent
to an unverified write (ADR 0005); everything else is the usual way: fresh read, diff,
confirmation, undo file, readback.
"""

from collections.abc import Sequence
from dataclasses import replace
from typing import Annotated

import typer

from churchtune.cli import records
from churchtune.cli.apply import (
    LAWS,
    with_fresh_polarity,
    write_with_undo,
)
from churchtune.cli.context import (
    EXIT_OK,
    EXIT_REFUSED,
    Console,
    fail,
    reachable_console,
    refuse_while_mutes_are_pending,
    say,
    settings_of,
)
from churchtune.domain.changes import Change, WriteScope
from churchtune.domain.console import ConsoleState
from churchtune.errors import UnsafeWrite
from churchtune.mixers.x32.nodes import read_console_state
from churchtune.mixers.x32.topology import detect_roles
from churchtune.safety.policy import WriteLimits, WritePolicy
from churchtune.safety.sub_plan import delay_check_changes, polarity_check_changes
from churchtune.storage.models import UndoFile

ACTIONS = ("delay", "polarity")
DEFAULT_DELAY_MS = 1.0
# A check needs a small, clearly visible value, never a long delay.
MAX_CHECK_DELAY_MS = 10.0
LIMITS = replace(WriteLimits(), sub_max_delay_ms=MAX_CHECK_DELAY_MS)
DELAY_ROW, POLARITY_ROW = 1, 2


def _delay_plan(state: ConsoleState, output: int, ms: float) -> tuple[list[Change], WritePolicy]:
    policy = WritePolicy(LAWS, frozenset(), LIMITS, allow_unverified_writes=True)
    changes = delay_check_changes(state, output, ms, policy)
    allowed = {f"/outputs/main/{output:02d}/delay/time", f"/outputs/main/{output:02d}/delay/on"}
    return _only(changes, allowed), policy


def _polarity_plan(
    console: Console, state: ConsoleState, matrix: int
) -> tuple[list[Change], WritePolicy]:
    try:
        sub_matrices = detect_roles(state).sub
    except ValueError as error:
        raise UnsafeWrite(f"cannot tell which matrix is the sub: {error}") from error
    if matrix not in sub_matrices:
        # SPEC section 9.2 allows a polarity write on the sub path only.
        raise UnsafeWrite(
            f"matrix {matrix} is not the sub (sub: {list(sub_matrices)}); "
            "the polarity check runs on the sub matrix only",
            matrix=matrix,
        )
    state = with_fresh_polarity(console, state, [matrix])
    policy = WritePolicy(LAWS, frozenset({matrix}), LIMITS, allow_unverified_writes=True)
    changes = polarity_check_changes(state, matrix, policy)
    return _only(changes, {f"/mtx/{matrix:02d}/preamp/invert"}), policy


def _only(changes: Sequence[Change], allowed: set[str]) -> list[Change]:
    """The plan, after checking that it touches nothing but the parameters of this check."""
    foreign = [c.path for c in changes if c.path not in allowed]
    if foreign:
        raise UnsafeWrite(f"the check would write outside its parameters: {foreign}")
    return list(changes)


def hwcheck_command(
    ctx: typer.Context,
    action: Annotated[str, typer.Argument(help="delay or polarity.")],
    output: Annotated[
        int | None, typer.Option(help="Physical output for the delay check, e.g. 6.")
    ] = None,
    ms: Annotated[float | None, typer.Option(help="Delay time to set, ms (default 1.0).")] = None,
    matrix: Annotated[
        int | None, typer.Option(help="Sub matrix for the polarity check, e.g. 3.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the diff, write nothing.")
    ] = False,
) -> None:
    """Hardware check of an unverified write: one output delay, or the sub matrix polarity."""
    settings = settings_of(ctx)
    if action not in ACTIONS:
        fail(f"действие: {', '.join(ACTIONS)}")
    if action == "delay" and (output is None or matrix is not None):
        fail("проверка задержки: ctune hwcheck delay --output 6 [--ms 1.0] (без --matrix)")
    if action == "polarity" and (matrix is None or output is not None or ms is not None):
        fail("проверка полярности: ctune hwcheck polarity --matrix 3 (без --output и --ms)")
    refuse_while_mutes_are_pending(settings)
    with reachable_console(settings) as console:
        state = read_console_state(console.client)
        try:
            if output is not None:
                wanted_ms = DEFAULT_DELAY_MS if ms is None else ms
                changes, policy = _delay_plan(state, output, wanted_ms)
                written_ms = LAWS.delay_ms(LAWS.delay_wire(wanted_ms))
                row = DELAY_ROW
                what = f"hwcheck delay out{output} {written_ms:.1f} ms"
                effect = (
                    f"задержка выхода {output} ВКЛЮЧИТСЯ: звук этого выхода сдвинется, стык "
                    "саб/топы изменится, возможен щелчок."
                )
            elif matrix is not None:
                changes, policy = _polarity_plan(console, state, matrix)
                row = POLARITY_ROW
                what = f"hwcheck polarity mtx{matrix}"
                effect = (
                    f"полярность Mtx{matrix} ПЕРЕВЕРНЁТСЯ: бас на стыке с топами изменится; до "
                    "отката система не в рабочем состоянии."
                )
            else:  # pragma: no cover - excluded by the argument checks above
                fail("укажите --output или --matrix")
        except UnsafeWrite as error:
            fail(f"ОТКАЗ: {error}", EXIT_REFUSED)
        say(
            f"Проверка на железе непроверенной записи (docs/hardware-checklist.md, строка {row})."
            f"\nБудет слышно: {effect}\nДелайте в пустом зале и не уходите, не выполнив откат."
        )
        undo = UndoFile(
            created=settings.timestamp(),
            ip=settings.venue.console_host,
            scope=WriteScope.SUB,
            what=what,
            changes=records.change_models(changes),
        )
        code, undo_path = write_with_undo(
            settings, console, WriteScope.SUB, changes, policy, title="Проверка записи",
            question=f"{what}: записать {len(changes)} изменений в пульт "
            f"{settings.venue.console_host}?",
            dry_run=dry_run, undo=undo,
        )  # fmt: skip
        if undo_path is not None and code == EXIT_OK:
            say(
                "Теперь сверьте значение на экране пульта с колонкой «станет», выполните откат "
                "командой выше и убедитесь, что на экране прежнее значение. Результат запишите в "
                f"docs/hardware-checklist.md, строка {row}."
            )
        elif undo_path is not None:
            say(
                "Запись не подтвердилась. Посмотрите на экран пульта, что там сейчас, выполните "
                f"откат командой выше и запишите в docs/hardware-checklist.md, строка {row}: "
                "«не подтвердилось» и что показал пульт."
            )
    raise typer.Exit(code)
