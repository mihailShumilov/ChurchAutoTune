"""``analyze``, ``propose`` and ``export-for-chat``: read-only commands on stored sessions."""

from pathlib import Path
from typing import Annotated

import typer

from churchtune.analysis.points import Analysis
from churchtune.cli import records
from churchtune.cli.context import (
    EQ_GRID,
    FORMATS,
    FS_HZ,
    Settings,
    fail,
    say,
    settings_of,
    try_read_main_eq,
)
from churchtune.design.proposal import Action, Proposal, TargetSpec
from churchtune.design.system_eq import design
from churchtune.design.system_eq_opt import optimize_design
from churchtune.design.target import target_curve
from churchtune.domain.arrays import FloatArray
from churchtune.domain.eq import SystemEq
from churchtune.mixers.x32.nodes import parse_eq_from_dump
from churchtune.report import plots
from churchtune.report.markdown import (
    export_for_chat,
    proposal_md,
    rt_interpretation,
    session_report_md,
)
from churchtune.report.texts import (
    design_warning_text,
    proposal_note,
    reason_text,
    target_description,
)
from churchtune.storage import files
from churchtune.storage.paths import TIMESTAMP_FORMAT

REPORT_FILE = "report.md"
PROPOSAL_FILE = "eq_proposal.json"
DEFAULT_TARGET = TargetSpec()


def resolve_session(settings: Settings, name: str | None, kind: str = "baseline") -> Path:
    """The named session, or the newest one of a kind; leaves with a message when missing."""
    workspace = settings.workspace
    if name:
        path = workspace.measurements / name
        if not (path / files.SESSION_FILE).exists():
            fail(f"сессия '{name}' не найдена в {workspace.measurements}")
        return path
    latest = files.latest_session(workspace, kind)
    if latest is None:
        fail("нет ни одной сессии замеров. Сначала: ctune measure <точка>")
    return latest


def load_analysis(
    settings: Settings, session_dir: Path, only_points: list[str] | None = None
) -> tuple[Analysis, dict[str, files.StoredPoint]]:
    """Load and analyse the points of a session (optionally a subset)."""
    points = files.load_points(session_dir)
    if only_points is not None:
        points = {k: v for k, v in points.items() if k in only_points}
    if not points:
        fail(f"в {session_dir} нет замеров")
    return records.analyse_stored(settings.workspace, points), points


def target_of(an: Analysis, target: TargetSpec) -> FloatArray:
    """The target curve on the analysis grid."""
    return target_curve(
        an.grid_hz,
        target.tilt_db_per_oct,
        target.tilt_from_hz,
        target.bass_lift_db,
        target.bass_from_hz,
    )


def mic_label(settings: Settings) -> str:
    """Microphone and input for report headers."""
    venue = settings.venue
    name = venue.mic_label or "измерительный микрофон"
    return f"{name} (вход USB {venue.mic_usb_input})" if venue.mic_usb_input else name


def run_analysis(
    settings: Settings,
    session_dir: Path,
    loaded: tuple[Analysis, dict[str, files.StoredPoint]] | None = None,
) -> tuple[Analysis, dict[str, files.StoredPoint], Path]:
    """Analyse a session and write its plots and report; returns the results directory too."""
    an, points = loaded or load_analysis(settings, session_dir)
    workspace = settings.workspace
    out = workspace.results / session_dir.name
    out.mkdir(parents=True, exist_ok=True)
    plots.plot_points(an, out / "fr_points.png")
    plots.plot_average(an, out / "fr_average.png", target_of(an, DEFAULT_TARGET))
    plots.plot_rt60(an, out / "rt60.png")
    session = files.read_session(session_dir)
    eq_meas = session.eq_at_measurement.to_domain() if session.eq_at_measurement else None
    report = session_report_md(
        session_dir.name,
        an,
        records.point_rows(points),
        FORMATS,
        generated=settings.now().strftime("%Y-%m-%d %H:%M"),
        mic_label=mic_label(settings),
        eq_meas=eq_meas,
    )
    (out / REPORT_FILE).write_text(report, encoding="utf-8")
    name = session_dir.name
    latest = report.replace("](fr_", f"]({name}/fr_").replace("](rt60", f"]({name}/rt60")
    (workspace.results / REPORT_FILE).write_text(latest, encoding="utf-8")
    return an, points, out


