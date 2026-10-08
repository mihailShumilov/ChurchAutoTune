"""``apply`` and ``undo``: the commands that write to the console.

Every write goes the same way: fresh read, diff, typed confirmation, undo file on disk, a
sealed authorization, the scoped writer with readback. The writer module is imported lazily,
inside the one function that sends.
"""

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer

from churchtune.cli import records
from churchtune.cli.context import (
    EXIT_OK,
    EXIT_READBACK,
    EXIT_REFUSED,
    EXIT_USER,
    FORMATS,
    Console,
    Settings,
    ask,
    fail,
    reachable_console,
    relative_to_home,
    say,
    settings_of,
)
from churchtune.domain.changes import Change, WriteScope
from churchtune.domain.console import ConsoleState
from churchtune.domain.eq import BANDS_PER_EQ, SystemEq
from churchtune.errors import UnsafeWrite
from churchtune.mixers.x32 import params
from churchtune.mixers.x32.laws import X32ValueLaws
from churchtune.mixers.x32.nodes import read_main_eq
from churchtune.report.markdown import describe_eq
from churchtune.report.texts import format_changes
from churchtune.safety.authorize import authorize, is_confirmation
from churchtune.safety.plan import plan_changes, same_step
from churchtune.safety.policy import WritePolicy, is_unverified_write
from churchtune.storage import files
from churchtune.storage.models import EqModel, UndoFile

LAWS = X32ValueLaws()
MAIN_POLICY = WritePolicy(LAWS, frozenset())
# Write order that never makes the system louder mid-way.
PHASE_QUIETER, PHASE_NEUTRAL, PHASE_LOUDER = 1, 2, 3
DELAY_SLACK_MS = 0.05  # half a display step


def send_plan(
    console: Console,
    scope: WriteScope,
    changes: Sequence[Change],
    undo_path: Path,
    confirmation: str,
    policy: WritePolicy,
) -> list[records.WriteResultLike]:
    """Authorize and send one plan; the undo file must already be on disk.

    Raises:
        UnsafeWrite: The plan was not confirmed, is outside the policy, or has no undo file.
    """
    from churchtune.mixers.x32.writer import ScopedWriter  # noqa: PLC0415 - lazy by design

    auth = authorize(scope, changes, undo_path, confirmation, policy)
    return list(ScopedWriter(console.transport, console.client, auth).apply())


def _unused(path: Path) -> Path:
    """A path that does not exist yet: an undo file is never overwritten by a later plan."""
    candidate, counter = path, 1
    while candidate.exists():
        counter += 1
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
    return candidate


def write_with_undo(
    settings: Settings,
    console: Console,
    scope: WriteScope,
    changes: Sequence[Change],
    policy: WritePolicy,
    *,
    title: str,
    question: str,
    dry_run: bool,
    undo: UndoFile,
    revert: bool = False,
) -> tuple[int, Path | None]:
    """Show the diff, ask, write the undo file, send, and record the results in the undo file.

    Returns:
        ``(exit code, undo file path)``; the path is None when nothing was written.
    """
    say(format_changes(changes, title))
    if not changes:
        return EXIT_OK, None
    unverified = [c.path for c in changes if is_unverified_write(c.path)]
    if unverified:
        say(
            "!! НЕ ПРОВЕРЕНО НА ЖЕЛЕЗЕ: запись этих параметров ни разу не делалась на реальном "
            "пульте (docs/hardware-checklist.md, строки 1 и 2). Сверьте значения на экране "
            "пульта после записи:\n" + "\n".join(f"     {path}" for path in unverified)
        )
    if dry_run:
        say("--dry-run: ничего не записано.")
        return EXIT_OK, None
    answer = ask(f"{question} [y/N] ")
    if not is_confirmation(answer):
        say("Отменено — ничего не записано.")
        return EXIT_USER, None
    undo_path = _unused(settings.workspace.undo_path(scope, settings.now(), revert=revert))
    files.write_json(undo_path, undo)  # BEFORE the first write
    say(f"undo-файл: {undo_path}")
    shown = str(relative_to_home(undo_path, settings.workspace))
    if unverified:
        shown += " --write-unverified"  # the way back is the same unverified write
    try:
        results = send_plan(console, scope, changes, undo_path, answer, policy)
    except UnsafeWrite as error:
        fail(f"ОТКАЗ: {error}", EXIT_REFUSED)
    except BaseException:
        # Interrupted or broken mid-plan: some writes may be on the console already.
        say(f"!! запись прервана — проверьте пульт; откат: ctune undo {shown}")
        raise
    for change, result in zip(changes, results, strict=False):
        mark = "OK " if result.ok else "!! "
        say(
            f"  {mark} {result.path} = {change.new_display:<12} readback {result.readback!r}"
            f"  |  {result.node}"
        )
    files.write_json(undo_path, undo.model_copy(update={"results": records.result_models(results)}))
    complete = len(results) == len(changes) and all(r.ok for r in results)
    if not complete:
        say(f"!! не все параметры подтвердились — проверьте пульт; откат: ctune undo {shown}")
        return EXIT_READBACK, undo_path
    say(f"Готово. Откат: ctune undo {shown}")
    return EXIT_OK, undo_path


