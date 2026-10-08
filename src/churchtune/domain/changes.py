"""A planned change of one console parameter, and the scope it belongs to."""

from dataclasses import dataclass
from enum import StrEnum

WireValue = float | int


class WriteScope(StrEnum):
    """Write scope: which parameters a plan may touch. Values are stored in undo files."""

    MAIN = "main"  # Main LR EQ only
    SUB = "sub"  # matrix EQ / mute / fader / polarity and output delays


@dataclass(frozen=True, slots=True)
class Change:
    """One parameter write.

    Attributes:
        path: Console parameter address, for example ``/main/st/eq/2/g``.
        old_value: Value before the write, as the console stores it (normalized float or int).
        new_value: Value to write, pre-quantized to the console grid.
        old_display: Human-readable old value.
        new_display: Human-readable new value.
    """

    path: str
    old_value: WireValue
    new_value: WireValue
    old_display: str
    new_display: str
