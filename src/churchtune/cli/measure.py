"""``level``, ``measure`` and ``verify``: the commands that play sound and record."""

import re
import sys
import threading
import time
from pathlib import Path
from typing import Annotated

import numpy as np
import typer

from churchtune.analysis.response import (
    GRID_HZ,
    interpolate_at,
    magnitude_db,
    third_octave_centres,
)
from churchtune.audio.base import AudioBackend, Float32Array
from churchtune.cli import records
from churchtune.cli.analyze import load_analysis, resolve_session, run_analysis, target_of
from churchtune.cli.context import (
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_USER,
    FS_HZ,
    Settings,
    ask,
    fail,
    open_audio,
    say,
    settings_of,
    try_read_main_eq,
)
from churchtune.design.proposal import TargetSpec
from churchtune.domain.levels import HARD_LIMIT_DBFS, peak_dbfs, rms_dbfs
from churchtune.errors import LevelError, MeasurementAborted
from churchtune.measure.config import QualityGates, SweepConfig
from churchtune.measure.point import measure_point
from churchtune.measure.stimulus import check_level, pink_noise
from churchtune.report import plots
from churchtune.storage import files
from churchtune.storage.models import EqModel, PointMeta, SessionFile, SweepModel

SWEEP = SweepConfig()
GATES = QualityGates()
POINT_RE = re.compile(r"[A-Za-z0-9_.-]{1,40}")
# Wanted mic level while pink noise plays, dBFS RMS.
LEVEL_TARGET_DBFS = (-20.0, -12.0)
LEVEL_BELOW_SWEEP_DB = 3.0
PINK_SECONDS = 10.0
PINK_SEED = 7
FADE_IN_S, FADE_OUT_S = 1.5, 0.5
METER_BUFFER_S = 0.3
METER_PRINT_S = 0.15
METER_FLOOR_DBFS = -60.0
METER_WIDTH = 40
PEAK_DECAY_DB = 0.5
VERIFY_MAX_HZ = 10000.0
VERIFY_MIN_HZ = 35.0
BETTER_BY_DB = 0.3


def valid_point(name: str | None) -> bool:
    """True for a point name made of Latin letters, digits and ``._-`` (at most 40)."""
    return bool(name) and bool(POINT_RE.fullmatch(name or ""))


def _checked_level(level_dbfs: float) -> float:
    try:
        check_level(level_dbfs)
    except LevelError as error:
        fail(f"ОТКАЗ: {error}")
    return level_dbfs


def session_file(settings: Settings, name: str, kind: str, base: str | None) -> SessionFile:
    """The session record for a measurement started now, with the console's current EQ."""
    eq = try_read_main_eq(settings)
    return SessionFile(
        name=name,
        kind=kind,  # type: ignore[arg-type]  # validated by the model
        base=base,
        created=settings.timestamp(),
        eq_at_measurement=EqModel.from_domain(eq) if eq is not None else None,
        fs=FS_HZ,
        sweep=SweepModel(f1=SWEEP.f1_hz, f2=SWEEP.f2_hz, seconds=SWEEP.duration_s),
        warnings=() if eq is not None else ("EQ Main LR was not read during the measurement",),
    )


def open_session(settings: Settings, session: SessionFile) -> Path:
    """Create or reuse the session directory; warn when the EQ changed during the session."""
    status = files.ensure_session(settings.workspace, session, settings.now().strftime("%H:%M:%S"))
    if status.eq_changed:
        say(
            "!! ВНИМАНИЕ: EQ Main LR изменился с начала сессии. Замеры в одной сессии должны "
            "делаться при одном и том же EQ. Начните новую сессию (--session)."
        )
    return status.path


