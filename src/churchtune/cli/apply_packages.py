"""``apply-packages``: write the chosen packages of a wizard proposal (SPEC S12, ADR 0010).

The proposal file is not trusted. The console is read fresh; a package whose "before" no longer
matches is refused (re-measure). The steps come in a never-louder order, become scoped writes,
and every run of one scope is authorized on its own with the typed word, bound to the undo file
that is on disk before the first write. Output delay and polarity are advice here: they are
written only by the wizard, or by ``ctune sub`` with ``--write-unverified``.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from churchtune.cli import records
from churchtune.cli.apply import refuse_while_mutes_are_pending
from churchtune.cli.context import (
    EXIT_OK,
    EXIT_READBACK,
    EXIT_REFUSED,
    EXIT_USER,
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
from churchtune.domain.console import ConsoleState, Roles
from churchtune.errors import UnsafeWrite
from churchtune.mixers.x32.laws import X32ValueLaws
from churchtune.mixers.x32.nodes import read_console_state
from churchtune.mixers.x32.plan_writes import (
    PlannedChange,
    plan_writes,
    runs_by_scope,
    strip_of,
    wire_value,
    with_wire_value,
)
from churchtune.mixers.x32.topology import detect_roles
from churchtune.report.texts import format_changes
from churchtune.safety.authorize import TypedWord, authorize_typed
from churchtune.safety.order import order_steps
from churchtune.safety.plan import same_step
from churchtune.safety.policy import WritePolicy
from churchtune.storage import files
from churchtune.storage.models import ChangeModel, PackageModel, UndoFile
from churchtune.wizard.applying import (
    chosen_packages,
    combined_target,
    ordered_steps,
    speaker_chains,
    strip_states,
)

LAWS = X32ValueLaws()
FS_HZ = 48000.0
CONFIRMATION_WORD = "применить"
UNDO_WORD = "вернуть"
# Scopes a package plan writes; an undo file naming any other is refused.
PACKAGE_SCOPES = frozenset({WriteScope.SYSTEM_EQ, WriteScope.LEVELS})


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_policy(state: ConsoleState, roles: Roles) -> WritePolicy:
    """The policy of a package plan: the speaker matrices, the sub's fader as it is now."""
    baselines = tuple((f"/mtx/{n:02d}/mix/fader", state.matrices[n].fader_db) for n in roles.sub)
    return WritePolicy(
        LAWS,
        frozenset(roles.all),
        sub_matrices=frozenset(roles.sub),
        top_matrices=frozenset(roles.mains),
        level_baselines=baselines,
        allow_cut_filters=True,
    )


def _advice_only(packages: Sequence[PackageModel]) -> list[PackageModel]:
    """Packages with output delays or polarity: shown as advice, never written here."""
    return [p for p in packages if p.delays or p.polarities or p.delay_ms]


def _show_advice(packages: Sequence[PackageModel]) -> None:
    for package in packages:
        say(f"!! {package.kind}: задержку и полярность выставьте на пульте руками:")
        for delay in package.delays:
            say(f"     выход {delay.output}: {delay.before_ms:.2f} -> {delay.after_ms:.2f} мс")
        for polarity in package.polarities:
            where = f"{polarity.path.kind} {polarity.path.number}"
            say(f"     {where}: полярность {'инверсия' if polarity.after else 'норма'}")


def plan_package_writes(
    packages: Sequence[PackageModel], state: ConsoleState, policy: WritePolicy
) -> tuple[PlannedChange, ...]:
    """The scoped writes of the packages from a fresh read, each validated by the policy.

    Raises:
        UnsafeWrite: The console changed since the measurement, no safe order exists, or a
            write is outside the policy.
    """
    fresh = strip_states(state)
    target = combined_target(packages, fresh, LAWS)
    steps = ordered_steps(target, fresh, FS_HZ)
    planned = plan_writes(steps, fresh, LAWS)
    for item in planned:
        policy.validate(item.change.path, item.change.new_value, item.scope)
    return planned


