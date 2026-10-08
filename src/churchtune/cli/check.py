"""``check`` and ``backup``: read-only looks at the console and the audio device."""

import threading

import numpy as np
import typer

from churchtune.analysis.bands import octave_levels
from churchtune.cli.context import (
    EXIT_OK,
    EXIT_USER,
    FORMATS,
    FS_HZ,
    Console,
    Settings,
    open_audio,
    open_console,
    reachable_console,
    say,
    settings_of,
)
from churchtune.domain.levels import peak_dbfs, rms_dbfs
from churchtune.errors import ChurchTuneError, ConsoleUnreachable
from churchtune.mixers.x32.client import X32Client
from churchtune.mixers.x32.codec import lin_to_db
from churchtune.mixers.x32.dump import dump_nodes
from churchtune.mixers.x32.nodes import parse_eq_from_dump, read_main_eq
from churchtune.mixers.x32.transport import X32_PORT, UdpTransport
from churchtune.report.markdown import describe_eq
from churchtune.storage.paths import TIMESTAMP_FORMAT

NOISE_SECONDS = 3.0
METER_SECONDS = 3.5
DIGITAL_SILENCE_DBFS = -110.0
LOUD_ROOM_DBFS = -45.0
LOW_GAIN_DB = 20.0
BACKUP_NODE_TIMEOUT_S = 0.4
CHANNEL_METERS = 32


def _token(line: str | None, index: int, default: str = "?") -> str:
    parts = line.split() if line else []
    return parts[index] if len(parts) > index else default


def _check_console(settings: Settings, console: Console) -> int:
    """Print the console checks; returns the number of problems."""
    venue, client = settings.venue, console.client
    problems = 0
    try:
        info = client.ping()
    except ConsoleUnreachable as error:
        info = None
        say(f"!! {error}")
    if not info:
        say(
            "!! Пульт не отвечает на /xinfo.\n"
            "   - macOS: System Settings -> Privacy & Security -> Local Network: включите "
            "ваш терминал;\n"
            "   - отключите VPN: он перехватывает локальную сеть -> 'No route to host';\n"
            "   - Mac должен быть в одной сети с пультом."
        )
        return 1
    say(f"   /xinfo: {' '.join(map(str, info))}")
    if venue.firmware and venue.firmware not in map(str, info):
        say(f"   (прошивка не {venue.firmware} — формулы OSC проверялись на {venue.firmware})")
    prefs = client.node("-prefs")
    if prefs:
        say(f"   клок: {'48K INT' if '48K INT' in prefs else prefs}")
        if "48K" not in prefs:
            say("!! пульт не на 48 кГц")
            problems += 1
    if venue.mic_headamp is not None:
        headamp = client.node(f"headamp/{venue.mic_headamp:03d}")
        if headamp:
            gain, phantom = _token(headamp, 1), _token(headamp, 2)
            say(
                f"   измерительный мик headamp/{venue.mic_headamp:03d}: gain {gain} dB, "
                f"+48V {phantom}"
            )
            if phantom != "ON":
                say(
                    "!! Конденсаторному измерительному микрофону нужен фантом +48V. Включите "
                    "вручную на пульте. Инструмент сам в пульт не пишет."
                )
                problems += 1
            try:
                if float(gain) < LOW_GAIN_DB:
                    say("   ! gain < +20 dB — скорее всего будет тихо; подберёте в ctune level")
            except ValueError:
                pass
    if venue.mic_channel is not None:
        mix = client.node(f"ch/{venue.mic_channel:02d}/mix")
        if mix:
            muted = _token(mix, 1) == "OFF"
            state = "в MUTE — правильно" if muted else "НЕ в mute!"
            say(f"   канал {venue.mic_channel} (мик) {state}  [{mix}]")
            if not muted:
                say(
                    f"!! Замьютьте канал {venue.mic_channel}, иначе микрофон пойдёт в колонки "
                    "(обратная связь)."
                )
                problems += 1
    for channel in venue.playback_channels:
        mix = client.node(f"ch/{channel:02d}/mix")
        if mix:
            on, fader = _token(mix, 1), _token(mix, 2)
            say(f"   канал {channel} (PC): {'ON' if on == 'ON' else 'MUTE'}, фейдер {fader}")
            if on != "ON" or fader == "-oo":
                say(f"!! канал {channel} замьючен или фейдер -oo — свип не дойдёт до колонок")
                problems += 1
    main_mix = client.node("main/st/mix")
    if main_mix:
        on, fader = _token(main_mix, 1), _token(main_mix, 2)
        say(f"   Main LR: {'ON' if on == 'ON' else 'MUTE'}, фейдер {fader}")
        if on != "ON" or fader == "-oo":
            say("!! Main LR замьючен или фейдер -oo — звука не будет")
            problems += 1
    insert = client.node("main/st/insert")
    if insert:
        say(f"   Main LR insert: {_token(insert, 1)} (GEQ/TEQ должны оставаться OFF)")
    card = client.node("config/routing/CARD")
    if card and venue.card_routing:
        ok = venue.card_routing in card
        shown = card.split(" ", 1)[1] if " " in card else card
        say(f"   USB routing: {shown} {'(ок)' if ok else '!! ожидалось ' + venue.card_routing}")
        problems += 0 if ok else 1
    try:
        eq = read_main_eq(client)
        say("   " + describe_eq(eq, FORMATS, "Main LR EQ").replace("\n", "\n   "))
    except ConsoleUnreachable as error:
        say(f"!! EQ Main LR не прочитан: {error}")
    return problems


