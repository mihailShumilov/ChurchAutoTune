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
from churchtune.domain.snapshot import SignalRef
from churchtune.measure.config import SweepConfig
from churchtune.measure.junction import JunctionBlock
from churchtune.mixers.base import DiscoveredConsole, MixerDriver
from churchtune.storage.models import PackageModel
from churchtune.storage.paths import Workspace
from churchtune.wizard.protocol import Prompter
from churchtune.wizard.state import StateBox

# Sample rate of every measurement and of the EQ model (the console card runs at 48 kHz).
FS_HZ = 48000.0


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


@dataclass(frozen=True, slots=True)
class PlannedWrite:
    """One console write as the person sees it.

    Attributes:
        path: The console parameter (for logs and experts).
        strip: The strip it belongs to.
        band: EQ band number, or None for a strip parameter.
        param: ``type``, ``freq``, ``gain``, ``q``, ``eq_on``, ``fader`` or ``on``.
        before: Its value now, as the console shows it (for logs and experts).
        after: Its value after the write, on the console's grid (for logs and experts).
        before_value: The value now in plain units: Hz, dB, Q, the type name, or on/off.
        after_value: The value after the write in the same units.
    """

    path: str
    strip: SignalRef
    band: int | None
    param: str
    before: str
    after: str
    before_value: float | bool | str = ""
    after_value: float | bool | str = ""


@dataclass(frozen=True, slots=True)
class WritePlan:
    """Writes ready for a typed confirmation; nothing is sent yet.

    Attributes:
        writes: The writes in the order they will be sent (never louder in between).
        token: The port's own plan, handed back to ``apply`` unchanged.
        already_old: Undo only: parameters that already have their old value.
        changed_by_hand: Undo only: parameters changed since; they are left alone.
    """

    writes: tuple[PlannedWrite, ...]
    token: object
    already_old: tuple[str, ...] = ()
    changed_by_hand: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WriteOutcome:
    """How a confirmed plan went.

    Attributes:
        undo_path: The undo file, on disk before the first write.
        sent: Writes sent and confirmed by readback.
        complete: Every write was sent and confirmed.
        stopped: The person stopped it between writes.
        failed_path: The first write that did not read back, if any.
        still_muted: Speaker matrices the plan muted for a moment and could not switch back on.
    """

    undo_path: Path
    sent: int
    complete: bool
    stopped: bool = False
    failed_path: str | None = None
    still_muted: tuple[int, ...] = ()


class MuteSession(Protocol):
    """The temporary matrix mutes of one junction round (SPEC S10)."""

    def switch(self, block: JunctionBlock) -> None:
        """Put the mutes of a block in place, confirmed by readback.

        Raises:
            UnsafeWrite: A switch was refused or did not read back.
            ConsoleUnreachable: The console did not answer.
        """
        ...

    def restore(self) -> bool:
        """Put every original switch back; True when confirmed (the pending file is gone)."""
        ...


class WritePort(Protocol):
    """The only way the wizard changes the console (SPEC S12, §9.2-9.3).

    The port reads the console fresh, validates every write against the write scopes, orders the
    writes so nothing is louder in between, writes the undo file before the first write and reads
    every write back. The wizard only shows the plan and asks for the typed word.
    """

    def plan_packages(self, packages: Sequence[PackageModel]) -> WritePlan:
        """The writes of the chosen packages from a fresh read.

        Raises:
            UnsafeWrite: The console changed since the measurement (``mismatches`` parameter),
                or a write is outside the write scopes.
        """
        ...

    def plan_undo(self, undo_path: Path) -> WritePlan:
        """The writes that put an undo file's old values back where nobody changed them.

        Raises:
            UnsafeWrite: The undo file is damaged or names a write outside the scopes.
        """
        ...

    def apply(  # noqa: PLR0913 - every input of a write is explicit
        self,
        plan: WritePlan,
        typed: str,
        expected: str,
        undo_path: Path,
        should_stop: Callable[[], bool],
        *,
        reverts: Path | None = None,
    ) -> WriteOutcome:
        """Send a plan after the typed word; the undo file is written first.

        Raises:
            UnsafeWrite: The word does not match or the plan was refused; nothing was sent.
        """
        ...

    def start_junction_mutes(
        self, sub: Sequence[int], tops: Sequence[int], typed: str, expected: str
    ) -> MuteSession:
        """Read the matrix switches fresh and record them before the first temporary mute.

        Raises:
            UnsafeWrite: The word does not match, the matrices are not distinct, or mutes of
                an earlier measurement are still pending (``pending``).
            ConsoleUnreachable: The console did not answer.
        """
        ...

    def pending_mutes(self) -> WritePlan | None:
        """The writes that put the mutes of an interrupted measurement back; None if none."""
        ...

    def clear_settled_mutes(self) -> bool:
        """Remove the pending record when the console already has every original switch.

        Raises:
            ConsoleUnreachable: The console did not answer.
        """
        ...

    def restore_pending_mutes(self, plan: WritePlan, typed: str, expected: str) -> None:
        """Put pending mutes back as shown, after a fresh read; the record goes after it.

        Raises:
            UnsafeWrite: The word does not match or the console changed (``mismatches``).
            ConsoleUnreachable: The console did not answer.
        """
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
        writes: Writes to the connected console (None: this run never writes).
        words: A message key as text in the session's language (typed confirmation words).
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
    writes: WritePort | None = None
    words: Callable[[str], str] = str

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
