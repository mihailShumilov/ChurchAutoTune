"""``devices``, ``mics map|calibrate``, ``noise``, ``pathcheck``: the M2 measurement engine.

Transitional expert CLI with Russian operator texts (docs/adr/0003); the wizard (M3) drives the
same engine with its own screens.
"""

from datetime import datetime
from typing import Annotated

import numpy as np
import typer

from churchtune.audio.devices import list_devices, simulator_device
from churchtune.audio.factory import open_multi_backend
from churchtune.audio.fake.hall import CALIBRATION_POINT, FakeHall
from churchtune.cli.context import (
    EXIT_OK,
    EXIT_USER,
    FS_HZ,
    Settings,
    ask,
    fail,
    relative_to_home,
    say,
    settings_of,
)
from churchtune.errors import DevSafeViolation
from churchtune.measure.calibration import (
    MIN_MICS,
    CalibrationError,
    MicSetCalibration,
    calibrate,
    mic_set_id,
)
from churchtune.measure.config import QualityGates, SweepConfig
from churchtune.measure.mic_mapping import MappingOutcome, find_scratch
from churchtune.measure.multi import measure_round
from churchtune.measure.noise import NoiseHint, measure_background
from churchtune.measure.path_check import check_path
from churchtune.safety.authorize import is_confirmation
from churchtune.storage.files import reusable_mic_calibration, write_mic_calibration
from churchtune.storage.models import MicCalibrationFile, MicCalibrationModel

mics_app = typer.Typer(help="Measurement microphones: mapping and set calibration.")

LISTEN_S = 3.0
MAX_MICS = 8
ALL_INPUTS = "1-32"
DEFAULT_MODEL = "ECM8000"
HINT_TEXT = {
    NoiseHint.HUM: "гул сети (диммеры, LED-драйверы): выключите свет сцены",
    NoiseHint.RUMBLE: "низкочастотный шум (вентиляция, кондиционер): выключите, если можно",
    NoiseHint.LOUD: "в зале шумно: разговоры, шаги, улица — попросите тишины",
}
PROBLEM_TEXT = {
    "shape": "АЧХ отличается от остальных",
    "delay": "задержка отличается",
    "polarity": "перевёрнута полярность",
    "noise": "сильно шумит",
}


def parse_inputs(text: str) -> list[int]:
    """``"10,11,13"`` or ``"9-12"`` or a mix into a list of input numbers.

    Raises:
        typer.BadParameter: The text is not a list of numbers and ranges.
    """
    found: list[int] = []
    for part in text.replace(" ", "").split(","):
        first, _, last = part.partition("-")
        if not first.isdigit() or (last and not last.isdigit()):
            raise typer.BadParameter(f"expected numbers like 10,11 or 9-12, got {text!r}")
        found += list(range(int(first), int(last or first) + 1))
    return found


def _confirm_sound(what: str) -> None:
    say(
        f"{what}\nСейчас прозвучит тестовый сигнал. Рядом с колонками не должно быть людей с "
        "чувствительным слухом; наушники снимите."
    )
    if not is_confirmation(ask("Продолжить? [y/N] ")):
        fail("Отменено.", EXIT_USER)


def devices_command(ctx: typer.Context) -> None:
    """List audio devices, the console's USB card first."""
    settings_of(ctx)
    try:
        devices = list_devices()
    except DevSafeViolation:
        devices = [simulator_device()]
        say("режим разработки (dev-safe): реальные устройства не опрашиваются")
    for device in devices:
        mark = "✓" if not device.problems else "!"
        notes = {"channels": "меньше 32 каналов", "sample_rate": "частота не 48 кГц"}
        problems = ", ".join(notes[p] for p in device.problems)
        say(
            f" {mark} {device.name}: {device.inputs} вх / {device.outputs} вых, "
            f"{device.default_fs_hz:.0f} Гц{' — ' + problems if problems else ''}"
        )
    raise typer.Exit(EXIT_OK)


@mics_app.command("map")
def map_command(
    ctx: typer.Context,
    count: Annotated[int, typer.Option(help="How many microphones to find.")] = 1,
    inputs: Annotated[str, typer.Option(help="USB inputs to listen to.")] = ALL_INPUTS,
) -> None:
    """Find each microphone's USB input: scratch its grille when asked."""
    settings = settings_of(ctx)
    if not 1 <= count <= MAX_MICS:
        fail(f"микрофонов может быть от 1 до {MAX_MICS}")
    candidates = parse_inputs(inputs)
    backend = open_multi_backend(settings.audio, fs_hz=FS_HZ)
    found: list[int] = []
    while len(found) < count:
        number = len(found) + 1
        ask(f"Микрофон {number}: нажмите Enter и сразу слегка поскребите по его сетке… ")
        if isinstance(backend, FakeHall):
            backend.scratch_next()  # development rig: a simulated person scratches the next mic
        recording = backend.record_multi(LISTEN_S, candidates)
        result = find_scratch(
            np.asarray(recording.samples, dtype=np.float64), candidates, FS_HZ, found
        )
        if result.outcome is MappingOutcome.FOUND and result.usb_input is not None:
            found.append(result.usb_input)
            clip = " (клиппинг — убавьте gain)" if result.clipped else ""
            say(f"  ✓ микрофон {number} → USB in {result.usb_input}{clip}")
            continue
        advice = {
            MappingOutcome.NOTHING: "ничего не услышал: проверьте +48V, кабель и вход; ещё раз",
            MappingOutcome.AMBIGUOUS: "всплеск сразу на нескольких входах; трогайте один микрофон",
            MappingOutcome.ALREADY_MAPPED: (
                f"это уже найденный вход {result.usb_input}; поскребите другой микрофон"
            ),
        }
        say(f"  ! {advice[result.outcome]}")
        if not is_confirmation(ask("  Повторить? [y/N] ")):
            fail("Маппинг не закончен.", EXIT_USER)
    say(f"Входы микрофонов: --inputs {','.join(str(i) for i in found)}")
    raise typer.Exit(EXIT_OK)


