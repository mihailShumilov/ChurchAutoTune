"""Console-neutral snapshot of a mixing console, as read before anything else happens (SPEC §6.3).

A snapshot describes inputs, buses, matrices, mains, physical outputs, the USB card routing and the
clock. It keeps the original reply lines in ``raw`` so a snapshot can be parsed again by a newer
version, compared, and used as the first restore point. Pure data.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from churchtune.domain.eq import Band


class ConsoleFamily(StrEnum):
    """Console families with a driver."""

    X32 = "x32"
    M32 = "m32"


@dataclass(frozen=True, slots=True)
class ConsoleIdentity:
    """Who answered: the console's own description of itself.

    Attributes:
        family: Console family.
        model: Model string as the console reports it (``X32P``, ``M32R``).
        name: Console name set by the owner.
        firmware: Firmware version string (``4.09``).
        host: IPv4 address the console answered from.
    """

    family: ConsoleFamily
    model: str
    name: str
    firmware: str
    host: str


class SourceKind(StrEnum):
    """Kind of signal that feeds a physical output or a matrix."""

    OFF = "off"
    MAIN_L = "main_l"
    MAIN_R = "main_r"
    MONO = "mono"
    BUS = "bus"
    MATRIX = "matrix"
    DIRECT_CHANNEL = "direct_channel"
    DIRECT_AUX = "direct_aux"
    DIRECT_FX = "direct_fx"
    MONITOR = "monitor"
    TALKBACK = "talkback"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SignalRef:
    """A mix signal of the console: Main L, bus 3, matrix 5, direct out of channel 10.

    Attributes:
        kind: Kind of signal.
        number: Bus, matrix or channel number; 0 when the kind has no number.
    """

    kind: SourceKind
    number: int = 0

    def label(self) -> str:
        """Short text such as ``matrix 3`` or ``main_l``."""
        return f"{self.kind.value} {self.number}" if self.number else self.kind.value


class InputKind(StrEnum):
    """Where an audio input of the console physically comes from."""

    LOCAL = "local"
    AES50_A = "aes50_a"
    AES50_B = "aes50_b"
    CARD = "card"
    AUX_IN = "aux_in"
    OUTPUT = "output"
    P16 = "p16"
    OFF = "off"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class InputRef:
    """A physical audio source: local input 5, AES50-A input 10, USB card output 1.

    Attributes:
        kind: Where the signal enters the console.
        number: 1-based number inside that kind; 0 for ``off`` and ``unknown``.
    """

    kind: InputKind
    number: int = 0

    def label(self) -> str:
        """Short text such as ``aes50_a 10``."""
        return f"{self.kind.value} {self.number}" if self.number else self.kind.value


@dataclass(frozen=True, slots=True)
class Headamp:
    """A microphone preamp.

    Attributes:
        index: Console headamp index (0-based on the X32).
        gain_db: Preamp gain in dB.
        phantom: True when +48 V is on.
    """

    index: int
    gain_db: float
    phantom: bool


@dataclass(frozen=True, slots=True)
class Send:
    """A send from a strip to a bus or a matrix.

    Attributes:
        target: Number of the receiving bus or matrix.
        on: Send switch.
        level_db: Send level in dB; ``-inf`` when down.
    """

    target: int
    on: bool
    level_db: float

    @property
    def open(self) -> bool:
        """True when the send passes signal (switched on and not at -inf)."""
        return self.on and self.level_db > float("-inf")


@dataclass(frozen=True, slots=True)
class InputChannel:
    """An input channel strip, read for routing checks and the channel audit (never written).

    Attributes:
        number: Channel number, 1..32.
        name: Scribble-strip name.
        source: Physical source of the channel, after the console's input routing.
        on: False when the channel is muted.
        fader_db: Fader level in dB.
        to_main: True when the channel is assigned to Main LR.
        to_mono: True when the channel is assigned to Main Mono.
        sends: Sends to the mix buses.
        hpf_on: High-pass filter switch.
        hpf_hz: High-pass filter frequency.
        eq_on: Channel EQ switch.
        eq: Channel EQ bands.
    """

    number: int
    name: str
    source: InputRef
    on: bool
    fader_db: float
    to_main: bool
    to_mono: bool
    sends: tuple[Send, ...] = ()
    hpf_on: bool = False
    hpf_hz: float = 0.0
    eq_on: bool = False
    eq: tuple[Band, ...] = ()


@dataclass(frozen=True, slots=True)
class MixStrip:
    """A bus, a matrix or a main strip: the paths that can feed speakers.

    Attributes:
        ref: Which strip this is (``bus 3``, ``matrix 1``, ``main_l`` for Main LR, ``mono``).
        name: Scribble-strip name.
        on: False when muted.
        fader_db: Fader level in dB; ``-inf`` when down.
        eq_on: EQ switch.
        eq: EQ bands.
        to_main: True when a bus is assigned to Main LR.
        matrix_sends: Sends of this strip to the matrices (buses and mains only).
        invert: True when the polarity is inverted (matrices only).
    """

    ref: SignalRef
    name: str
    on: bool
    fader_db: float
    eq_on: bool
    eq: tuple[Band, ...]
    to_main: bool = False
    matrix_sends: tuple[Send, ...] = ()
    invert: bool = False


class LinkKind(StrEnum):
    """Kind of strips that can be stereo-linked in pairs."""

    CHANNEL = "channel"
    BUS = "bus"
    MATRIX = "matrix"


class OutputKind(StrEnum):
    """Group of physical outputs."""

    MAIN = "main"
    AUX = "aux"


@dataclass(frozen=True, slots=True)
class OutputDelay:
    """Output delay setting.

    Attributes:
        on: Delay switch.
        delay_ms: Stored delay time in ms.
    """

    on: bool
    delay_ms: float


@dataclass(frozen=True, slots=True)
class PhysicalOutput:
    """A physical output and what feeds it.

    Attributes:
        kind: Output group.
        number: Output number inside the group.
        source: The mix signal that feeds the output.
        source_id: The console's own source number, kept for messages and debugging.
        delay: Output delay, when the output has one.
    """

    kind: OutputKind
    number: int
    source: SignalRef
    source_id: int
    delay: OutputDelay | None = None


@dataclass(frozen=True, slots=True)
class UsbRouting:
    """Routing of the USB audio card.

    Attributes:
        to_computer: For every USB input of the computer (index 0 = USB in 1), its source.
        from_computer: For every console input fed by the computer, the USB output number
            (``{23: 1}``: input 23 carries USB out 1).
        channels: Card channel count in each direction, as reported.
    """

    to_computer: tuple[InputRef, ...]
    from_computer: Mapping[int, int]
    channels: int = 32


@dataclass(frozen=True, slots=True)
class ClockInfo:
    """Sample clock of the console.

    Attributes:
        fs_hz: Sample rate in Hz.
        source: Clock source as reported (``INT``, ``AES50A``, ``CARD``).
    """

    fs_hz: int
    source: str


@dataclass(frozen=True, slots=True)
class ConsoleSnapshot:
    """Everything read from the console in one pass.

    Attributes:
        identity: Model, firmware and address.
        taken_at: ISO time of the read.
        channels: Input channels by number.
        buses: Mix buses by number.
        matrices: Matrices by number.
        main: Main LR strip.
        mono: Main Mono / centre strip; None when the console has none.
        outputs: Physical outputs.
        headamps: Preamps by index.
        input_sources: For every console input 1..32 (index 0 = input 1), its physical source.
        usb: USB card routing.
        clock: Sample clock.
        stereo_links: Linked pairs of strips by kind and first number: ``(LinkKind.MATRIX, 1)``
            is matrices 1/2.
        mono_mode: Main Mono mode as reported (``LR+M``, ``LCR``).
        raw: Original reply lines by node path.
        unparsed: Node paths whose reply was missing or could not be understood.
    """

    identity: ConsoleIdentity
    taken_at: str
    channels: Mapping[int, InputChannel]
    buses: Mapping[int, MixStrip]
    matrices: Mapping[int, MixStrip]
    main: MixStrip
    mono: MixStrip | None
    outputs: tuple[PhysicalOutput, ...]
    headamps: Mapping[int, Headamp]
    input_sources: tuple[InputRef, ...]
    usb: UsbRouting
    clock: ClockInfo | None
    stereo_links: frozenset[tuple[LinkKind, int]] = frozenset()
    mono_mode: str = ""
    raw: Mapping[str, str] = field(default_factory=dict)
    unparsed: tuple[str, ...] = ()

    def strip(self, ref: SignalRef) -> MixStrip | None:
        """The strip behind a signal, or None (direct outs, off, unknown)."""
        if ref.kind is SourceKind.BUS:
            return self.buses.get(ref.number)
        if ref.kind is SourceKind.MATRIX:
            return self.matrices.get(ref.number)
        if ref.kind in (SourceKind.MAIN_L, SourceKind.MAIN_R):
            return self.main
        if ref.kind is SourceKind.MONO:
            return self.mono
        return None

    def outputs_fed_by(self, ref: SignalRef) -> tuple[PhysicalOutput, ...]:
        """Physical outputs whose source is ``ref``."""
        return tuple(o for o in self.outputs if o.source == ref)


@dataclass(frozen=True, slots=True)
class UsbInputInfo:
    """One USB input of the computer (a signal the console sends to the computer).

    Attributes:
        usb: 1-based USB input number.
        source: Physical source of the signal.
        headamp: The preamp of that source, when it has one.
        channels: Console channels that listen to the same source (they must stay muted while a
            measurement microphone is on it).
    """

    usb: int
    source: InputRef
    headamp: Headamp | None
    channels: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class UsbOutputInfo:
    """One USB output of the computer (a signal the computer plays into the console).

    Attributes:
        usb: 1-based USB output number.
        channels: Console channels fed by it.
    """

    usb: int
    channels: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class UsbAudioMap:
    """What travels over the USB card in both directions.

    Attributes:
        inputs: USB inputs of the computer, in order.
        outputs: USB outputs of the computer that feed at least one channel.
    """

    inputs: tuple[UsbInputInfo, ...]
    outputs: tuple[UsbOutputInfo, ...]

    def input_info(self, usb: int) -> UsbInputInfo | None:
        """Information about one USB input, or None."""
        return next((i for i in self.inputs if i.usb == usb), None)
