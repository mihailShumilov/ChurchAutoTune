"""``ctune`` / ``ctune wizard`` / ``ctune resume``: the guided wizard.

The CLI builds the concrete console and audio ports the wizard needs (the wizard itself knows only
``mixers.base`` and ``audio.base``) and picks the user interface: the Textual app in a terminal,
a plain line interface otherwise (``--plain``, pipes, screen readers).
"""

import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import typer

from churchtune.audio.base import MultiChannelBackend
from churchtune.audio.devices import list_devices, simulator_device
from churchtune.audio.factory import open_multi_backend
from churchtune.cli.apply_packages import X32WritePort
from churchtune.cli.context import EXIT_OK, EXIT_USER, FS_HZ, Console, fail, settings_of
from churchtune.errors import DevSafeViolation, UnsafeWrite
from churchtune.i18n import LANGUAGES, Translator, detect_language
from churchtune.mixers.base import DiscoveredConsole, MixerDriver
from churchtune.mixers.x32.client import X32Client
from churchtune.mixers.x32.driver import X32Driver, discover_consoles
from churchtune.mixers.x32.transport import X32_PORT, UdpTransport
from churchtune.platform.devsafe import is_dev_safe
from churchtune.storage.files import read_wizard_state
from churchtune.wizard.engine import RunResult
from churchtune.wizard.protocol import (
    Answer,
    Kind,
    Nav,
    Prompt,
    Prompter,
    StepHeader,
    Text,
    render_text,
    split_question,
)
from churchtune.wizard.run import latest_session, run_wizard, start
from churchtune.wizard.services import AudioDeviceInfo, Services


@dataclass(slots=True)
class X32ConsolePort:
    """Discovery and read-only connections to X32/M32 consoles; closes them at the end."""

    transports: list[UdpTransport] = field(default_factory=list)
    connections: list[tuple[str, Console]] = field(default_factory=list)

    def discover(self, extra: Sequence[tuple[str, int]]) -> list[DiscoveredConsole]:
        """Broadcast (outside dev-safe) plus the known addresses."""
        return discover_consoles(extra=tuple(extra))

    def connect(self, host: str, port: int) -> MixerDriver:
        """A read-only driver for one console."""
        transport = UdpTransport(host, port)
        self.transports.append(transport)
        client = X32Client(transport)
        driver = X32Driver.connect(client)
        self.connections.append((host, Console(transport, client)))
        return driver

    def last(self) -> tuple[str, Console]:
        """The console connected last: the one the wizard works with.

        Raises:
            UnsafeWrite: No console is connected.
        """
        if not self.connections:
            raise UnsafeWrite("no console is connected")
        return self.connections[-1]

    def close(self) -> None:
        """Close every transport."""
        for transport in self.transports:
            transport.close()


@dataclass(slots=True)
class DeviceAudioPort:
    """The measurement audio device (the simulated hall in development)."""

    def devices(self) -> list[AudioDeviceInfo]:
        """Candidate devices, the console card first."""
        try:
            found = list_devices()
        except DevSafeViolation:
            found = [simulator_device()]
        return [AudioDeviceInfo(d.name, d.problems) for d in found]

    def open(self, name: str) -> MultiChannelBackend:
        """Open a device by name."""
        return open_multi_backend(name, fs_hz=FS_HZ)


METER_EVERY_S = 1.0
YES_WORDS = frozenset({"y", "yes", "д", "да", "т", "так"})
NO_WORDS = frozenset({"n", "no", "н", "нет", "ні", "ни"})
# Texts that name Space, a key of the full-screen app only.
PLAIN_TEXTS = {"sound-stop-keys": "sound-stop-ctrl-c", "sound-playing": "sound-playing-plain"}


