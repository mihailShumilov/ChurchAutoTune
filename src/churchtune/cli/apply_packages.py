"""``apply-packages`` and the undo of a package plan: the terminal side (SPEC S12, ADR 0010).

The planning, checking and sending live in ``cli.package_writes``; this module asks, shows and
turns refusals into exit codes. Output delay and polarity are advice here: they are written only
by ``ctune sub`` with ``--write-unverified``.
"""

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer

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
    refuse_while_mutes_are_pending,
    relative_to_home,
    say,
    settings_of,
)
from churchtune.cli.package_writes import (
    MuteGuard,
    fresh_inputs,
    has_crossover,
    package_undo_file,
    plan_package_undo,
    plan_package_writes,
    require_same_plan,
    send_runs,
    undo_policy,
)
from churchtune.domain.changes import Change, WriteScope
from churchtune.domain.console import ConsoleState
from churchtune.errors import ChurchTuneError, UnsafeWrite
from churchtune.mixers.x32 import params
from churchtune.mixers.x32.plan_writes import PlannedChange
from churchtune.mixers.x32.snapshot import parse_snapshot
from churchtune.report.texts import format_changes
from churchtune.safety.authorize import TypedWord, is_confirmation
from churchtune.safety.policy import WritePolicy
from churchtune.storage import files
from churchtune.storage.models import PackageModel, UndoFile
from churchtune.wizard.applying import chosen_packages, writable_packages

CONFIRMATION_WORD = "применить"
UNDO_WORD = "вернуть"
FAR_FROM_FIRST_DB = 12.0  # SPEC §9.2 levels: further from the venue's first snapshot asks first
ROLES_UNCLEAR = "ОТКАЗ: не понятно, какие матрицы саб и сателлиты"


def _show_advice(packages: Sequence[PackageModel]) -> None:
    for package in packages:
        say(f"!! {package.kind}: задержку и полярность выставьте на пульте руками:")
        for delay in package.delays:
            say(f"     выход {delay.output}: {delay.before_ms:.2f} -> {delay.after_ms:.2f} мс")
        for polarity in package.polarities:
            where = f"{polarity.path.kind} {polarity.path.number}"
            say(f"     {where}: полярность {'инверсия' if polarity.after else 'норма'}")


def _fresh(console: Console, *, allow_cut_filters: bool) -> tuple[ConsoleState, WritePolicy]:
    try:
        state, _, policy = fresh_inputs(console, allow_cut_filters=allow_cut_filters)
    except UnsafeWrite as error:
        fail(f"{ROLES_UNCLEAR}: {error}", EXIT_REFUSED)
    return state, policy


def first_faders(proposal: Path) -> dict[int, float]:
    """Matrix faders in the first snapshot of the proposal's venue (empty when there is none).

    A wizard proposal lives in ``venues/<venue>/sessions/<session>/``; the oldest session of the
    venue with a readable snapshot is its first reading.
    """
    sessions = proposal.resolve().parent.parent
    for session in sorted(p for p in sessions.glob("*") if p.is_dir()):
        try:
            path = files.read_wizard_state(session).snapshot_file
            if path is None:
                continue
            home = sessions.parent.parent.parent
            stored = files.read_snapshot(home / path)
            snapshot = parse_snapshot(stored.identity(), stored.raw, stored.taken_at)
        except (ValueError, OSError, ChurchTuneError):
            continue
        return {n: m.fader_db for n, m in snapshot.matrices.items()}
    return {}


def far_levels(planned: Sequence[PlannedChange], first: dict[int, float]) -> list[str]:
    """Faders more than 12 dB away from the venue's first reading, as lines for the person."""
    lines = []
    for item in planned:
        number = item.change.path.split("/")[2] if item.scope is WriteScope.LEVELS else ""
        if not number.isdecimal():
            continue
        before = first.get(int(number))
        after = params.norm_to_fader(float(item.change.new_value))
        if before is None or math.isinf(before) or math.isinf(after):
            continue
        if abs(after - before) > FAR_FROM_FIRST_DB:
            lines.append(f"     {item.change.path}: {before:+.1f} -> {after:+.1f} дБ")
    return lines


def _new_undo_path(settings: Settings, *, revert: bool = False) -> Path:
    return settings.workspace.new_undo_path(None, settings.now(), revert=revert)