def measure_one(
    settings: Settings,
    backend: AudioBackend,
    point: str,
    session_dir: Path,
    level_dbfs: float,
    avg: int,
) -> PointMeta | None:
    """Measure and store one point; None when the measurement was interrupted."""
    backend.set_point(point)

    def progress(index: int, total: int) -> None:
        say(f"  свип {index}/{total}: {SWEEP.duration_s:.0f} с + хвост {SWEEP.tail_s:.1f} с ...")

    try:
        result = measure_point(
            backend, fs_hz=FS_HZ, sweep=SWEEP, gates=GATES, level_dbfs=level_dbfs, n_avg=avg,
            on_sweep=progress,
        )  # fmt: skip
    except MeasurementAborted as error:
        say(str(error))
        return None
    meta = records.point_meta(point, settings.timestamp(), backend.name, result)
    response = magnitude_db(result.ir, FS_HZ)
    files.save_point(
        session_dir, meta, recording=result.recording, ir=result.ir, fs_hz=FS_HZ,
        grid_hz=GRID_HZ, response_db=response,
    )  # fmt: skip
    say(
        f"  пик {meta.peak_dbfs} dBFS | шум {meta.noise_dbfs} dBFS | SNR {meta.snr_db} дБ | "
        f"PNR {meta.pnr_db} дБ | задержка {meta.latency_ms} мс -> {meta.verdict.upper()}"
    )
    for warning in records.warning_texts(meta):
        say(f"  ! {warning}")
    if meta.verdict == "bad":
        say("  ЗАМЕР ПЛОХОЙ — исправьте причину и повторите эту точку (--force).")
    return meta


def measure_command(
    ctx: typer.Context,
    point: Annotated[str, typer.Argument(help="Point name, for example p1-center.")],
    session: Annotated[
        str | None, typer.Option(help="Session name; today's date by default.")
    ] = None,
    level: Annotated[float, typer.Option(help="Sweep peak level, dBFS.")] = SWEEP.level_dbfs,
    avg: Annotated[int, typer.Option(help="Number of sweeps to average.")] = 1,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing point.")] = False,
    yes: Annotated[bool, typer.Option("-y", "--yes", help="Do not wait for Enter.")] = False,
) -> None:
    """Measure one point: sweep, record, impulse response, quality check."""
    settings = settings_of(ctx)
    if not valid_point(point):
        fail("имя точки: латиница/цифры/._- (например p1-center)")
    _checked_level(level)
    name = session or settings.now().date().isoformat()
    session_dir = open_session(settings, session_file(settings, name, "baseline", None))
    if (session_dir / point / files.IR_FILE).exists() and not force:
        answer = ask(f"Точка {point} уже есть в сессии {name}. Перезаписать? [y/N] ")
        if answer.strip().lower() not in ("y", "д"):
            say("Отменено.")
            raise typer.Exit(EXIT_USER)
    backend = open_audio(settings)
    say(
        f"Сессия {name}, точка {point}. Свип {SWEEP.f1_hz:.0f}–{SWEEP.f2_hz:.0f} Гц, "
        f"{SWEEP.duration_s:.0f} с, {level:+.1f} dBFS, x{avg}."
    )
    if not yes:
        ask(
            "Микрофон на штативе 1.2 м, капсюль на колонки, в зале тишина. Enter — старт "
            "(Ctrl+C — отмена) "
        )
    meta = measure_one(settings, backend, point, session_dir, level, avg)
    if meta is None:
        raise typer.Exit(EXIT_INTERRUPTED)
    say(f"Сохранено: {session_dir / point}")
    raise typer.Exit(EXIT_USER if meta.verdict == "bad" else EXIT_OK)


