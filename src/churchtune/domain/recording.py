"""Multichannel recordings and the stop request: what audio backends and measurements share."""

from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

Float32Array = npt.NDArray[np.float32]


class StopFlag(Protocol):
    """Set by the UI (Ctrl+C or Space in the TUI) to silence the output at once."""

    def is_set(self) -> bool:
        """True once a stop was requested."""
        ...


@dataclass(frozen=True, slots=True)
class MultiRecording:
    """What a multichannel play/record returned.

    Attributes:
        samples: ``float32[n, k]``, one column per requested input, on the time base of the output.
        flags: Stream status flags seen (empty when clean).
    """

    samples: Float32Array
    flags: tuple[str, ...]

    def channel(self, index: int) -> Float32Array:
        """One recorded input by its position in the request, ``float32[n]``."""
        return np.ascontiguousarray(self.samples[:, index])