def send_runs(
    console: Console,
    planned: Sequence[PlannedChange],
    undo_path: Path,
    word: TypedWord,
    policy: WritePolicy,
) -> tuple[list[records.WriteResultLike], bool]:
    """Send the runs of one scope each, in order; stop at the first write that fails.

    The undo file gets the results after every run, so an interruption leaves an honest record.

    Returns:
        The results so far and whether every planned write was confirmed.

    Raises:
        UnsafeWrite: A run was refused by the authorization or by the writer.
    """
    from churchtune.mixers.x32.writer import ScopedWriter  # noqa: PLC0415 - lazy by design

    results: list[records.WriteResultLike] = []
    for scope, changes in runs_by_scope(planned):
        auth = authorize_typed(
            scope, changes, undo_path, word, policy, undo_sha256=_sha256(undo_path)
        )
        sent = list(ScopedWriter(console.transport, console.client, auth).apply())
        results.extend(sent)
        undo = files.read_undo(undo_path)
        update = {"results": records.result_models(results)}
        files.write_json(undo_path, undo.model_copy(update=update))
        if len(sent) != len(changes) or not all(r.ok for r in sent):
            return results, False
    return results, True


def _undo_file(
    settings: Settings,
    planned: Sequence[PlannedChange],
    *,
    proposal: Path | None = None,
    reverts: Path | None = None,
) -> UndoFile:
    return UndoFile(
        created=settings.timestamp(),
        ip=settings.venue.console_host,
        scope=planned[0].scope,
        what="packages" if reverts is None else "undo of packages",
        proposal=None if proposal is None else str(proposal),
        reverts=None if reverts is None else str(reverts),
        changes=tuple(ChangeModel.from_domain(p.change, p.scope) for p in planned),
    )


def _new_undo_path(settings: Settings, *, revert: bool = False) -> Path:
    path, counter = settings.workspace.undo_path(None, settings.now(), revert=revert), 1
    candidate = path
    while candidate.exists():
        counter += 1
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
    return candidate


def apply_packages_command(
    ctx: typer.Context,
    proposal: Annotated[Path, typer.Argument(help="proposal.json of a wizard session.")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the writes, write nothing.")
    ] = False,
) -> None:
    """Write the chosen packages of a wizard proposal (fresh read, typed word, undo file)."""
    settings = settings_of(ctx)
    refuse_while_mutes_are_pending(settings)
    try:
        stored = files.read_packages(proposal)
        packages = chosen_packages(stored)
    except (ValueError, OSError) as error:
        fail(f"ОТКАЗ: файл предложения не читается: {error}", EXIT_REFUSED)
    except UnsafeWrite as error:
        fail(f"ОТКАЗ: {error}", EXIT_REFUSED)
    advice = _advice_only(packages)
    _show_advice(advice)
    packages = [p for p in packages if p not in advice]
    if not packages:
        say("Нечего записывать: не выбран ни один пакет, который пишет программа.")
        raise typer.Exit(EXIT_OK)
    with reachable_console(settings) as console:
        state = read_console_state(console.client)
        try:
            roles = detect_roles(state)
        except ValueError as error:
            fail(f"ОТКАЗ: не понятно, какие матрицы саб и сателлиты: {error}", EXIT_REFUSED)
        policy = package_policy(state, roles)
        try:
            planned = plan_package_writes(packages, state, policy)
        except UnsafeWrite as error:
            fail(f"ОТКАЗ: {error}. Сделайте замер заново.", EXIT_REFUSED)
        changes: list[Change] = [p.change for p in planned]
        say(format_changes(changes, "Пакеты: " + ", ".join(p.kind for p in packages)))
        if not changes:
            raise typer.Exit(EXIT_OK)
        if dry_run:
            say("--dry-run: ничего не записано.")
            raise typer.Exit(EXIT_OK)
        typed = ask(f"Чтобы записать {len(changes)} изменений, введите «{CONFIRMATION_WORD}»: ")
        word = TypedWord(typed, CONFIRMATION_WORD)
        if not word.matches():
            say("Отменено — ничего не записано.")
            raise typer.Exit(EXIT_USER)
        undo_path = _new_undo_path(settings)
        files.write_json(undo_path, _undo_file(settings, planned, proposal=proposal))  # BEFORE
        shown = relative_to_home(undo_path, settings.workspace)
        say(f"undo-файл: {undo_path}")
        try:
            results, complete = send_runs(console, planned, undo_path, word, policy)
        except UnsafeWrite as error:
            fail(f"ОТКАЗ: {error}. Откат: ctune undo {shown}", EXIT_REFUSED)
        except BaseException:
            say(f"!! запись прервана — проверьте пульт; откат: ctune undo {shown}")
            raise
        for result in results:
            say(f"  {'OK ' if result.ok else '!! '} {result.path}  readback {result.readback!r}")
        if not complete:
            say(f"!! не все параметры подтвердились — проверьте пульт; откат: ctune undo {shown}")
            raise typer.Exit(EXIT_READBACK)
        say(f"Готово. Откат: ctune undo {shown}. Дальше: ctune verify")
    raise typer.Exit(EXIT_OK)