def refuse_while_mutes_are_pending(settings: Settings) -> None:
    """Leave with a refusal while a sub measurement has not given the matrix mutes back."""
    pending = settings.workspace.pending_mutes_file
    if pending.exists():
        fail(
            f"!! Найден {pending.name}: прошлый замер саба не вернул мьюты. Сначала: "
            "ctune sub restore",
            EXIT_REFUSED,
        )


def apply_command(
    ctx: typer.Context,
    proposal: Annotated[Path, typer.Argument(help="Proposal file from `ctune propose`.")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the diff, write nothing.")
    ] = False,
) -> None:
    """Write a proposal's Main LR EQ to the console (diff, confirmation, undo file, readback)."""
    settings = settings_of(ctx)
    refuse_while_mutes_are_pending(settings)
    stored = files.read_proposal(proposal)
    target = records.proposal_from_file(stored)[0].to_eq()
    with reachable_console(settings) as console:
        current = read_main_eq(console.client)
        say("Сейчас на пульте:\n" + describe_eq(current, FORMATS, "Main LR EQ"))
        if stored.eq_at_measurement != EqModel.from_domain(current):
            say(
                "!! ВНИМАНИЕ: EQ на пульте отличается от EQ во время замера. Предложение "
                "рассчитано для того состояния; diff ниже включает все отличия."
            )
        try:
            changes = plan_changes(current, target, MAIN_POLICY)
        except UnsafeWrite as error:
            fail(f"ОТКАЗ: {error}", EXIT_REFUSED)
        undo = UndoFile(
            created=settings.timestamp(),
            ip=settings.venue.console_host,
            scope=WriteScope.MAIN,
            proposal=str(proposal),
            eq_before=EqModel.from_domain(current),
            changes=records.change_models(changes),
        )
        code, undo_path = write_with_undo(
            settings, console, WriteScope.MAIN, changes, MAIN_POLICY,
            title="Изменения (только /main/st/eq/*)",
            question=f"Записать {len(changes)} изменений в пульт {settings.venue.console_host}?",
            dry_run=dry_run, undo=undo,
        )  # fmt: skip
        if undo_path is not None:
            after = read_main_eq(console.client)
            stored_undo = files.read_undo(undo_path)
            files.write_json(
                undo_path, stored_undo.model_copy(update={"eq_after": EqModel.from_domain(after)})
            )
            say("Фактически установлено (readback):\n" + describe_eq(after, FORMATS, "Main LR EQ"))
            if code == EXIT_OK:
                say("Дальше: ctune verify")
    raise typer.Exit(code)


def _restored_eq(current: SystemEq, undo: UndoFile) -> SystemEq:
    """The EQ with the old values of an undo file put back; everything else as it is now.

    Raises:
        ValueError: The undo file names a path or a value that is not a Main LR EQ parameter.
    """
    eq = current
    for change in undo.changes:
        parts = change.path.split("/")
        value = change.old_value
        if parts[:4] != ["", "main", "st", "eq"] or len(parts) not in (5, 6):
            raise ValueError(f"bad path in undo file: {change.path}")
        if len(parts) == 5:  # noqa: PLR2004 - /main/st/eq/on
            if parts[4] != "on" or isinstance(value, float) or value not in (0, 1):
                raise ValueError(f"bad on value in undo file: {change.path}={value!r}")
            eq = eq.with_on(bool(value))
            continue
        number, kind = parts[4], parts[5]
        if number not in {str(n) for n in range(1, BANDS_PER_EQ + 1)}:
            raise ValueError(f"bad path in undo file: {change.path}")
        band = eq.band(int(number))
        if kind == "type":
            if isinstance(value, float) or not 0 <= value < len(params.EQ_TYPES):
                raise ValueError(f"bad type index in undo file: {value!r}")
            eq = eq.with_band(replace(band, type=params.EQ_TYPES[value]))
            continue
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"bad normalized value in undo file: {change.path}={value!r}")
        if kind == "f":
            eq = eq.with_band(replace(band, freq_hz=params.norm_to_freq(float(value))))
        elif kind == "g":
            eq = eq.with_band(replace(band, gain_db=params.norm_to_gain(float(value))))
        elif kind == "q":
            eq = eq.with_band(replace(band, q=params.norm_to_q(float(value))))
        else:
            raise ValueError(f"bad path in undo file: {change.path}")
    return eq


