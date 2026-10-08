"""``sub``: crossover and time alignment between the sub and the tops.

Actions: ``show`` (read-only), ``crossover``, ``measure`` (temporary matrix mutes, always
restored), ``align`` and ``restore`` (put back mutes after a failed measurement).
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

import numpy as np
import typer

from churchtune.cli import records
from churchtune.cli.apply import LAWS, send_plan, write_with_undo
from churchtune.cli.context import (
    EQ_GRID,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_USER,
    FORMATS,
    FS_HZ,
    Console,
    Settings,
    ask,
    fail,
    open_audio,
    reachable_console,
    say,
    settings_of,
)
from churchtune.cli.measure import SWEEP, valid_point
from churchtune.design.alignment import JunctionPoint, analyze_alignment, recommend_level
from churchtune.design.crossover import CROSSOVER_SLOPES, plan_crossover
from churchtune.domain.changes import Change, WriteScope
from churchtune.domain.console import ConsoleState, Roles
from churchtune.domain.eq import EqType
from churchtune.domain.levels import peak_dbfs
from churchtune.errors import ChurchTuneError, LevelError, MeasurementAborted, UnsafeWrite
from churchtune.measure.junction import block_stimulus, mute_plan, split_irs
from churchtune.measure.stimulus import check_level
from churchtune.mixers.x32 import params
from churchtune.mixers.x32.nodes import read_console_state
from churchtune.mixers.x32.topology import detect_roles
from churchtune.report import plots
from churchtune.report.markdown import alignment_summary
from churchtune.report.texts import crossover_note_text
from churchtune.safety.authorize import is_confirmation
from churchtune.safety.policy import WritePolicy
from churchtune.safety.sub_plan import alignment_changes, crossover_changes
from churchtune.storage import files
from churchtune.storage.models import (
    JunctionMeta,
    JunctionState,
    PendingMutes,
    RolesModel,
    UndoFile,
)

ACTIONS = ("show", "crossover", "measure", "align", "restore")
JUNCTION_LEVEL_ABOVE_SWEEP_DB = 7.0
# The mutes switch this long after a block boundary, inside the silent pre-roll.
MUTE_SWITCH_AFTER_S = 0.1
QUIET_BLOCK_DBFS = -45.0
CLIP_DBFS = -0.5
MISMATCH_WARNING_DB = 3.0
MIN_ALIGNMENT_GAIN_DB = 1.0


def _numbers(text: str | None) -> list[int] | None:
    return [int(part) for part in text.split(",")] if text else None


def _policy(roles: Roles, allow_unverified: bool) -> WritePolicy:
    return WritePolicy(LAWS, frozenset(roles.all), allow_unverified_writes=allow_unverified)


def _describe(state: ConsoleState, roles: Roles) -> str:
    lines = []
    for role, matrices in (("sub", roles.sub), ("mains", roles.mains), ("others", roles.others)):
        for n in matrices:
            m = state.matrices[n]
            outs = ", ".join(
                f"out {o} (delay {'ON' if state.delays[o].on else 'OFF'} "
                f"{state.delays[o].delay_ms:.1f} мс)"
                for o in m.outputs
                if o in state.delays
            )
            lines.append(
                f"Mtx{n} «{m.name}» [{role}] {'ON' if m.on else 'MUTE'} fader "
                f"{params.fmt_fader(m.fader_db)} polarity {'INV' if m.invert else 'norm'} -> {outs}"
            )
            lines += [f"    band {b.band}: {FORMATS.band(b)}" for b in m.eq.bands]
    return "\n".join(lines)


def _write(
    settings: Settings,
    console: Console,
    changes: list[Change],
    policy: WritePolicy,
    *,
    title: str,
    what: str,
    dry_run: bool,
) -> int:
    undo = UndoFile(
        created=settings.timestamp(),
        ip=settings.venue.console_host,
        scope=WriteScope.SUB,
        what=what,
        changes=records.change_models(changes),
    )
    host = settings.venue.console_host
    code, _ = write_with_undo(
        settings, console, WriteScope.SUB, changes, policy, title=title,
        question=f"{what}: записать {len(changes)} изменений в пульт {host}?",
        dry_run=dry_run, undo=undo,
    )  # fmt: skip
    return code


def _mute_changes(console: Console, target: Mapping[int, bool]) -> list[Change]:
    """Writes that bring the matrix switches to ``target``, from a fresh read of each."""
    changes = []
    for matrix, on in target.items():
        path = f"/mtx/{matrix:02d}/mix/on"
        current = console.client.query(path)
        if not isinstance(current, int):
            raise ChurchTuneError(f"пульт не ответил на чтение {path}")
        if current != int(on):
            changes.append(
                Change(path, current, int(on), "ON" if current else "MUTE", "ON" if on else "MUTE")
            )
    return changes


def _restore(settings: Settings, console: Console, state: ConsoleState, dry_run: bool) -> int:
    pending = files.read_pending_mutes(settings.workspace)
    if pending is None:
        say("Нечего восстанавливать.")
        return EXIT_OK
    changes = [
        Change(
            f"/mtx/{n:02d}/mix/on",
            int(state.matrices[n].on),
            int(on),
            "ON" if state.matrices[n].on else "MUTE",
            "ON" if on else "MUTE",
        )
        for n, on in pending.mtx_on.items()
        if state.matrices[n].on != on
    ]
    policy = WritePolicy(LAWS, frozenset(pending.mtx_on))
    code = _write(
        settings, console, changes, policy, title="Вернуть мьюты матриц", what="restore",
        dry_run=dry_run,
    )  # fmt: skip
    if code == EXIT_OK and not dry_run:
        files.clear_pending_mutes(settings.workspace)
    return code


def _crossover(
    settings: Settings,
    console: Console,
    state: ConsoleState,
    roles: Roles,
    fc: float,
    slope: EqType,
    dry_run: bool,
) -> int:
    off = [n for n in (*roles.sub, *roles.mains) if not state.matrices[n].eq.on]
    if off:
        fail(
            f"EQ матриц {off} выключен — фильтры не будут работать. Включите EQ на пульте и "
            "повторите."
        )
    plan = plan_crossover(state, roles, EQ_GRID, FS_HZ, fc_hz=fc, slope=slope)
    say(
        f"Кроссовер {slope} {FORMATS.freq(plan.fc_hz)} Гц: саб = Mtx{list(roles.sub)}, "
        f"сателлиты = Mtx{list(roles.mains)}"
    )
    for note in plan.notes:
        say("  " + crossover_note_text(note, FORMATS))
    policy = _policy(roles, allow_unverified=False)
    try:
        changes = crossover_changes(state, plan.targets, policy)
    except UnsafeWrite as error:
        fail(f"ОТКАЗ: {error}", EXIT_REFUSED)
    say(
        "Порядок записи: сначала убрать подъёмы, затем включить фильтры, потом снять старые "
        "вырезы — без громких скачков."
    )
    return _write(
        settings, console, changes, policy, title="Изменения кроссовера",
        what=f"crossover {slope} {fc:.0f}Hz", dry_run=dry_run,
    )  # fmt: skip


def _measure(
    settings: Settings,
    console: Console,
    state: ConsoleState,
    roles: Roles,
    point: str | None,
    session: str | None,
    level: float,
    dry_run: bool,
) -> int:
    workspace = settings.workspace
    if not valid_point(point) or point is None:
        fail("укажите точку: ctune sub measure p1-center")
    try:
        check_level(level)
    except LevelError as error:
        fail(f"ОТКАЗ: {error}")
    if dry_run:
        fail(
            "measure всегда временно мьютит матрицы; --dry-run здесь не применим "
            "(используйте sub show)"
        )
    plan = mute_plan(state, roles)
    muted_for_sub = [n for n, on in plan.sub_only.items() if not on]
    muted_for_tops = [n for n, on in plan.tops_only.items() if not on]
    say(
        f"Замер стыка в точке {point}: 3 свипа подряд в одном потоке:\n"
        f"  A — только саб (временно MUTE: Mtx{muted_for_sub}),\n"
        f"  B — только сателлиты (MUTE: Mtx{muted_for_tops}),\n"
        "  C — всё как было. Мьюты вернутся автоматически, даже при Ctrl+C."
    )
    answer = ask("Временно мьютить матрицы на ~30 с? [y/N] ")
    if not is_confirmation(answer):
        say("Отменено.")
        return EXIT_USER
    backend = open_audio(settings)
    stimulus = block_stimulus(FS_HZ, SWEEP, level)
    policy = WritePolicy(LAWS, frozenset(roles.all))

    def set_mutes(target: Mapping[int, bool], label: str) -> None:
        changes = _mute_changes(console, target)
        results = send_plan(
            console, WriteScope.SUB, changes, workspace.pending_mutes_file, answer, policy
        )
        if len(results) != len(changes) or not all(r.ok for r in results):
            raise ChurchTuneError(f"мьюты ({label}) не подтвердились: {results}")

    # Read the mute states again right now: the operator may have changed something while the
    # question was open.
    original: dict[int, bool] = {}
    for matrix in plan.original:
        value = console.client.query(f"/mtx/{matrix:02d}/mix/on")
        if not isinstance(value, int):
            fail("пульт не ответил на чтение мьютов — замер отменён, ничего не записано")
        original[matrix] = bool(value)
    sub_only = {n: on and (n in roles.sub) for n, on in original.items()}
    tops_only = {n: on and (n in roles.mains) for n, on in original.items()}
    files.write_pending_mutes(
        workspace, PendingMutes(created=settings.timestamp(), mtx_on=original)
    )  # BEFORE the first mute
    restored = False
    switch_after = int(MUTE_SWITCH_AFTER_S * FS_HZ)
    try:
        set_mutes(sub_only, "A")
        events = [
            (stimulus.block_len + switch_after, lambda: set_mutes(tops_only, "B")),
            (2 * stimulus.block_len + switch_after, lambda: set_mutes(original, "C")),
        ]
        say("  играю A (саб) -> B (сателлиты) -> C (вместе) ...")
        recording, flags = backend.playrec(stimulus.out, events=events)
        set_mutes(original, "restore")
        restored = True
    except MeasurementAborted as error:
        say(str(error))
        return EXIT_INTERRUPTED
    finally:
        if not restored:
            try:
                set_mutes(original, "restore")
                restored = True
            except BaseException as error:
                say(
                    f"!! НЕ УДАЛОСЬ вернуть мьюты автоматически: {error}\n"
                    "   Выполните: ctune sub restore"
                )
        if restored:
            files.clear_pending_mutes(workspace)
    rec = np.asarray(recording, dtype=np.float64)
    sub_ir, tops_ir, both_ir = split_irs(rec, stimulus, FS_HZ, SWEEP)[:3]
    length = stimulus.block_len
    peaks = [peak_dbfs(rec[i * length : (i + 1) * length]) for i in range(3)]
    fader_db = state.matrices[roles.sub[0]].fader_db
    meta = JunctionMeta(
        point=point,
        time=settings.timestamp(),
        level_dbfs=level,
        peaks_dbfs=(round(peaks[0], 1), round(peaks[1], 1), round(peaks[2], 1)),
        xruns=tuple(flags),
        roles=RolesModel(sub=roles.sub, mains=roles.mains, others=roles.others),
        state=JunctionState(sub_fader=None if np.isinf(fader_db) else fader_db),
    )
    directory = files.save_junction_point(
        _session_dir(settings, session), meta, (sub_ir, tops_ir, both_ir), FS_HZ
    )
    clipping = " !! КЛИППИНГ" if max(peaks) > CLIP_DBFS else ""
    say(f"  пики A/B/C: {peaks[0]:.1f} / {peaks[1]:.1f} / {peaks[2]:.1f} dBFS{clipping}")
    if min(peaks) < QUIET_BLOCK_DBFS:
        say("  !! один из блоков очень тихий — проверьте, что саб/сателлиты реально звучали")
    say(
        f"Сохранено: {directory}\nДальше: ещё 1–2 точки (ctune sub measure p4-mid), затем "
        "ctune sub align"
    )
    return EXIT_OK


def _session_dir(settings: Settings, name: str | None) -> Path:
    return settings.workspace.measurements / (name or f"sub-{settings.now().date().isoformat()}")


def _align(
    settings: Settings,
    console: Console,
    state: ConsoleState,
    roles: Roles,
    *,
    fc: float,
    session: str | None,
    only_points: str | None,
    level_match: bool,
    only_level: bool,
    write_unverified: bool,
    dry_run: bool,
) -> int:
    session_dir = _session_dir(settings, session)
    if not session_dir.exists():
        fail(f"нет замеров стыка в {session_dir}. Сначала: ctune sub measure p1-center")
    stored = files.load_junction_points(session_dir)
    if only_points:
        stored = {k: v for k, v in stored.items() if k in only_points.split(",")}
    if not stored:
        fail("нет точек для анализа")
    result = analyze_alignment(
        {name: JunctionPoint(p.sub, p.tops, p.both) for name, p in stored.items()}, fc, FS_HZ
    )
    out = settings.workspace.results / session_dir.name
    out.mkdir(parents=True, exist_ok=True)
    plots.plot_alignment(result, out / "sub_align.png")
    summary = alignment_summary(result)
    (out / "sub_align.txt").write_text(summary + "\n", encoding="utf-8")
    say(summary)
    say(f"График: {out / 'sub_align.png'}")
    suspicious = [n for n, p in result.points.items() if p.check_mismatch_db > MISMATCH_WARNING_DB]
    if suspicious:
        say(
            f"!! в точках {suspicious} блок «вместе» не совпал с суммой A+B (> 3 дБ) — возможно, "
            "мьюты не сработали или что-то менялось во время замера. Перемерьте эти точки."
        )
    fader_change = recommend_level(result) if (level_match or only_level) else 0.0
    delay_ms, polarity = result.delay_ms, result.polarity
    if only_level:
        delay_ms, polarity = 0.0, 1
        say(
            "--only-level: задержку/полярность не трогаю, только уровень саба "
            f"{fader_change:+.1f} дБ"
        )
    difference = result.sub_minus_tops_at_fc_db
    if not (level_match or only_level) and abs(difference) >= 1:
        say(f"(уровень саба можно подровнять на {-difference:+.1f} дБ — добавьте --level-match)")
    # Decide what is written BEFORE building the plan: with a gain below 1 dB the delay and the
    # polarity stay as they are.
    if not only_level and result.gain_db < MIN_ALIGNMENT_GAIN_DB:
        say(
            f"Выигрыш от задержки/полярности {result.gain_db:.1f} дБ < 1 дБ — стык уже в "
            "фазе, задержку НЕ меняю."
        )
        if not fader_change:
            return EXIT_OK
        delay_ms, polarity = 0.0, 1
    policy = _policy(roles, write_unverified)
    try:
        changes = alignment_changes(
            state, roles, policy, delay_ms=delay_ms, polarity=polarity, fader_change_db=fader_change
        )
    except UnsafeWrite as error:
        say(f"ОТКАЗ записи: {error}")
        say(
            f"Запись задержки и полярности не проверена на железе. Выставьте на пульте руками: "
            f"к задержке выходов {'саба' if delay_ms > 0 else 'сателлитов'} ДОБАВИТЬ "
            f"{abs(delay_ms):.1f} мс (к уже выставленной), "
            f"полярность саба {'НЕ менять' if polarity == 1 else 'ИНВЕРТИРОВАТЬ'}. "
            "Уровень саба можно записать отдельно: --only-level."
        )
        return EXIT_REFUSED
    return _write(
        settings, console, changes, policy, title="Выравнивание саба",
        what=f"align d={delay_ms}ms pol={polarity}", dry_run=dry_run,
    )  # fmt: skip


def sub_command(
    ctx: typer.Context,
    action: Annotated[str, typer.Argument(help="show, crossover, measure, align or restore.")],
    point: Annotated[str | None, typer.Argument(help="Point name (for measure).")] = None,
    fc: Annotated[float, typer.Option(help="Crossover frequency, Hz.")] = 100.0,
    slope: Annotated[str, typer.Option(help="LR24, LR12, BU24, BU18 or BU12.")] = "LR24",
    session: Annotated[str | None, typer.Option(help="Junction session name.")] = None,
    points: Annotated[str | None, typer.Option(help="Comma-separated points for align.")] = None,
    level: Annotated[float, typer.Option(help="Sweep peak level, dBFS.")] = SWEEP.level_dbfs
    + JUNCTION_LEVEL_ABOVE_SWEEP_DB,
    level_match: Annotated[
        bool, typer.Option("--level-match", help="Also bring the sub level to the tops.")
    ] = False,
    only_level: Annotated[
        bool, typer.Option("--only-level", help="Change the sub level only.")
    ] = False,
    write_unverified: Annotated[
        bool,
        typer.Option(
            "--write-unverified",
            help="Write output delay and polarity although these writes were never verified "
            "on hardware. Without it they are shown as advice.",
        ),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the diff, write nothing.")
    ] = False,
    sub: Annotated[str | None, typer.Option(help="Sub matrices, e.g. 3.")] = None,
    mains: Annotated[str | None, typer.Option(help="Top matrices, e.g. 1,2.")] = None,
    others: Annotated[str | None, typer.Option(help="Other speaker matrices, e.g. 5,6.")] = None,
) -> None:
    """Crossover and alignment between the sub and the tops."""
    settings = settings_of(ctx)
    if action not in ACTIONS:
        fail(f"действие: {', '.join(ACTIONS)}")
    try:
        slope_type = EqType(slope)
    except ValueError:
        slope_type = EqType.PEQ
    if slope_type not in CROSSOVER_SLOPES:
        fail("--slope: LR24, LR12, BU24, BU18 или BU12")
    with reachable_console(settings) as console:
        pending = settings.workspace.pending_mutes_file
        if pending.exists() and action != "restore":
            say(
                f"!! Найден {pending.name}: прошлый замер саба не вернул мьюты. Сначала: "
                "ctune sub restore"
            )
            raise typer.Exit(EXIT_REFUSED)
        state = read_console_state(console.client)
        if action == "restore":
            raise typer.Exit(_restore(settings, console, state, dry_run))
        try:
            roles = detect_roles(
                state, sub=_numbers(sub), mains=_numbers(mains), others=_numbers(others)
            )
        except ValueError as error:
            fail(f"не удалось определить роли матриц ({error}); укажите --sub/--mains")
        if action == "show":
            say(_describe(state, roles))
            code = EXIT_OK
        elif action == "crossover":
            code = _crossover(settings, console, state, roles, fc, slope_type, dry_run)
        elif action == "measure":
            code = _measure(settings, console, state, roles, point, session, level, dry_run)
        else:
            code = _align(
                settings, console, state, roles, fc=fc, session=session, only_points=points,
                level_match=level_match, only_level=only_level,
                write_unverified=write_unverified, dry_run=dry_run,
            )  # fmt: skip
    raise typer.Exit(code)
