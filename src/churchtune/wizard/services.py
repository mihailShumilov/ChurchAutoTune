"""What the wizard needs from the outside world, as ports the entry point fills in.

The wizard knows the console only through ``mixers.base`` and the audio only through
``audio.base``; the CLI builds the concrete X32 driver and audio backend and hands them over here.
Tests hand over the emulator and the simulated hall.
"""

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from churchtune.audio.base import MultiChannelBackend
from churchtune.measure.config import SweepConfig
from churchtune.mixers.base import DiscoveredConsole, MixerDriver
from churchtune.storage.paths import Workspace
from churchtune.wizard.protocol import Prompter
from churchtune.wizard.state import StateBox


class ConsolePort(Protocol):
    """Finding and connecting a console."""

    def discover(self, extra: Sequence[tuple[str, int]]) -> list[DiscoveredConsole]:
        """Consoles that answer discovery, plus the given known addresses."""
        ...

    def connect(self, host: str, port: int) -> MixerDriver:
        """Connect to one console (read-only)."""
        ...


@dataclass(frozen=True, slots=True)
class AudioDeviceInfo:
    """An audio device as the Mac check shows it.

    Attributes:
        name: Device name.
        problems: Codes: ``channels`` (fewer than 32), ``sample_rate`` (not 48 kHz).
    """

    name: str
    problems: tuple[str, ...]


class AudioPort(Protocol):
    """The measurement audio device."""

    def devices(self) -> list[AudioDeviceInfo]:
        """Candidate devices, the console card first."""
        ...

    def open(self, name: str) -> MultiChannelBackend:
        """Open a device by name (``fake`` for the simulated hall)."""
        ...


@dataclass(slots=True)
class Services:
    """Everything outside the wizard.

    Attributes:
        console: Finds and connects consoles.
        audio: Lists and opens audio devices.
        workspace: Where files live.
        now: Wall clock.
        stop: The UI's stop flag (Ctrl+C / Space): silence now; cleared before each step and
            each new sound, never after ``shutdown``.
        dev_safe: True in development (emulator and simulator only).
        audio_name: Audio device of this run (``fake`` in development).
        sweep: Measurement sweep (shorter in tests).
        known_consoles: Addresses given on the command line (``--console``, ``--emulator``).
        default_port: Console port when an address is typed without one.
        sleep: Waits (the countdown before a sweep); instant in tests.
        shutdown: Set by the UI when it goes away; the stop flag then stays set for good.
    """

    console: ConsolePort
    audio: AudioPort
    workspace: Workspace
    now: Callable[[], datetime]
    stop: threading.Event
    dev_safe: bool
    audio_name: str = "fake"
    sweep: SweepConfig = field(default_factory=SweepConfig)
    known_consoles: tuple[tuple[str, int], ...] = ()
    default_port: int = 10023
    sleep: Callable[[float], None] = time.sleep
    shutdown: threading.Event = field(default_factory=threading.Event)

    def reset_stop(self) -> bool:
        """Clear the stop flag for what comes next; False (flag kept) once the UI is gone.

        The UI sets ``shutdown`` before ``stop``, so a clear that races with the UI going away
        is undone here and no sound can start without a screen.
        """
        self.stop.clear()
        if self.shutdown.is_set():
            self.stop.set()
            return False
        return True


@dataclass(slots=True)
class WizardContext:
    """What a step receives.

    Attributes:
        prompter: The user interface.
        box: The current state.
        services: The outside world.
        session_dir: Directory of this session.
        driver: The connected console, once step S2 connected it.
        backend: The open audio device, once a measuring step opened it.
        mics_checked: The console was read again in this run and no measurement microphone
            reaches the speakers (checked before the first sound).
    """

    prompter: Prompter
    box: StateBox
    services: Services
    session_dir: Path
    driver: MixerDriver | None = None
    backend: MultiChannelBackend | None = None
    mics_checked: bool = False