def safety_phase(change: Change) -> int:
    """1 = gets quieter (gain or fader down, mute), 2 = filters, delays, polarity, 3 = louder.

    An output delay is switched off before its time is touched and switched on after it, so a
    stored time never reaches the signal path half-way through a plan.
    """
    kind = change.path.rsplit("/", 1)[1]
    if kind in ("g", "fader"):
        return PHASE_QUIETER if float(change.new_value) < float(change.old_value) else PHASE_LOUDER
    if kind == "on" and ("/mix/" in change.path or "/delay/" in change.path):
        return PHASE_QUIETER if int(change.new_value) == 0 else PHASE_LOUDER
    return PHASE_NEUTRAL


def _undo_main(
    settings: Settings, console: Console, undo: UndoFile, source: Path, dry_run: bool
) -> int:
    current = read_main_eq(console.client)
    try:
        target = _restored_eq(current, undo)
    except (ValueError, IndexError) as error:
        fail(f"ОТКАЗ: повреждённый undo-файл: {error}", EXIT_REFUSED)
    try:
        changes = plan_changes(current, target, MAIN_POLICY)
    except UnsafeWrite as error:
        fail(f"ОТКАЗ: {error}", EXIT_REFUSED)
    say(f"Откат по {source} (параметров в файле: {len(undo.changes)})")
    revert = UndoFile(
        created=settings.timestamp(),
        scope=WriteScope.MAIN,
        reverts=str(source),
        eq_before=EqModel.from_domain(current),
        changes=records.change_models(changes),
    )
    code, written = write_with_undo(
        settings, console, WriteScope.MAIN, changes, MAIN_POLICY,
        title="Вернуть", question=f"Вернуть {len(changes)} параметров?", dry_run=dry_run,
        undo=revert, revert=True,
    )  # fmt: skip
    if written is not None:
        say(
            "Сейчас на пульте:\n" + describe_eq(read_main_eq(console.client), FORMATS, "Main LR EQ")
        )
    return code


def with_fresh_polarity(
    console: Console, state: ConsoleState, matrices: Sequence[int]
) -> ConsoleState:
    """The state with the polarity of ``matrices`` read again, parameter by parameter.

    The node read of a matrix counts a lost reply as "not inverted"; a toggle built on that
    would write the wrong direction and an undo would then really flip the speaker. So a plan
    that toggles a polarity starts from a direct read, and no answer is a refusal.
    """
    fresh = dict(state.matrices)
    for matrix in matrices:
        path = f"/mtx/{matrix:02d}/preamp/invert"
        value = console.client.query(path)
        if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 1):
            fail(f"ОТКАЗ: не удалось прочитать {path} — ничего не записано", EXIT_REFUSED)
        fresh[matrix] = replace(fresh[matrix], invert=bool(value))
    return replace(state, matrices=fresh)


def _matrix_of(path: str) -> int | None:
    parts = path.split("/")
    if len(parts) > 2 and parts[1] == "mtx" and parts[2].isdecimal():  # noqa: PLR2004
        return int(parts[2])
    return None


def _refuse_switching_on_a_long_delay(
    console: Console, changes: Sequence[Change], policy: WritePolicy
) -> None:
    """Refuse a plan that switches an output delay on with a time above the limit.

    The limit is checked on every time that is written; a plan that only flips the switch would
    otherwise bring in whatever time the console has stored.
    """
    planned = {c.path: c.new_value for c in changes}
    for change in changes:
        if not change.path.endswith("/delay/on") or int(change.new_value) != 1:
            continue
        time_path = change.path.removesuffix("on") + "time"
        time_norm = planned[time_path] if time_path in planned else console.client.query(time_path)
        if not isinstance(time_norm, int | float):
            fail(f"ОТКАЗ: не удалось прочитать {time_path}", EXIT_REFUSED)
        delay_ms = LAWS.delay_ms(float(time_norm))
        if delay_ms > policy.limits.sub_max_delay_ms + DELAY_SLACK_MS:
            fail(
                f"ОТКАЗ: {change.path} включил бы задержку {delay_ms:.1f} мс — больше лимита "
                f"{policy.limits.sub_max_delay_ms:.0f} мс. Проверьте задержку на пульте руками.",
                EXIT_REFUSED,
            )