def _calibration_file(
    calibration: MicSetCalibration, set_id: str, model: str, now: datetime
) -> MicCalibrationFile:
    return MicCalibrationFile(
        set_id=set_id,
        created=now.isoformat(timespec="seconds"),
        centres_hz=calibration.centres_hz,
        mics=tuple(
            MicCalibrationModel(
                usb_input=m.usb_input,
                model=model,
                sensitivity_db=m.sensitivity_db,
                delay_ms=m.delay_ms,
                inverted=m.inverted,
                shape_db=m.shape_db,
                problems=tuple(p.value for p in m.problems),
            )
            for m in calibration.mics
        ),
    )


@mics_app.command("calibrate")
def calibrate_command(
    ctx: typer.Context,
    inputs: Annotated[str, typer.Option(help="USB inputs of the microphones, e.g. 10-13.")],
    model: Annotated[str, typer.Option(help="Microphone model.")] = DEFAULT_MODEL,
    level: Annotated[float, typer.Option(help="Sweep peak level, dBFS.")] = -24.0,
    force: Annotated[bool, typer.Option(help="Calibrate again even if recent.")] = False,
) -> None:
    """Calibrate a set of microphones against each other (all of them side by side)."""
    settings = settings_of(ctx)
    chosen = parse_inputs(inputs)
    if len(chosen) < MIN_MICS:
        fail("калибровка нужна только для двух и более микрофонов")
    set_id = mic_set_id([(usb, model) for usb in chosen])
    recent = reusable_mic_calibration(settings.workspace, set_id, settings.now())
    if recent is not None and not force:
        say(f"Комплект уже откалиброван {recent.created}; повтор: --force")
        raise typer.Exit(EXIT_OK)
    say(
        "Поставьте все микрофоны рядом: капсюли в пределах 10 см, на одной высоте, смотрят в "
        "одну сторону; 3–5 м от колонок."
    )
    _confirm_sound("Два свипа для калибровки комплекта.")
    backend = open_multi_backend(settings.audio, fs_hz=FS_HZ)
    if isinstance(backend, FakeHall):
        backend.place_all(CALIBRATION_POINT)
    measurement = measure_round(
        backend, chosen, fs_hz=FS_HZ, sweep=SweepConfig(), gates=QualityGates(), level_dbfs=level
    )
    try:
        result = calibrate(measurement)
    except CalibrationError as error:
        fail(f"✗ замер для калибровки плохой ({error}); проверьте gain и тишину, повторите")
    say("  вход  чувствит.  задержка  форма   итог")
    for mic in result.mics:
        verdict = "✓" if mic.ok else "✗ " + ", ".join(PROBLEM_TEXT[p.value] for p in mic.problems)
        say(
            f"  {mic.usb_input:>4}  {mic.sensitivity_db:+6.2f} дБ  {mic.delay_ms:+6.2f} мс  "
            f"±{mic.max_shape_db:4.1f} дБ  {verdict}"
        )
    path = write_mic_calibration(
        settings.workspace, _calibration_file(result, set_id, model, settings.now())
    )
    say(f"Сохранено: {relative_to_home(path, settings.workspace)}")
    if result.polarity_ambiguous:
        say("! Полярность у половины микрофонов разная — проверьте кабели всех микрофонов.")
    if result.pair and result.rejected:
        say("! Микрофона два: один из них отличается, но какой — не понять. Проверьте оба.")
    if result.rejected:
        say("Проверьте кабель или исключите отмеченные микрофоны из замера.")
        raise typer.Exit(EXIT_USER)
    raise typer.Exit(EXIT_OK)


def noise_command(
    ctx: typer.Context,
    inputs: Annotated[str, typer.Option(help="USB inputs of the microphones.")],
) -> None:
    """Measure the background noise at every microphone (3 s of silence)."""
    settings = settings_of(ctx)
    backend = open_multi_backend(settings.audio, fs_hz=FS_HZ)
    say("Тишина в зале на 3 секунды…")
    for mic in measure_background(backend, parse_inputs(inputs), FS_HZ):
        octaves = " ".join(f"{band}:{level:.0f}" for band, level in mic.octaves_dbfs.items())
        say(f"  USB in {mic.usb_input}: {mic.rms_dbfs:.1f} dBFS  [{octaves}]")
        for hint in mic.hints:
            say(f"    ! {HINT_TEXT[hint]}")
    raise typer.Exit(EXIT_OK)


def pathcheck_command(
    ctx: typer.Context,
    inputs: Annotated[str, typer.Option(help="USB inputs of the microphones.")],
) -> None:
    """Quiet tone and noise: does the sound reach every microphone, and with which latency."""
    settings: Settings = settings_of(ctx)
    _confirm_sound("Тихий тон 1 кГц и шум (−40 dBFS).")
    backend = open_multi_backend(settings.audio, fs_hz=FS_HZ)
    results = check_path(backend, parse_inputs(inputs), FS_HZ)
    for mic in results:
        if mic.heard:
            say(
                f"  ✓ USB in {mic.usb_input}: слышно (+{mic.tone_db:.0f} дБ), "
                f"задержка {mic.latency_ms:.1f} мс"
            )
        else:
            say(f"  ✗ USB in {mic.usb_input}: не слышно — проверьте маршрут и мьюты")
    raise typer.Exit(EXIT_OK if all(m.heard for m in results) else EXIT_USER)