def level_command(
    ctx: typer.Context,
    level: Annotated[float | None, typer.Option(help="Pink noise RMS level, dBFS.")] = None,
    max_seconds: Annotated[float, typer.Option(help="Stop by itself after this long.")] = 0.0,
) -> None:
    """Play pink noise and show the mic level, to set the headamp gain."""
    settings = settings_of(ctx)
    out_level = _checked_level(
        level if level is not None else SWEEP.level_dbfs - LEVEL_BELOW_SWEEP_DB
    )
    backend = open_audio(settings)
    noise = pink_noise(
        PINK_SECONDS, FS_HZ, np.random.default_rng(PINK_SEED), rms_level_dbfs=out_level
    ).astype(np.float32)
    max_up = HARD_LIMIT_DBFS - peak_dbfs(noise)  # dB that may still be added
    state = _LevelState(buffer=np.zeros(int(METER_BUFFER_S * FS_HZ), dtype=np.float32))
    low, high = LEVEL_TARGET_DBFS
    started = time.monotonic()

    def next_block(frames: int) -> Float32Array | None:
        if state.stop and state.envelope <= 0.0:
            return None
        if max_seconds and time.monotonic() - started > max_seconds:
            state.stop = True
        index = (state.position + np.arange(frames)) % len(noise)
        state.position = (state.position + frames) % len(noise)
        step = -1.0 / (FADE_OUT_S * FS_HZ) if state.stop else 1.0 / (FADE_IN_S * FS_HZ)
        envelope = np.clip(state.envelope + step * np.arange(1, frames + 1), 0.0, 1.0)
        state.envelope = float(envelope[-1])
        mono = noise[index] * envelope * 10 ** (state.offset_db / 20)
        return np.stack([mono, mono], axis=1).astype(np.float32)

    def on_input(block: Float32Array) -> None:
        buffer = state.buffer
        count = min(len(block), len(buffer))
        buffer[:-count] = buffer[count:]
        buffer[-count:] = block[-count:]
        state.rms = rms_dbfs(buffer)
        state.peak = max(state.peak - PEAK_DECAY_DB, peak_dbfs(block))
        if state.envelope >= 0.999 and not state.stop:  # noqa: PLR2004 - fully faded in
            state.steady_rms, state.steady_peak = state.rms, state.peak

    def on_tick() -> None:
        now = time.monotonic()
        if now - state.last_print < METER_PRINT_S:
            return
        state.last_print = now
        span = -METER_FLOOR_DBFS
        filled = int(np.clip((state.rms + span) / span * METER_WIDTH, 0, METER_WIDTH))
        marks = {int((low + span) / span * METER_WIDTH), int((high + span) / span * METER_WIDTH)}
        bar = "".join(
            "#" if i < filled else ("|" if i in marks else ".") for i in range(METER_WIDTH)
        )
        verdict = "ОК" if low <= state.rms <= high else ("тихо" if state.rms < low else "ГРОМКО")
        limit = " (предел)" if state.offset_db >= max_up - 1e-6 else ""
        typer.echo(
            f"\r  вход: RMS {state.rms:6.1f} dBFS  пик {state.peak:6.1f}  [{bar}] {verdict:<6} "
            f"выход {out_level + state.offset_db:+.1f} dBFS{limit} ",
            nl=False,
        )

    def reader() -> None:
        while not state.stop:
            line = sys.stdin.readline()
            command = line.strip()
            if command in ("+", "="):
                state.offset_db = min(state.offset_db + 1.0, max_up)
            elif command == "-":
                state.offset_db -= 1.0
            else:  # Enter, 'q', end of input
                state.stop = True
                return

    say(
        f"Розовый шум {out_level:+.1f} dBFS RMS на выходы USB 1+2.\nЦель: вход микрофона в "
        f"диапазоне {low:.0f}..{high:.0f} dBFS. Регулируйте gain входа на пульте.\nКоманды: "
        f"'+' Enter / '-' Enter — выход ±1 дБ (предел {HARD_LIMIT_DBFS:.0f} dBFS пик), Enter — "
        "стоп (плавный fade-out), Ctrl+C — мгновенная тишина."
    )
    if not max_seconds:
        threading.Thread(target=reader, daemon=True).start()
    try:
        backend.run_stream(next_block, on_input, on_tick)
    except MeasurementAborted as error:
        say(f"\n{error}")
        raise typer.Exit(EXIT_INTERRUPTED) from None
    state.stop = True
    rms = state.steady_rms if state.steady_rms is not None else state.rms
    peak = state.steady_peak if state.steady_peak is not None else state.peak
    say(f"\nИтог (до fade-out): вход {rms:.1f} dBFS RMS, пик {peak:.1f} dBFS.")
    if low <= rms <= high:
        say("Уровень в норме.")
    elif rms < low:
        say("Тихо: прибавьте gain входа микрофона на пульте (или '+' здесь).")
    else:
        say("Громко: убавьте gain входа микрофона на пульте.")
    if state.offset_db:
        say(
            f"Вы меняли выход на {state.offset_db:+.1f} дБ -> для свипа той же громкости: "
            f"ctune measure <точка> --level {SWEEP.level_dbfs + state.offset_db:.0f}"
        )


class _LevelState:
    """Mutable state shared by the audio callback, the meter and the key reader of ``level``."""

    def __init__(self, buffer: Float32Array) -> None:
        self.buffer = buffer
        self.position = 0
        self.envelope = 0.0
        self.offset_db = 0.0
        self.stop = False
        self.rms = -200.0
        self.peak = -200.0
        self.last_print = 0.0
        self.steady_rms: float | None = None
        self.steady_peak: float | None = None


def _verify_target(settings: Settings) -> TargetSpec:
    """The target of the latest proposal, so that verify judges against what was aimed at."""
    path = settings.workspace.proposal_file
    if not path.exists():
        return TargetSpec(bass_lift_db=0.0)
    target = files.read_proposal(path).target
    return TargetSpec(
        target.tilt_db_per_oct, target.tilt_from_hz, target.bass_lift_db, target.bass_from_hz
    )


