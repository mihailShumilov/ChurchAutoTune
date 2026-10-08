"""The audio backend contract and the output limiter every backend applies.

Nothing louder than ``HARD_LIMIT_DBFS`` peak ever leaves the computer (SPEC §9.4): each backend
passes every output block through :func:`limit` as its last step.
"""

from collections.abc import Callable, Sequence
from typing import Protocol

import numpy as np
import numpy.typing as npt

from churchtune.domain.levels import HARD_LIMIT_DBFS, db_to_lin

Float32Array = npt.NDArray[np.float32]
# (sample index, action): the action runs in the calling thread once playback passed the index.
PlaybackEvent = tuple[int, Callable[[], None]]

LIMIT = db_to_lin(HARD_LIMIT_DBFS)


def limit(block: npt.ArrayLike) -> Float32Array:
    """Clip an output block to the hard limit, as ``float32``.

    NaN becomes silence, never full scale; infinities are clipped like any other overshoot.
    """
    with np.errstate(over="ignore"):  # values beyond float32 become inf and are clipped below
        samples = np.nan_to_num(np.asarray(block, dtype=np.float32), nan=0.0)
    return np.clip(samples, -LIMIT, LIMIT).astype(np.float32)


class AudioBackend(Protocol):
    """One duplex audio device: playback and capture share a stream, hence a clock."""

    name: str
    fs_hz: float

    def describe(self) -> str:
        """One line about the device, for the operator."""
        ...

    def check_samplerate(self) -> float:
        """Check that the device runs at ``fs_hz``; return its default sample rate.

        Raises:
            AudioDeviceError: The sample rate is not supported.
        """
        ...

    def set_point(self, name: str) -> None:
        """Tell the backend which listening point is measured (only a simulator cares)."""
        ...

    def playrec(
        self, out: Float32Array, events: Sequence[PlaybackEvent] | None = None
    ) -> tuple[Float32Array, list[str]]:
        """Play ``out`` (``float32[n, 2]``) and record the mic in the same stream.

        Args:
            out: Output buffer.
            events: Actions to run once playback has passed a sample index (used to switch
                mutes between sweep blocks). An event that did not fire before a clean end of
                the buffer still runs at the end. After ``MeasurementAborted`` or any other
                error no further event runs: restoring console state (mutes) is the caller's
                job, in ``finally`` and through the pending-restore file.

        Returns:
            ``(recording, flags)``: ``float32[n]`` on the time base of ``out`` and the stream
            status flags seen (empty when clean).

        Raises:
            MeasurementAborted: Playback was interrupted; the output is already silent.
        """
        ...

    def record(self, duration_s: float) -> Float32Array:
        """Record the mic for ``duration_s`` seconds while playing silence."""
        ...

    def run_stream(
        self,
        next_block: Callable[[int], Float32Array | None],
        on_input: Callable[[Float32Array], None],
        on_tick: Callable[[], None] | None = None,
    ) -> None:
        """Run a duplex loop until ``next_block`` returns None.

        Args:
            next_block: Called with a frame count; returns ``float32[frames, 2]`` to play, or
                None to end (after its own fade-out).
            on_input: Called with each recorded mono block.
            on_tick: Called regularly in the calling thread.

        Raises:
            MeasurementAborted: The loop was interrupted; the output is already silent.
        """
        ...