@dataclass(slots=True)
class LinePrompter:
    """A plain line interface: one prompt after another, answers typed on standard input.

    For pipes and screen readers: whole lines only, table headers spoken, the default named.
    """

    translate: Translator
    clock: Callable[[], float] = time.monotonic
    last_meter: float = field(default=-METER_EVERY_S)

    def _text(self, text: Text) -> str:
        key = PLAIN_TEXTS.get(text.key, text.key)
        return render_text(Text(key, text.args), self.translate)

    def header(self, header: StepHeader) -> None:
        """``== Step 3 of 12 · Console ==``."""
        title = self._text(header.title)
        step = self._text(Text("common-step", {"number": header.number, "total": header.total}))
        typer.echo(f"\n== {step} · {title} ==")

    def _show(self, prompt: Prompt) -> None:
        before, question = split_question(prompt)
        for paragraph in before:
            typer.echo(self._text(paragraph))
        if prompt.table is not None:
            typer.echo("  " + " | ".join(self._text(h) for h in prompt.table.headers))
            for row in prompt.table.rows:
                typer.echo("  " + " | ".join(self._text(c) for c in row))
        if prompt.picture:
            typer.echo(prompt.picture)
        for paragraph in question:
            typer.echo(self._text(paragraph))
        recommended = self._text(Text("common-recommended"))
        for number, option in enumerate(prompt.options, start=1):
            mark = f" {recommended}" if option.recommended else ""
            typer.echo(f"  {number}) {self._text(option.label)}{mark}")
        if prompt.kind is Kind.INPUT and prompt.default:
            typer.echo(self._text(Text("common-default", {"value": prompt.default})))

    def _read(self) -> str | None:
        typer.echo("> ", nl=False)
        line = sys.stdin.readline()
        if not line:
            return None
        if not sys.stdin.isatty():
            typer.echo(line.rstrip("\n"))  # piped answers are not echoed by a terminal
        return line.strip()

    def _option(self, prompt: Prompt, value: str) -> str | None:
        """The option id a typed value names, or None when it names none."""
        options = prompt.options
        word = value.casefold()
        if value.isdigit() and 1 <= int(value) <= len(options):
            return options[int(value) - 1].id
        if prompt.kind is Kind.CONFIRM and len(options) == 2:  # noqa: PLR2004 - yes and no
            if word in YES_WORDS:
                return options[0].id
            if word in NO_WORDS:
                return options[1].id
        for option in options:
            if word in (option.id.casefold(), self._text(option.label).casefold()):
                return option.id
        return None

    def ask(self, prompt: Prompt) -> Answer:
        """Print the prompt and read lines; ``b`` is back, ``q`` is save and quit.

        A choice that names no option is asked again instead of taking the default.
        """
        self._show(prompt)
        hint = self._text(Text(f"keys-{prompt.kind.value}"))
        while True:
            typer.echo(hint)
            value = self._read()
            if value is None or value.lower() == "q":
                return Answer(nav=Nav.QUIT)
            if value.lower() == "b" and prompt.can_back:
                return Answer(nav=Nav.BACK)
            if not prompt.options or not value:
                return Answer(value)
            chosen = self._option(prompt, value)
            if chosen is not None:
                return Answer(chosen)
            typer.echo(self._text(Text("plain-invalid-choice", {"count": len(prompt.options)})))

    def progress(self, label: Text, done: int, total: int) -> None:
        """Whole lines only (screen readers): the start and the end of an operation."""
        if done == 0 or done >= total:
            typer.echo(f"  {self._text(label)} {done}/{total}")

    def meter(self, levels_dbfs: Mapping[str, float]) -> None:
        """Live levels as whole lines, at most one per second."""
        now = self.clock()
        if now - self.last_meter < METER_EVERY_S:
            return
        self.last_meter = now
        shown = "  ".join(
            self._text(Text("common-mic-number", {"number": number}))
            + ": "
            + self._text(Text("common-dbfs", {"level": round(level, 1)}))
            for number, level in levels_dbfs.items()
        )
        typer.echo(f"  {shown}")


def build_services(ctx: typer.Context) -> tuple[Services, X32ConsolePort]:
    """The ports of this run."""
    settings = settings_of(ctx)
    console = X32ConsolePort()
    venue = settings.venue
    known = ((venue.console_host, venue.console_port or X32_PORT),) if venue.console_host else ()
    services = Services(
        console=console,
        audio=DeviceAudioPort(),
        workspace=settings.workspace,
        now=settings.now,
        stop=threading.Event(),
        dev_safe=is_dev_safe(),
        audio_name=settings.audio,
        known_consoles=known,
        default_port=X32_PORT,
        writes=X32WritePort(console.last, settings.now),
    )
    return services, console


def _language(lang: str | None) -> str:
    chosen = lang or detect_language()
    if chosen not in LANGUAGES:
        fail(f"language must be one of {', '.join(LANGUAGES)}")
    return chosen


ENGINE_JOIN_S = 10.0


def _run(
    language: str,
    plain: bool,
    services: Services,
    begin: Callable[[Prompter], RunResult],
) -> RunResult:
    """Run the wizard in the TUI, or in the line interface when asked or without a terminal.

    After the TUI closes, the engine thread is given time to stop and save before the console
    connection closes under it.
    """
    translate = Translator(language)
    services.words = translate
    if plain or not sys.stdin.isatty() or not sys.stdout.isatty():
        result: RunResult | None = begin(LinePrompter(translate))
    else:
        from churchtune.app.app import WizardApp  # noqa: PLC0415 - Textual only for the TUI

        app = WizardApp(translate, begin, services.stop, services.shutdown)
        result = app.run()
        app.engine_done.wait(ENGINE_JOIN_S)
    if result is None or not result.finished:
        typer.echo(translate("session-saved-resume"))
        raise typer.Exit(EXIT_USER)
    return result


def _language_of(directory: Path, lang: str | None) -> str:
    """The language asked for, else the one the session was started in."""
    if lang is not None:
        return _language(lang)
    try:
        return _language(read_wizard_state(directory).lang)
    except (ValueError, OSError):
        return _language(None)


def wizard_command(
    ctx: typer.Context,
    lang: Annotated[str | None, typer.Option("--lang", help="en, ru or uk.")] = None,
    plain: Annotated[
        bool, typer.Option("--plain", help="Line interface instead of the TUI.")
    ] = False,
) -> None:
    """Guided measurement and tuning, step by step (the default command)."""
    language = _language(lang)
    services, console = build_services(ctx)
    try:
        _run(language, plain, services, lambda p: start(p, services, language))
    finally:
        console.close()
    raise typer.Exit(EXIT_OK)


def resume_command(
    ctx: typer.Context,
    session: Annotated[
        Path | None, typer.Argument(help="Session directory; the newest by default.")
    ] = None,
    lang: Annotated[str | None, typer.Option("--lang", help="en, ru or uk.")] = None,
    plain: Annotated[
        bool, typer.Option("--plain", help="Line interface instead of the TUI.")
    ] = False,
) -> None:
    """Continue a saved wizard session after its last completed step."""
    services, console = build_services(ctx)
    directory = session or latest_session(services)
    if directory is None:
        console.close()
        translate = Translator(_language(lang))
        fail(translate("err-no-session"))
    language = _language_of(directory, lang)
    try:
        state = read_wizard_state(directory)
    except (ValueError, OSError):
        console.close()
        translate = Translator(language)
        fail(translate("err-bad-session"))
    try:
        _run(language, plain, services, lambda p: run_wizard(p, services, state, directory))
    finally:
        console.close()
    raise typer.Exit(EXIT_OK)
