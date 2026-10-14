"""``doctor``, ``snapshot``, ``emulator``: find the console, read it, guess the speaker topology.

Read-only. Transitional expert CLI with Russian operator texts (docs/adr/0003).
"""

import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from churchtune.cli.context import (
    EXIT_OK,
    EXIT_UNREACHABLE,
    Settings,
    fail,
    open_console,
    relative_to_home,
    say,
    settings_of,
)
from churchtune.domain.snapshot import ConsoleFamily, ConsoleIdentity, ConsoleSnapshot
from churchtune.domain.topology import Issue, TopologyGuess
from churchtune.errors import ChurchTuneError, ConsoleUnreachable
from churchtune.mixers.base import Feature, Support
from churchtune.mixers.x32.driver import LinkQuality, X32Driver, discover_consoles
from churchtune.mixers.x32.snapshot import parse_snapshot
from churchtune.mixers.x32.topology import infer_topology
from churchtune.platform.devsafe import is_dev_safe
from churchtune.storage.files import read_snapshot, write_snapshot
from churchtune.storage.models import SnapshotFile

LOSS_WARN = 0.05
SLOW_LINK_MS = 50.0
PROGRESS_EVERY = 250

ROLE_TEXT = {
    "top_l": "портал левый",
    "top_r": "портал правый",
    "top_mono": "портал (моно)",
    "sub": "саб",
    "fill": "фронтфил",
    "delay": "задержанные / балкон",
    "monitor": "монитор",
    "unused": "не колонки",
}
VARIANT_TEXT = {
    "a_tops_and_sub_on_own_paths": "A — порталы и саб на своих матрицах/шинах",
    "b_tops_on_main_sub_separate": "B — порталы с Main LR, саб отдельно",
    "c_tops_only": "C — порталы без саба",
    "sub_shares_main": "саб на Main LR вместе с порталами — кроссовер невозможен",
    "unknown": "порталы не найдены",
}
ISSUE_TEXT = {
    "several_subs": "несколько путей похожи на саб ({paths}); подтвердите, какой настоящий",
    "sub_on_mono_suggest_only": (
        "саб на Main Mono: адреса /main/m/* не проверены на железе — программа покажет, что "
        "выставить руками"
    ),
    "sub_on_bus_suggest_only": (
        "саб на шине {bus}: запись в шины не проверена на железе — только подсказки"
    ),
    "sub_not_fed_by_main": "матрица саба {matrix} не получает сигнал с Main LR",
    "crossover_hpf_suggest_only": (
        "порталы идут прямо с Main LR: ФВЧ кроссовера поставить некуда — только подсказка"
    ),
    "other_speakers_audible": (
        "{path} ({role}) включён и будет звучать во время замера; в MVP не настраивается"
    ),
    "other_speakers_found": "{path} ({role}) найден; в MVP не настраивается",
    "monitors_on_outputs": "мониторные шины на выходах: {paths} — свип не должен уйти в них",
    "confirm_roles": "{count} выход(ов) с низкой уверенностью — подтвердите роли",
}
FEATURE_TEXT = {
    Feature.MAIN_EQ: "EQ Main LR",
    Feature.MATRIX_EQ: "EQ матриц",
    Feature.MATRIX_MUTE: "mute матриц",
    Feature.MATRIX_FADER: "фейдеры матриц",
    Feature.MATRIX_POLARITY: "полярность матриц",
    Feature.OUTPUT_DELAY: "задержка выходов",
    Feature.BUS_EQ: "EQ шин",
    Feature.CHANNEL_MUTE: "mute каналов",
    Feature.MONO_EQ: "EQ Main Mono",
}


# ------------------------------------------------------------------ snapshot files
def to_file(snapshot: ConsoleSnapshot) -> SnapshotFile:
    """The stored form of a snapshot: identity and raw lines."""
    identity = snapshot.identity
    return SnapshotFile(
        taken_at=snapshot.taken_at,
        family=identity.family.value,
        model=identity.model,
        name=identity.name,
        firmware=identity.firmware,
        host=identity.host,
        raw=dict(snapshot.raw),
    )


def from_file(stored: SnapshotFile) -> ConsoleSnapshot:
    """Parse a stored snapshot with this version's parser."""
    identity = ConsoleIdentity(
        ConsoleFamily(stored.family), stored.model, stored.name, stored.firmware, stored.host
    )
    return parse_snapshot(identity, stored.raw, stored.taken_at)