def verify_command(
    ctx: typer.Context,
    session: Annotated[
        str | None, typer.Option(help="Baseline session; the newest by default.")
    ] = None,
    points: Annotated[
        str | None, typer.Option(help="Comma-separated points to re-measure.")
    ] = None,
    level: Annotated[
        float | None, typer.Option(help="Sweep level; as in the baseline by default.")
    ] = None,
    avg: Annotated[int, typer.Option(help="Sweeps to average; as in the baseline by default.")] = 1,
    yes: Annotated[bool, typer.Option("-y", "--yes", help="Do not wait between points.")] = False,
    analyze_only: Annotated[
        bool, typer.Option("--analyze-only", help="Compare existing measurements only.")
    ] = False,
) -> None:
    """Re-measure the same points and compare with the baseline session."""
    settings = settings_of(ctx)
    base = resolve_session(settings, session)
    base_points = files.point_names(base)
    wanted = points.split(",") if points else base_points
    metas = files.session_levels(base)
    if level is None:
        level = next(iter(metas.values())).level_dbfs if metas else SWEEP.level_dbfs
        say(f"Уровень свипа как в базовой сессии: {level:+.1f} dBFS")
    _checked_level(level)
    if avg == 1 and metas:
        avg = max(meta.avg for meta in metas.values())
    name = f"{base.name}-verify-{settings.now():%H%M}"
    verify_dir = open_session(settings, session_file(settings, name, "verify", base.name))
    if not analyze_only:
        backend = open_audio(settings)
        say(f"Повторный замер {len(wanted)} точек (база {base.name}) -> {name}")
        for point in wanted:
            if not yes:
                answer = (
                    ask(
                        f"[{point}] поставьте микрофон в ту же точку. Enter — замер, s — "
                        "пропустить, q — закончить: "
                    )
                    .strip()
                    .lower()
                )
                if answer == "q":
                    break
                if answer == "s":
                    continue
            say(f"[{point}]")
            measure_one(settings, backend, point, verify_dir, level, avg)
    common = [p for p in files.point_names(verify_dir) if p in base_points]
    if not common:
        fail("нет повторных замеров для сравнения")
    before, _ = load_analysis(settings, base, common)
    after_loaded = load_analysis(settings, verify_dir, common)
    after = after_loaded[0]
    target = target_of(before, _verify_target(settings))
    out = settings.workspace.results / name
    out.mkdir(parents=True, exist_ok=True)
    plots.plot_verify(before, after, out / "verify.png", target)
    grid = before.grid_hz
    compared = (grid >= max(before.f_low_hz * 1.25, VERIFY_MIN_HZ)) & (grid <= VERIFY_MAX_HZ)
    rms_before = float(np.sqrt(np.mean((before.avg_db - target)[compared] ** 2)))
    rms_after = float(np.sqrt(np.mean((after.avg_db - target)[compared] ** 2)))
    spread_before = float(before.spread_db[compared].mean())
    spread_after = float(after.spread_db[compared].mean())
    lines = [
        f"points: {', '.join(common)}",
        f"RMS deviation from target (35..10k): before {rms_before:.2f} dB -> "
        f"after {rms_after:.2f} dB",
        f"mean spread between points: before {spread_before:.2f} dB -> after {spread_after:.2f} dB",
        "1/3 oct  before  after  diff:",
    ]
    for centre in third_octave_centres(31.5, 16000):
        was = float(interpolate_at(np.array([centre]), before.avg_db)[0])
        now = float(interpolate_at(np.array([centre]), after.avg_db)[0])
        lines.append(f"  {centre:7.0f}  {was:+5.1f}  {now:+5.1f}  {now - was:+5.1f}")
    summary = "\n".join(lines)
    (out / "verify_summary.txt").write_text(summary + "\n", encoding="utf-8")
    (out / "verify.md").write_text(
        f"# Verify {name} vs {base.name}\n\n```\n{summary}\n```\n\n![verify](verify.png)\n",
        encoding="utf-8",
    )
    run_analysis(settings, verify_dir, after_loaded)
    say(summary.split("1/3 oct")[0])
    if rms_after < rms_before - BETTER_BY_DB:
        verdict = "лучше"
    elif abs(rms_after - rms_before) <= BETTER_BY_DB:
        verdict = "без заметной разницы"
    else:
        verdict = "ХУЖЕ — подумайте об ctune undo"
    say(f"Итог: {verdict}. График: {out / 'verify.png'}")