def _mic_meter_db(settings: Settings, seconds: float) -> list[float]:
    """Levels of the mic channel from the console meters, read on a second connection."""
    venue = settings.venue
    values: list[float] = []
    if venue.mic_channel is None or not venue.console_host:
        return values
    index = venue.mic_channel - 1

    def on_frame(frame: list[float]) -> None:
        if len(frame) >= CHANNEL_METERS:
            values.append(lin_to_db(frame[index]))

    transport = UdpTransport(venue.console_host, venue.console_port or X32_PORT)
    try:
        X32Client(transport).meters0(seconds, on_frame)
    except ConsoleUnreachable:
        pass
    finally:
        transport.close()
    return values


def check_command(ctx: typer.Context) -> None:
    """Check the console, the audio device and the room noise. Read-only."""
    settings = settings_of(ctx)
    venue = settings.venue
    say(f"== 1. Пульт {settings.console_label} (OSC, только чтение)")
    with open_console(settings) as console:
        problems = _check_console(settings, console)
        reachable = problems == 0 or console.alive()
    say("")
    say(f"== 2. Аудио: устройство '{settings.audio}'")
    try:
        backend = open_audio(settings)
        say(f"   {backend.describe()}")
        rate = backend.check_samplerate()
        if int(rate) != int(FS_HZ):
            say(f"!! устройство сейчас на {rate:.0f} Гц. Audio MIDI Setup -> 48000 Hz")
            problems += 1
        else:
            say("   48 кГц: ок")
    except ChurchTuneError as error:
        say(f"!! {error}")
        say(f"\nИтого проблем: {problems + 1}")
        raise typer.Exit(EXIT_USER) from None
    say("")
    say(f"== 3. Шум зала: {NOISE_SECONDS:.0f} с записи со входа микрофона (тишина в зале!)")
    meter_values: list[float] = []
    thread = None
    if reachable:
        thread = threading.Thread(
            target=lambda: meter_values.extend(_mic_meter_db(settings, METER_SECONDS)), daemon=True
        )
        thread.start()
    recording = np.asarray(backend.record(NOISE_SECONDS), dtype=np.float64)
    rms, peak = rms_dbfs(recording), peak_dbfs(recording)
    say(f"   шум: RMS {rms:.1f} dBFS, пик {peak:.1f} dBFS")
    if rms < DIGITAL_SILENCE_DBFS:
        say("!! цифровая тишина: микрофон не подключён / нет +48V / не тот вход USB")
        problems += 1
    elif rms > LOUD_ROOM_DBFS:
        say("!! громко: в зале шумно или gain слишком большой — замеры будут с плохим SNR")
    else:
        say("   (для замера нужно, чтобы свип был минимум на 30–40 дБ громче этого шума)")
    levels = octave_levels(recording, FS_HZ)
    say("   по октавам: " + "  ".join(f"{band}:{level:.0f}" for band, level in levels.items()))
    if thread is not None:
        thread.join(timeout=METER_SECONDS + 1.5)
        if meter_values:
            say(
                f"   метр пульта ch{venue.mic_channel} (OSC /meters/0): медиана "
                f"{float(np.median(meter_values)):.1f} dB — для сравнения с USB-входом"
            )
    tail = "  — можно продолжать: ctune backup" if problems == 0 else ""
    say(f"\nИтого проблем: {problems}{tail}")
    raise typer.Exit(EXIT_OK if problems == 0 else EXIT_USER)


def backup_command(ctx: typer.Context) -> None:
    """Save a read-only dump of every console node to the backups directory."""
    settings = settings_of(ctx)
    workspace = settings.workspace
    with reachable_console(settings) as console:
        info = console.client.ping() or []
        workspace.backups.mkdir(parents=True, exist_ok=True)
        out = workspace.backups / f"{settings.now().strftime(TIMESTAMP_FORMAT)}.txt"
        nodes = dump_nodes()
        lines = ["# /xinfo " + " ".join(map(str, info))]
        missing = 0
        for node in nodes:
            line = console.client.node(node, timeout_s=BACKUP_NODE_TIMEOUT_S)
            if line:
                lines.append(line)
            else:
                missing += 1
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    say(f"   готово: {len(nodes) - missing}/{len(nodes)} узлов -> {out}")
    eq = parse_eq_from_dump(out.read_text(encoding="utf-8"))
    if eq is not None:
        say(describe_eq(eq, FORMATS, "Main LR EQ"))
    say(
        "\nСТРАХОВКА: сохраните сцену на пульте вручную в СВОБОДНЫЙ слот:\n"
        "  View на SCENES -> выберите пустой слот -> SAVE -> имя, например 'before-ctune-"
        f"{settings.now().date().isoformat()}'.\n  (Этот инструмент сцены не трогает — "
        "только чтение.)"
    )