# ------------------------------------------------------------------ printing
def _issue_text(issue: Issue) -> str:
    template = ISSUE_TEXT.get(issue.code, issue.code)
    return template.format(**dict(issue.params or {}))


def print_topology(guess: TopologyGuess) -> None:
    """Print the topology table, the variant and the issues."""
    say("   Выход      Путь         Имя                Роль                   Уверенность")
    for g in guess.outputs:
        say(
            f"   {g.kind.value:4} {g.number:>2}    {g.path.label():12} {g.name[:18]:18} "
            f"{ROLE_TEXT[g.role.value]:22} {g.confidence:.0%}"
        )
    say(f"   Вариант системы: {VARIANT_TEXT[guess.variant.value]}")
    for issue in guess.issues:
        say(f"   ! {_issue_text(issue)}")


def _print_link(quality: LinkQuality) -> None:
    if quality.median_ms is None:
        say(f"   ✗ связь: ни один из {quality.sent} запросов не получил ответа")
        return
    mark = "!" if quality.loss > LOSS_WARN or (quality.max_ms or 0) > SLOW_LINK_MS else "✓"
    say(
        f"   {mark} связь: {quality.sent} запросов, потерь {quality.loss:.0%}, "
        f"медиана {quality.median_ms:.1f} мс, максимум {quality.max_ms:.1f} мс"
    )


def _print_capabilities(driver: X32Driver) -> None:
    caps = driver.model.capabilities
    full = [text for f, text in FEATURE_TEXT.items() if caps.of(f) is Support.FULL]
    hints = [text for f, text in FEATURE_TEXT.items() if caps.of(f) is Support.SUGGEST_ONLY]
    say(f"   программа может записывать: {', '.join(full) or 'ничего'}")
    if hints:
        say(f"   только подсказки (не проверено на железе): {', '.join(hints)}")


def _print_usb(driver: X32Driver, snapshot: ConsoleSnapshot) -> None:
    usb = driver.usb_audio_map(snapshot)
    with_preamp = [i for i in usb.inputs if i.headamp is not None and i.headamp.phantom]
    for info in with_preamp:
        headamp = info.headamp
        assert headamp is not None  # noqa: S101 - filtered above
        say(
            f"   USB in {info.usb:>2} ← {info.source.label()} (headamp {headamp.index:03d}, +48V, "
            f"gain {headamp.gain_db:+.1f}), каналы {list(info.channels)}"
        )
    for out in usb.outputs:
        say(f"   USB out {out.usb:>2} → каналы {list(out.channels)}")


# ------------------------------------------------------------------ console access
def _find_console(settings: Settings) -> Settings:
    """Settings with a console address: the given one, or the only console discovery found."""
    if settings.venue.console_host:
        return settings
    say("   поиск пульта в сети (1,5 с)…")
    found = discover_consoles()
    if not found:
        hint = (
            " (dev-safe: широковещательный поиск выключен, укажите --emulator)"
            if (is_dev_safe())
            else ""
        )
        fail(
            "   ✗ пульт не найден. Укажите адрес: --console HOST"
            + hint
            + "\n     macOS: System Settings → Privacy & Security → Local Network — включите "
            "терминал; отключите VPN.",
            EXIT_UNREACHABLE,
        )
    for console in found:
        identity = console.identity
        say(f"   найден {identity.model} «{identity.name}» {identity.host} fw {identity.firmware}")
    if len(found) > 1:
        fail("   несколько пультов: укажите нужный через --console HOST", EXIT_UNREACHABLE)
    chosen = found[0]
    venue = replace(settings.venue, console_host=chosen.identity.host, console_port=chosen.port)
    return replace(settings, venue=venue)


def _read(settings: Settings, driver: X32Driver) -> tuple[ConsoleSnapshot, Path, float]:
    def progress(done: int, total: int) -> None:
        if done % PROGRESS_EVERY == 0 or done == total:
            say(f"   … {done}/{total}")

    start = time.monotonic()
    snapshot = driver.read_snapshot(progress)
    elapsed = time.monotonic() - start
    path = write_snapshot(settings.workspace, to_file(snapshot), settings.now())
    return snapshot, path, elapsed