def apply_packages_command(
    ctx: typer.Context,
    proposal: Annotated[Path, typer.Argument(help="proposal.json of a wizard session.")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the writes, write nothing.")
    ] = False,
    at_the_console: Annotated[
        bool,
        typer.Option(
            "--yes-i-am-at-the-console",
            help="Non-interactive: confirm without typing the word (SPEC §9.3). Everything else "
            "stays: fresh read, the same plan again right before sending, undo file, readback.",
        ),
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
    packages, advice = writable_packages(packages)
    _show_advice(advice)
    if not packages:
        say("Нечего записывать: не выбран ни один пакет, который пишет программа.")
        raise typer.Exit(EXIT_OK)
    with reachable_console(settings) as console:
        cut = has_crossover(packages)
        state, policy = _fresh(console, allow_cut_filters=cut)
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
        far = far_levels(planned, first_faders(proposal))
        if far:
            say(
                "!! Фейдер уходит больше чем на 12 дБ от первой настройки этого зала:\n"
                + "\n".join(far)
            )
            if at_the_console:
                fail(
                    "ОТКАЗ: такой уровень не пишется без отдельного вопроса; запустите без "
                    "--yes-i-am-at-the-console",
                    EXIT_REFUSED,
                )
            if not is_confirmation(ask("Всё равно записать такой уровень? [y/N] ")):
                say("Отменено — ничего не записано.")
                raise typer.Exit(EXIT_USER)
        if at_the_console:
            say("Подтверждено флагом --yes-i-am-at-the-console.")
            typed = CONFIRMATION_WORD
        else:
            typed = ask(f"Чтобы записать {len(changes)} изменений, введите «{CONFIRMATION_WORD}»: ")
        word = TypedWord(typed, CONFIRMATION_WORD)
        if not word.matches():
            say("Отменено — ничего не записано.")
            raise typer.Exit(EXIT_USER)
        # The person may have thought for a while: plan again from a fresh read, send only the
        # plan that was shown.
        state, policy = _fresh(console, allow_cut_filters=cut)
        try:
            require_same_plan(planned, plan_package_writes(packages, state, policy))
        except UnsafeWrite as error:
            fail(f"ОТКАЗ: {error}. Ничего не записано.", EXIT_REFUSED)
        undo_path = _new_undo_path(settings)
        files.write_json(
            undo_path,
            package_undo_file(
                settings.timestamp(), settings.venue.console_host, planned, proposal=proposal
            ),
        )  # BEFORE
        shown = relative_to_home(undo_path, settings.workspace)
        say(f"undo-файл: {undo_path}")
        try:
            guard = MuteGuard(settings.workspace, settings.venue.console_host)
            sent = send_runs(console, planned, undo_path, word, policy, guard=guard)
            results, complete = sent.results, sent.complete
        except UnsafeWrite as error:
            fail(f"ОТКАЗ: {error}. Откат: ctune undo {shown}", EXIT_REFUSED)
        except BaseException:
            say(f"!! запись прервана — проверьте пульт; откат: ctune undo {shown}")
            raise
        for result in results:
            say(f"  {'OK ' if result.ok else '!! '} {result.path}  readback {result.readback!r}")
        if sent.still_muted:
            say(
                f"!! матрицы {list(sent.still_muted)} остались выключены (Mute): включите их на "
                "пульте руками"
            )
        if not complete:
            say(f"!! не все параметры подтвердились — проверьте пульт; откат: ctune undo {shown}")
            raise typer.Exit(EXIT_READBACK)
        say(f"Готово. Откат: ctune undo {shown}. Дальше: ctune verify")
    raise typer.Exit(EXIT_OK)


def undo_packages(
    settings: Settings, console: Console, undo: UndoFile, source: Path, dry_run: bool
) -> int:
    """Put back the values of an ``apply-packages`` undo file where they were not touched since."""
    state, policy = _fresh(console, allow_cut_filters=True)
    policy = undo_policy(undo, policy)
    try:
        plan = plan_package_undo(undo, state, policy)
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
    state, policy = _fresh(console, allow_cut_filters=True)
    policy = undo_policy(undo, policy)
    try:
        require_same_plan(plan.planned, plan_package_undo(undo, state, policy).planned)
    except UnsafeWrite as error:
        fail(f"ОТКАЗ: {error}. Ничего не записано.", EXIT_REFUSED)
    revert_path = _new_undo_path(settings, revert=True)
    files.write_json(
        revert_path,
        package_undo_file(
            settings.timestamp(), settings.venue.console_host, plan.planned, reverts=source
        ),
    )
    say(f"undo-файл: {revert_path}")
    try:
        guard = MuteGuard(settings.workspace, settings.venue.console_host)
        sent = send_runs(console, plan.planned, revert_path, word, policy, guard=guard)
        complete = sent.complete
        if sent.still_muted:
            say(
                f"!! матрицы {list(sent.still_muted)} остались выключены (Mute): включите их на "
                "пульте руками"
            )
    except UnsafeWrite as error:
        fail(f"ОТКАЗ: {error}", EXIT_REFUSED)
    if not complete:
        say("!! не все параметры подтвердились — проверьте пульт")
        return EXIT_READBACK
    say("Готово.")
    return EXIT_OK
