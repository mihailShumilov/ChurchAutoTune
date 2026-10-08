"""What the write policy needs to know about a console's value encoding.

The policy judges real values (dB, ms, band types) but writes carry the console's own encoding
(normalized floats, type indices). A driver supplies the translation through this protocol, so
``safety`` never imports ``mixers``.
"""

from collections.abc import Mapping, Sequence
from typing import Protocol

from churchtune.domain.changes import WireValue
from churchtune.domain.eq import Band, EqType


class ValueLaws(Protocol):
    """Translation between console-encoded values and real units."""

    @property
    def eq_types(self) -> Sequence[EqType]:
        """Band types in wire order: the index is the integer written to ``.../type``."""
        ...

    def gain_db(self, x_norm: float) -> float:
        """EQ gain in dB of a normalized value."""
        ...

    def fader_db(self, x_norm: float) -> float:
        """Fader level in dB of a normalized value."""
        ...

    def delay_ms(self, x_norm: float) -> float:
        """Output delay in ms of a normalized value."""
        ...

    def step_index(self, kind: str, x_norm: float) -> int:
        """Grid step of a normalized value; ``kind`` is the last path segment (f, g, q, ...)."""
        ...

    def band_wire_values(self, band: Band) -> tuple[Mapping[str, WireValue], Mapping[str, str]]:
        """Quantized wire values ``{type, f, g, q}`` of a band and their display strings."""
        ...

    def fader_wire(self, level_db: float) -> float:
        """Quantized normalized value of a fader level in dB."""
        ...

    def fader_display(self, level_db: float) -> str:
        """A fader level as the console shows it."""
        ...

    def delay_wire(self, delay_ms: float) -> float:
        """Quantized normalized value of an output delay in ms (clamped to the console range)."""
        ...

    @property
    def min_delay_ms(self) -> float:
        """Shortest output delay the console can store."""
        ...