def snapshot_command(ctx: typer.Context) -> None:
    """Read the whole console (read-only) and save the snapshot."""
    settings = _find_console(settings_of(ctx))
    with open_console(settings) as console:
        try:
            driver = X32Driver.connect(console.client)
        except ConsoleUnreachable as error:
            fail(f"✗ {error}", EXIT_UNREACHABLE)
        snapshot, path, elapsed = _read(settings, driver)
    say(
        f"✓ снимок: {len(snapshot.raw)} узлов за {elapsed:.1f} с → "
        f"{relative_to_home(path, settings.workspace)}"
    )
    if snapshot.unparsed:
        say(f"! не поняты {len(snapshot.unparsed)} узл.: {', '.join(snapshot.unparsed[:8])}")
    raise typer.Exit(EXIT_OK)


def doctor_command(
    ctx: typer.Context,
    snapshot_file: Annotated[
        Path | None,
        typer.Option("--from-file", help="Show the topology of a saved snapshot instead."),
    ] = None,
) -> None:
    """Check the computer and the console without any sound; read and explain the topology."""
    settings = settings_of(ctx)
    say("ctune doctor — проверка без звука, в пульт ничего не пишется")
    if snapshot_file is not None:
        snapshot = from_file(read_snapshot(snapshot_file))
        identity = snapshot.identity
        say(
            f"Снимок {snapshot.taken_at}: {identity.model} «{identity.name}» fw {identity.firmware}"
        )
        say("Топология")
        print_topology(infer_topology(snapshot))
        raise typer.Exit(EXIT_OK)
    say("1. Компьютер")
    if is_dev_safe():
        say("   ! режим разработки (dev-safe): только эмулятор на 127.0.0.1 и симулятор звука")
    say(f"   аудио: {settings.audio} (подбор устройства и разрешения — этап M2)")
    say("2. Пульт")
    settings = _find_console(settings)
    with open_console(settings) as console:
        try:
            driver = X32Driver.connect(console.client)
        except ChurchTuneError as error:  # silent console or another model
            fail(f"   ✗ {error}", EXIT_UNREACHABLE)
        identity = driver.model.identity
        fw_mark = "✓" if driver.model.capabilities.verified_firmware else "!"
        say(f"   ✓ {identity.model} «{identity.name}» на {identity.host}")
        say(f"   {fw_mark} прошивка {identity.firmware}")
        _print_link(driver.link_quality())
        say("3. Снимок")
        snapshot, path, elapsed = _read(settings, driver)
        say(
            f"   ✓ {len(snapshot.raw)} узлов за {elapsed:.1f} с → "
            f"{relative_to_home(path, settings.workspace)}"
        )
        if snapshot.clock is not None:
            say(f"   частота {snapshot.clock.fs_hz} Гц, клок {snapshot.clock.source}")
        say(
            "   Сохраните сцену на пульте: SCENES → свободный слот → SAVE, имя "
            f"before-ctune-{datetime.now().strftime('%Y-%m-%d')}. Это страховка без компьютера."
        )
        say("4. Топология")
        print_topology(driver.infer_topology(snapshot))
        say("5. USB-карта")
        _print_usb(driver, snapshot)
        say("6. Возможности")
        _print_capabilities(driver)
    raise typer.Exit(EXIT_OK)


def emulator_command(
    model: Annotated[str, typer.Option("--model", help="Model string, e.g. X32P or M32.")] = "X32P",
    fixture: Annotated[
        Path | None, typer.Option("--fixture", help="A /node dump to seed the state from.")
    ] = None,
    port: Annotated[int, typer.Option("--port", help="UDP port; a free one when 0.")] = 0,
    latency_ms: Annotated[float, typer.Option("--latency-ms", help="Delay per reply.")] = 0.0,
) -> None:
    """Run the X32/M32 emulator on 127.0.0.1 until Ctrl+C (development)."""
    from churchtune_emulator.x32 import X32Emulator  # noqa: PLC0415 - dev tool, loaded on demand

    emulator = X32Emulator(fixture, model=model, latency_s=latency_ms / 1000.0, port=port)
    say(f"эмулятор {model} на 127.0.0.1:{emulator.port} (Ctrl+C — выход)")
    say(f"  CTUNE_DEV_SAFE=1 ctune --console 127.0.0.1:{emulator.port} doctor")
    try:
        emulator.serve_forever()
    except KeyboardInterrupt:
        say("эмулятор остановлен")
    raise typer.Exit(EXIT_OK)