def analyze_command(
    ctx: typer.Context,
    session: Annotated[
        str | None, typer.Argument(help="Session name; the newest by default.")
    ] = None,
) -> None:
    """Analyse a measured session: response, spread, RT60, report and plots."""
    settings = settings_of(ctx)
    session_dir = resolve_session(settings, session)
    an, points, out = run_analysis(settings, session_dir)
    say(f"Сессия {session_dir.name}: {len(an.norm_db)} точек.")
    for name, point in points.items():
        if point.meta is not None and point.meta.verdict != "ok":
            say(
                f"  ! {name}: {point.meta.verdict} — {'; '.join(records.warning_texts(point.meta))}"
            )
    for note in rt_interpretation(an):
        say("  " + note)
    say(f"Отчёт: {settings.workspace.results / REPORT_FILE} (копия и графики: {out})")


def _eq_from_latest_backup(settings: Settings) -> tuple[SystemEq | None, Path | None]:
    for path in sorted(settings.workspace.backups.glob("*.txt"), reverse=True):
        eq = parse_eq_from_dump(path.read_text(encoding="utf-8"))
        if eq is not None:
            return eq, path
    return None, None


def _measured_eq(settings: Settings, session_dir: Path, eq_now: SystemEq | None) -> SystemEq:
    """The EQ the session was measured under; falls back to the console or the last backup."""
    session = files.read_session(session_dir)
    if session.eq_at_measurement is not None:
        return session.eq_at_measurement.to_domain()
    source = "текущий EQ с пульта"
    eq = eq_now
    if eq is None:
        eq, backup = _eq_from_latest_backup(settings)
        source = f"бэкап {backup}"
    if eq is None:
        fail(
            "EQ во время замера неизвестен, пульт недоступен и бэкапов нет — нечего брать за основу"
        )
    say(f"!! EQ во время замера не записан; беру {source} — убедитесь, что он не менялся")
    return eq


def _print_proposal(proposal: Proposal, session: str) -> None:
    say(f"Предложение для Main LR (сессия {session}, цель: {target_description(proposal.target)}):")
    for entry in proposal.bands:
        mark = "  " if entry.action is Action.KEEP else "->"
        approx = " ОРИЕНТИРОВОЧНО" if entry.approximate else ""
        say(
            f" {mark} {entry.band.band}: {FORMATS.band(entry.was)}  =>  "
            f"{FORMATS.band(entry.band)}  [{entry.action}]{approx}"
        )
        if entry.action is not Action.KEEP:
            say(f"      {reason_text(entry.reason, FORMATS)}")
    pr = proposal.prediction
    say(
        f"Прогноз: RMS отклонения {pr.rms_dev_before_db} -> {pr.rms_dev_after_db} дБ, "
        f"максимум {pr.max_dev_before_db} -> {pr.max_dev_after_db} дБ (модель)."
    )
    if proposal.skipped:
        say("Не исправляем:")
        for s in proposal.skipped:
            say(
                f"   {s.kind} {s.freq_hz:.0f} Гц {s.dev_db:+.1f} дБ — "
                f"{reason_text(s.reason, FORMATS)}"
            )
    for warning in proposal.warnings:
        say(f"!! {design_warning_text(warning)}")
    say(proposal_note(proposal))


