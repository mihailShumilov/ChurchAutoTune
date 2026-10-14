"""The console driver contract (SPEC §6.3, skill ``mixer-driver``).

A driver discovers consoles, reads a full snapshot without changing anything, infers the speaker
topology, maps the USB audio card and streams meters. Writes go through a scoped writer that needs
an authorization from ``safety`` (M5). The wizard depends on this module only, never on a concrete
console family. Discovery is a function of each family (``mixers/x32/driver.discover_consoles``)
that the entry point hands to the wizard; it is not a method because it runs before any console is
connected (ADR 0006).
"""

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from churchtune.domain.eq import EqQuantizer
from churchtune.domain.snapshot import (
    ConsoleIdentity,
    ConsoleSnapshot,
    Headamp,
    InputRef,
    UsbAudioMap,
)
from churchtune.domain.topology import TopologyGuess

ProgressCb = Callable[[int, int], None]


class Feature(StrEnum):
    """A kind of console write the tool may want to make."""

    MAIN_EQ = "main_eq"
    MAIN_MUTE = "main_mute"
    MATRIX_EQ = "matrix_eq"
    MATRIX_MUTE = "matrix_mute"
    MATRIX_FADER = "matrix_fader"
    MATRIX_POLARITY = "matrix_polarity"
    OUTPUT_DELAY = "output_delay"
    BUS_EQ = "bus_eq"
    BUS_MUTE = "bus_mute"
    BUS_FADER = "bus_fader"
    MONO_EQ = "mono_eq"
    MONO_MUTE = "mono_mute"
    MONO_FADER = "mono_fader"
    CHANNEL_MUTE = "channel_mute"


class Support(StrEnum):
    """How far a feature is supported on a model and firmware."""

    # Written and read back on real hardware of this model and firmware.
    FULL = "full"
    # Addresses known only from documentation: the wizard shows what to set by hand.
    SUGGEST_ONLY = "suggest_only"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Capabilities:
    """Support of every feature on one model and firmware.

    Attributes:
        support: Support level by feature; a missing feature is ``none``.
        verified_firmware: True when the firmware belongs to the line the protocol notes describe
            (4.x); False means "not checked at all" and earns a warning.
    """

    support: Mapping[Feature, Support]
    verified_firmware: bool

    def of(self, feature: Feature) -> Support:
        """Support level of one feature."""
        return self.support.get(feature, Support.NONE)

    def can_write(self, feature: Feature) -> bool:
        """True only for features verified on hardware."""
        return self.of(feature) is Support.FULL


@dataclass(frozen=True, slots=True)
class ConsoleModel:
    """A console as the driver sees it.

    Attributes:
        identity: The console's description of itself.
        capabilities: What may be written on it.
    """

    identity: ConsoleIdentity
    capabilities: Capabilities


@dataclass(frozen=True, slots=True)
class DiscoveredConsole:
    """A console that answered discovery.

    Attributes:
        identity: Model, name, firmware and address from the answer.
        port: UDP port it answered from.
    """

    identity: ConsoleIdentity
    port: int


class MeterSet(StrEnum):
    """Groups of meters a driver can stream."""

    # Input channels, aux inputs, FX returns, buses, matrices (X32 ``/meters/0``).
    STRIPS = "strips"


@dataclass(frozen=True, slots=True)
class MeterFrame:
    """One frame of linear meter levels (1.0 = 0 dBFS).

    Attributes:
        channels: Input channels, index 0 = channel 1.
        buses: Mix buses.
        matrices: Matrices.
    """

    channels: tuple[float, ...]
    buses: tuple[float, ...]
    matrices: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class LinkQuality:
    """Round trips of repeated identity requests (SPEC S2).

    Attributes:
        sent: Requests sent.
        answered: Requests answered in time.
        median_ms: Median round trip of the answered requests; None when none was answered.
        max_ms: Slowest answered round trip; None when none was answered.
    """

    sent: int
    answered: int
    median_ms: float | None
    max_ms: float | None

    @property
    def loss(self) -> float:
        """Share of unanswered requests, 0..1."""
        return 1.0 - self.answered / self.sent if self.sent else 0.0


class MixerDriver(Protocol):
    """What the wizard needs from a console family."""

    @property
    def model(self) -> ConsoleModel:
        """Model, firmware and capabilities of the connected console."""
        ...

    @property
    def eq_grid(self) -> EqQuantizer:
        """The console's EQ value grid: every designed value is snapped to it."""
        ...

    def read_snapshot(self, progress: ProgressCb | None = None) -> ConsoleSnapshot:
        """Read everything needed without changing anything."""
        ...

    def parse_snapshot(
        self, identity: ConsoleIdentity, raw: Mapping[str, str], taken_at: str
    ) -> ConsoleSnapshot:
        """Rebuild a snapshot from stored raw lines with this version's parser. Pure."""
        ...

    def link_quality(self, count: int, timeout_s: float) -> LinkQuality:
        """Round trips and loss of ``count`` identity requests."""
        ...

    def infer_topology(self, snapshot: ConsoleSnapshot) -> TopologyGuess:
        """Guess the speaker roles from a snapshot. Pure."""
        ...

    def usb_audio_map(self, snapshot: ConsoleSnapshot) -> UsbAudioMap:
        """Map the USB audio card from a snapshot. Pure."""
        ...

    def headamp_of(self, snapshot: ConsoleSnapshot, source: InputRef) -> Headamp | None:
        """The preamp behind a physical input, or None (card, outputs, unknown). Pure."""
        ...

    def meters(self, which: MeterSet, duration_s: float) -> Iterator[MeterFrame]:
        """Stream meter frames for ``duration_s`` seconds."""
        ...