def is_package_undo(undo: UndoFile) -> bool:
    """True for an undo file written by ``apply-packages`` (every change carries its scope)."""
    return undo.what in ("packages", "undo of packages") or any(c.scope for c in undo.changes)


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
    for stored in undo.changes:
        if stored.scope not in PACKAGE_SCOPES:
            raise UnsafeWrite(f"undo file names a write outside the packages: {stored.path}")
        strip = strip_of(stored.path)
        if strip not in fresh:
            raise UnsafeWrite(f"the console has no {strip.label()}", strip=strip.label())
        policy.validate(stored.path, stored.old_value, stored.scope)
        kind = stored.path.rsplit("/", 1)[1]
        now = wire_value(target[strip], stored.path, LAWS)
        if same_step(LAWS, kind, now, stored.old_value):
            already_old.append(stored.path)
        elif same_step(LAWS, kind, now, stored.new_value):
            target[strip] = with_wire_value(target[strip], stored.path, stored.old_value)
        else:
            by_hand.append(stored.path)
    steps = order_steps(fresh, target, speaker_chains(fresh), FS_HZ)
    planned = plan_writes(steps, fresh, LAWS)
    for item in planned:
        policy.validate(item.change.path, item.change.new_value, item.scope)
    return UndoPlan(planned, tuple(already_old), tuple(by_hand))


def undo_packages(
    settings: Settings, console: Console, undo: UndoFile, source: Path, dry_run: bool
) -> int:
    """Put back the values of an ``apply-packages`` undo file where they were not touched since."""
    state = read_console_state(console.client)
    try:
        roles = detect_roles(state)
    except ValueError as error:
        fail(f"ОТКАЗ: не понятно, какие матрицы саб и сателлиты: {error}", EXIT_REFUSED)
    try:
        plan = plan_package_undo(undo, state, package_policy(state, roles))
    except UnsafeWrite as error:
        fail(f"ОТКАЗ: повреждённый/опасный undo-файл: {error}", EXIT_REFUSED)
    say(f"Откат по {source} (параметров в файле: {len(undo.changes)})")
    if plan.already_old:
        say("Уже на старых значениях: " + ", ".join(plan.already_old))
    if plan.changed_by_hand:
        say("!! Изменены руками после записи — не трогаю: " + ", ".join(plan.changed_by_hand))
    changes = [p.change for p in plan.planned]
    say(format_changes(changes, "Вернуть"))
    if not changes or dry_run:
        if dry_run:
            say("--dry-run: ничего не записано.")
        return EXIT_OK
    word = TypedWord(
        ask(f"Чтобы вернуть {len(changes)} параметров, введите «{UNDO_WORD}»: "), UNDO_WORD
    )
    if not word.matches():
        say("Отменено — ничего не записано.")
        return EXIT_USER
    revert_path = _new_undo_path(settings, revert=True)
    files.write_json(revert_path, _undo_file(settings, plan.planned, reverts=source))
    say(f"undo-файл: {revert_path}")
    policy = package_policy(state, roles)
    try:
        _, complete = send_runs(console, plan.planned, revert_path, word, policy)
    except UnsafeWrite as error:
        fail(f"ОТКАЗ: {error}", EXIT_REFUSED)
    if not complete:
        say("!! не все параметры подтвердились — проверьте пульт")
        return EXIT_READBACK
    say("Готово.")
    return EXIT_OK
