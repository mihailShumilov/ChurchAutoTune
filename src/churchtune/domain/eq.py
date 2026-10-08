"""Six-band system EQ as pure data: band types, one band, one EQ block.

The models are console-neutral in shape; the type names are those of the X32/M32 bus, matrix and
main EQ, the only console family supported so far.
"""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

BANDS_PER_EQ = 6


class EqType(StrEnum):
    """EQ band type. Values are the strings the console prints in ``/node`` replies."""

    LCUT = "LCut"
    LSHV = "LShv"
    PEQ = "PEQ"
    VEQ = "VEQ"
    HSHV = "HShv"
    HCUT = "HCut"
    BU6 = "BU6"
    BU12 = "BU12"
    BS12 = "BS12"
    LR12 = "LR12"
    BU18 = "BU18"
    BU24 = "BU24"
    BS24 = "BS24"
    LR24 = "LR24"


# Types the tool may tune; everything else is a cut filter and is never touched automatically.
ADJUSTABLE_TYPES: frozenset[EqType] = frozenset({EqType.PEQ, EqType.VEQ, EqType.LSHV, EqType.HSHV})
CUT_FILTER_TYPES: frozenset[EqType] = frozenset(EqType) - ADJUSTABLE_TYPES


@dataclass(frozen=True, slots=True)
class Band:
    """One EQ band.

    Attributes:
        band: Band number, 1..6.
        type: Band type.
        freq_hz: Centre or corner frequency in Hz.
        gain_db: Gain in dB (ignored by cut filters).
        q: Quality factor (ignored by cut filters).
    """

    band: int
    type: EqType
    freq_hz: float
    gain_db: float
    q: float

    def __post_init__(self) -> None:
        if not 1 <= self.band <= BANDS_PER_EQ:
            raise ValueError(f"band number must be 1..{BANDS_PER_EQ}, got {self.band}")

    @property
    def adjustable(self) -> bool:
        """True when the band type is one the tool may tune (not a cut filter)."""
        return self.type in ADJUSTABLE_TYPES


@dataclass(frozen=True, slots=True)
class SystemEq:
    """A six-band EQ block of one path (main, matrix or bus).

    Attributes:
        on: Whether the EQ block is switched on.
        bands: Exactly six bands, numbered 1..6 in order.
    """

    on: bool
    bands: tuple[Band, ...]

    def __post_init__(self) -> None:
        if len(self.bands) != BANDS_PER_EQ:
            raise ValueError(f"an EQ block has {BANDS_PER_EQ} bands, got {len(self.bands)}")
        if [b.band for b in self.bands] != list(range(1, BANDS_PER_EQ + 1)):
            raise ValueError("bands must be in order 1..6")

    def band(self, number: int) -> Band:
        """Return the band with the given number (1..6)."""
        return self.bands[number - 1]

    def with_band(self, band: Band) -> "SystemEq":
        """Return a copy in which the band with ``band.band`` is replaced by ``band``."""
        return replace(self, bands=tuple(band if b.band == band.band else b for b in self.bands))

    def with_on(self, on: bool) -> "SystemEq":
        """Return a copy with the EQ block switched on or off."""
        return replace(self, on=on)


class EqQuantizer(Protocol):
    """The value grid of a console's EQ: snaps real values to what the console can store.

    ``design`` proposes only values a console can hold; it receives the grid through this
    protocol instead of importing a driver.
    """

    def snap_freq_hz(self, freq_hz: float) -> float:
        """Return the nearest frequency the console can store, in Hz."""
        ...

    def snap_gain_db(self, gain_db: float) -> float:
        """Return the nearest gain the console can store, in dB."""
        ...

    def snap_q(self, q: float) -> float:
        """Return the nearest Q the console can store."""
        ...