def _undo_sub(
    settings: Settings,
    console: Console,
    undo: UndoFile,
    source: Path,
    dry_run: bool,
    write_unverified: bool,
) -> int:
    """Restore exactly the listed parameters of a sub-scope plan where they still differ."""
    matrices = frozenset(n for n in (_matrix_of(c.path) for c in undo.changes) if n is not None)
    unverified = [c for c in undo.changes if is_unverified_write(c.path)]
    if unverified and not write_unverified:
        say(
            "ОТКАЗ: в undo-файле есть задержка выхода или полярность — их запись не проверена "
            "на железе. Верните на пульте руками:"
        )
        for stored in unverified:
            say(f"     {stored.path} -> {stored.old_display}")
        fail(
            "Либо повторите с --write-unverified (осознанная запись непроверенных параметров).",
            EXIT_REFUSED,
        )
    # The gate is lifted only by the explicit flag and only for a file that needs it.
    policy = WritePolicy(LAWS, matrices, allow_unverified_writes=bool(unverified))
    # Check the whole file against the policy before the console is asked anything: a path
    # that may not be written is not even queried.
    try:
        for stored in undo.changes:
            policy.validate(stored.path, stored.old_value, WriteScope.SUB)
    except UnsafeWrite as error:
        fail(f"ОТКАЗ: повреждённый/опасный undo-файл: {error}", EXIT_REFUSED)
    changes: list[Change] = []
    for stored in undo.changes:
        current = console.client.query(stored.path)
        if current is None or isinstance(current, str | bytes):
            fail(f"ОТКАЗ: не удалось прочитать {stored.path}", EXIT_REFUSED)
        kind = stored.path.rsplit("/", 1)[1]
        try:
            same = same_step(LAWS, kind, current, stored.old_value)
        except ValueError:
            fail(f"ОТКАЗ: повреждённый/опасный undo-файл: {stored.path}", EXIT_REFUSED)
        if not same:
            shown = f"{current:.4g}" if isinstance(current, float) else str(current)
            changes.append(
                Change(stored.path, current, stored.old_value, shown, stored.old_display)
            )
    changes.sort(key=safety_phase)
    try:
        for change in changes:
            policy.validate(change.path, change.new_value, WriteScope.SUB)
    except UnsafeWrite as error:
        fail(f"ОТКАЗ: повреждённый/опасный undo-файл: {error}", EXIT_REFUSED)
    _refuse_switching_on_a_long_delay(console, changes, policy)
    say(f"Откат по {source} ({undo.what or 'sub'}, параметров в файле: {len(undo.changes)})")
    revert = UndoFile(
        created=settings.timestamp(),
        scope=WriteScope.SUB,
        what=f"undo of {undo.what or 'sub'}",
        reverts=str(source),
        changes=records.change_models(changes),
    )
    code, _ = write_with_undo(
        settings, console, WriteScope.SUB, changes, policy,
        title="Вернуть", question=f"Вернуть {len(changes)} параметров?", dry_run=dry_run,
        undo=revert, revert=True,
    )  # fmt: skip
    return code


def undo_command(
    ctx: typer.Context,
    undo_file: Annotated[Path, typer.Argument(help="Undo file written by an earlier apply.")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the diff, write nothing.")
    ] = False,
    write_unverified: Annotated[
        bool,
        typer.Option(
            "--write-unverified",
            help="Also put back output delay and polarity, although these writes were never "
            "verified on hardware. Without it such a file is refused.",
        ),
    ] = False,
) -> None:
    """Put back the values recorded in an undo file (fresh read, diff, confirmation)."""
    settings = settings_of(ctx)
    refuse_while_mutes_are_pending(settings)
    try:
        undo = files.read_undo(undo_file)
    except (ValueError, OSError) as error:
        fail(f"ОТКАЗ: повреждённый undo-файл: {error}", EXIT_REFUSED)
    host = settings.venue.console_host
    if undo.ip and host and undo.ip != host:
        question = f"undo-файл записан для пульта {undo.ip}, а сейчас {host}. Продолжить? [y/N] "
        if not is_confirmation(ask(question)):
            say("Отменено — ничего не записано.")
            raise typer.Exit(EXIT_USER)
    with reachable_console(settings) as console:
        if undo.scope is WriteScope.SUB:
            code = _undo_sub(settings, console, undo, undo_file, dry_run, write_unverified)
        else:
            code = _undo_main(settings, console, undo, undo_file, dry_run)
    raise typer.Exit(code)
