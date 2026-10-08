"""What every command shares: settings, the console connection, prompts and exit codes.

Transitional expert CLI (docs/adr/0003): operator texts are Russian literals until the Fluent
catalogues arrive in M3.
"""

import sys
import tomllib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import NoReturn

import typer

from churchtune.audio.base import AudioBackend
from churchtune.audio.factory import open_backend
from churchtune.domain.eq import SystemEq
from churchtune.domain.venue import Venue
from churchtune.errors import ChurchTuneError, ConsoleUnreachable, DevSafeViolation
from churchtune.mixers.x32.client import X32Client
from churchtune.mixers.x32.nodes import read_main_eq
from churchtune.mixers.x32.params import X32EqGrid, X32Formats
from churchtune.mixers.x32.transport import X32_PORT, UdpTransport
from churchtune.storage.paths import Workspace

# Exit codes (SPEC §12).
EXIT_OK = 0
EXIT_USER = 1  # cancelled, bad input, a measurement that must be repeated
EXIT_UNREACHABLE = 2  # no console or no audio device
EXIT_REFUSED = 3  # a safety refusal
EXIT_READBACK = 4  # a write did not read back
EXIT_INTERRUPTED = 130

# The console's USB card runs at 48 kHz (SPEC §3.1).
FS_HZ = 48000.0
VENUE_FILE = "venue.toml"
PING_TIMEOUT_S = 1.0
FORMATS = X32Formats()
EQ_GRID = X32EqGrid()


def say(*parts: object) -> None:
    """Print a line for the operator."""
    typer.echo(" ".join(str(p) for p in parts))


def ask(prompt: str) -> str:
    """Ask the operator; end of input counts as an empty answer (which means no)."""
    typer.echo(prompt, nl=False)
    line = sys.stdin.readline()
    if not line.endswith("\n"):
        typer.echo("")
    return line.rstrip("\n")


def fail(message: str, code: int = EXIT_USER) -> NoReturn:
    """Print a message and leave with an exit code."""
    say(message)
    raise typer.Exit(code)


@dataclass(frozen=True, slots=True)
class Settings:
    """Global options of one run.

    Attributes:
        workspace: Where files live.
        venue: The hall's values from ``venue.toml`` and the options.
        audio: ``fake`` or part of an audio device name.
        now: Clock, injectable for tests.
    """

    workspace: Workspace
    venue: Venue
    audio: str
    now: Callable[[], datetime] = datetime.now

    @property
    def console_label(self) -> str:
        """The console address for messages."""
        return f"{self.venue.console_host}:{self.venue.console_port}"

    def timestamp(self) -> str:
        """Current time as stored in files."""
        return self.now().isoformat(timespec="seconds")


def load_venue(workspace: Workspace) -> Venue:
    """Read ``venue.toml`` from the home directory; an empty venue when there is none.

    Raises:
        ValueError: The file has a key the venue model does not know.
    """
    path = workspace.root / VENUE_FILE
    if not path.exists():
        return Venue()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    known = {f.name for f in fields(Venue)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f"unknown keys in {path}: {', '.join(unknown)}")
    if "playback_channels" in data:
        data["playback_channels"] = tuple(data["playback_channels"])
    return Venue(**data)


def parse_console(address: str) -> tuple[str, int]:
    """Split ``HOST[:PORT]`` into host and port (the console's default port when omitted)."""
    host, _, port = address.partition(":")
    if not host or (port and not port.isdigit()):
        raise typer.BadParameter(f"expected HOST[:PORT], got {address!r}")
    return host, int(port) if port else X32_PORT


@dataclass(slots=True)
class Console:
    """An open connection: the transport and the read-only client that share it."""

    transport: UdpTransport
    client: X32Client

    def alive(self, timeout_s: float = PING_TIMEOUT_S) -> bool:
        """True when the console answers ``/xinfo``."""
        return self.client.ping(timeout_s) is not None


@contextmanager
def open_console(settings: Settings) -> Iterator[Console]:
    """Open the console named in the settings; closed on leaving the block.

    Raises:
        typer.Exit: No console address is known (exit code 1).
        DevSafeViolation: Dev-safe mode is on and the address is not loopback.
    """
    venue = settings.venue
    if not venue.console_host:
        fail("адрес пульта не задан: --console HOST[:PORT], CTUNE_CONSOLE или venue.toml")
    transport = UdpTransport(venue.console_host, venue.console_port or X32_PORT)
    try:
        yield Console(transport, X32Client(transport))
    finally:
        transport.close()


@contextmanager
def reachable_console(settings: Settings) -> Iterator[Console]:
    """Like :func:`open_console`, but leaves with exit code 2 when the console is silent."""
    with open_console(settings) as console:
        try:
            alive = console.alive()
        except ConsoleUnreachable as error:
            fail(f"!! {error}", EXIT_UNREACHABLE)
        if not alive:
            fail(f"нет ответа от {settings.console_label} — см. ctune check", EXIT_UNREACHABLE)
        yield console


def try_read_main_eq(settings: Settings, *, quiet: bool = False) -> SystemEq | None:
    """Read the Main LR EQ if the console can be reached; None otherwise."""
    if not settings.venue.console_host:
        return None
    try:
        with open_console(settings) as console:
            if not console.alive():
                raise ConsoleUnreachable(f"нет ответа от {settings.console_label}")
            return read_main_eq(console.client)
    except DevSafeViolation:
        raise  # a refused console is never a quiet "not read"
    except ChurchTuneError as error:
        if not quiet:
            say(f"!! EQ Main LR не прочитан: {error}")
        return None


def open_audio(settings: Settings) -> AudioBackend:
    """Open the audio backend of the settings.

    The simulator plays through the console's current Main LR EQ, so apply and verify can be
    rehearsed against the emulator.
    """
    return open_backend(
        settings.audio,
        fs_hz=FS_HZ,
        mic_input=settings.venue.mic_usb_input or 1,
        eq_provider=lambda: try_read_main_eq(settings, quiet=True),
    )


def settings_of(ctx: typer.Context) -> Settings:
    """The settings stored by the root callback."""
    settings = ctx.obj
    if not isinstance(settings, Settings):
        raise TypeError("the CLI context holds no settings")
    return settings


def relative_to_home(path: Path, workspace: Workspace) -> Path:
    """A path as shown to the operator: relative to the home directory when inside it."""
    return path.relative_to(workspace.root) if path.is_relative_to(workspace.root) else path