def propose_command(
    ctx: typer.Context,
    session: Annotated[
        str | None, typer.Option(help="Session name; the newest by default.")
    ] = None,
    tilt: Annotated[float, typer.Option(help="Target slope above --tilt-from, dB/oct.")] = -1.0,
    tilt_from: Annotated[float, typer.Option(help="Where the target tilt starts, Hz.")] = 1000.0,
    bass_lift: Annotated[float, typer.Option(help="Target lift below about 100 Hz, dB.")] = 3.0,
    method: Annotated[str, typer.Option(help="optimize or greedy.")] = "optimize",
    drop_notches: Annotated[
        bool, typer.Option("--drop-notches", help="Do not protect narrow existing cuts.")
    ] = False,
    output: Annotated[Path | None, typer.Option("-o", "--output", help="Proposal file.")] = None,
) -> None:
    """Propose a Main LR EQ for a measured session. Writes files only, never to the console."""
    settings = settings_of(ctx)
    if method not in ("optimize", "greedy"):
        fail("--method: optimize или greedy")
    session_dir = resolve_session(settings, session)
    eq_now = try_read_main_eq(settings, quiet=True)
    eq_meas = _measured_eq(settings, session_dir, eq_now)
    if eq_now is None:
        say(
            "(пульт недоступен — текущий EQ не сверён; apply всё равно прочитает его перед записью)"
        )
    an, _points, results_dir = run_analysis(settings, session_dir)
    target = TargetSpec(tilt, tilt_from, bass_lift)
    if method == "optimize":
        proposal = optimize_design(
            an, eq_meas, EQ_GRID, FS_HZ, target=target, eq_now=eq_now, keep_notches=not drop_notches
        )
    else:
        proposal = design(an, eq_meas, EQ_GRID, FS_HZ, target=target, eq_now=eq_now)
    stored = records.proposal_file(proposal, session_dir.name, settings.timestamp())
    out_file = output or settings.workspace.proposal_file
    files.write_json(out_file, stored)
    files.write_json(results_dir / PROPOSAL_FILE, stored)
    plots.plot_proposal(an, proposal, results_dir / "proposal.png", target_of(an, target), FS_HZ)
    section = proposal_md(proposal, FORMATS)
    (results_dir / "proposal.md").write_text(section, encoding="utf-8")
    report = settings.workspace.results / REPORT_FILE
    linked = section.replace("](proposal", f"]({session_dir.name}/proposal")
    report.write_text(report.read_text(encoding="utf-8") + linked, encoding="utf-8")
    _print_proposal(proposal, session_dir.name)
    say(
        f"Файл: {out_file}\nДальше: ctune apply {out_file.name}  (покажет diff и спросит "
        "подтверждение)"
    )


def export_command(
    ctx: typer.Context,
    session: Annotated[
        str | None, typer.Argument(help="Session name; the newest by default.")
    ] = None,
) -> None:
    """Print and save a compact English summary of a session for a chat or a forum."""
    settings = settings_of(ctx)
    workspace = settings.workspace
    session_dir = resolve_session(settings, session)
    an, points = load_analysis(settings, session_dir)
    proposal, created = None, ""
    if workspace.proposal_file.exists():
        stored = files.read_proposal(workspace.proposal_file)
        if stored.session == session_dir.name:
            proposal, created = records.proposal_from_file(stored)[0], stored.created
    summaries = sorted(workspace.results.glob(f"{session_dir.name}-verify-*/verify_summary.txt"))
    venue = settings.venue
    setup = (
        f"mic={venue.mic_label or 'unknown'} uncalibrated fs={FS_HZ / 1000:.0f}k "
        "sweep=ESS 20-20k 6s"
    )
    text = export_for_chat(
        session_dir.name,
        an,
        records.point_rows(points),
        FORMATS,
        generated=settings.now().strftime("%Y-%m-%d %H:%M"),
        setup=setup,
        proposal=proposal,
        proposal_created=created,
        eq_now=try_read_main_eq(settings, quiet=True),
        verify=summaries[-1].read_text(encoding="utf-8") if summaries else None,
    )
    workspace.results.mkdir(parents=True, exist_ok=True)
    out = workspace.results / f"for_chat_{settings.now().strftime(TIMESTAMP_FORMAT)}.txt"
    out.write_text(text, encoding="utf-8")
    say(text)
    say(f"--- сохранено: {out} ({len(text.encode())} байт).")
