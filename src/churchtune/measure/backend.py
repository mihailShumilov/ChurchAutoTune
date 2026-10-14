"""The one audio interface the measurement engine needs (the layer rule keeps ``audio`` out).

``audio.base.MultiChannelBackend`` satisfies it; the measurement modules type against this.
"""

from collections.abc import Callable, Sequence
from typing import Protocol

from churchtune.domain.recording import Float32Array, MultiRecording, StopFlag

PlaybackEvent = tuple[int, Callable[[], None]]


class MeasureBackend(Protocol):
    """Play and record several microphone inputs in one stream."""

    def playrec_multi(
        self,
        out: Float32Array,
        inputs: Sequence[int],
        events: Sequence[PlaybackEvent] | None = None,
        stop: StopFlag | None = None,
    ) -> MultiRecording:
        """Play ``out`` and record ``inputs``; events fire between blocks."""
        ...

    def record_multi(
        self, duration_s: float, inputs: Sequence[int], stop: StopFlag | None = None
    ) -> MultiRecording:
        """Record ``inputs`` while playing silence."""
        ...

    def run_stream_multi(
        self,
        next_block: Callable[[int], Float32Array | None],
        inputs: Sequence[int],
        on_input: Callable[[Float32Array], None],
        stop: StopFlag | None = None,
        on_tick: Callable[[], None] | None = None,
    ) -> None:
        """Duplex loop until ``next_block`` returns None."""
        ...
