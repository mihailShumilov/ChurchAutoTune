"""Real audio through one CoreAudio/PortAudio device.

One duplex stream: playback and recording share the same clock (no drift, fixed latency).
Interrupting playback calls ``stream.abort()``: buffers are dropped and the output is silent at
once. The final limiter runs inside the audio callback on every block, and a callback that
fails writes silence and stops the stream.

In dev-safe mode the real ``sounddevice`` module is never loaded (SPEC §9.1): importing it
already makes PortAudio enumerate the devices.
"""

import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from churchtune.audio.base import (
    STOP_CHECK_S,
    Float32Array,
    MultiRecording,
    PlaybackEvent,
    StopFlag,
    check_inputs,
    limit,
)
from churchtune.errors import AudioDeviceError, DevSafeViolation, MeasurementAborted
from churchtune.platform.devsafe import is_dev_safe

OUTPUT_CHANNELS = 2
BLOCK_FRAMES = 1024
# The stop flag is looked at on every poll (ADR 0007: at least every STOP_CHECK_S).
PLAYREC_POLL_S = STOP_CHECK_S
STREAM_POLL_S = STOP_CHECK_S


def load_sounddevice() -> Any:  # noqa: ANN401 - the sounddevice module has no type stubs
    """Import the real ``sounddevice`` module; refused in dev-safe mode before the import."""
    if is_dev_safe():
        raise DevSafeViolation("dev-safe mode refuses real audio devices; use the fake backend")
    try:
        import sounddevice  # noqa: PLC0415 - optional at import time; PortAudio may be missing
    except (OSError, ImportError) as error:
        raise AudioDeviceError(f"sounddevice / PortAudio is not available: {error}") from error
    return sounddevice


def _check_stop(stop: StopFlag | None) -> None:
    """Treat a set stop flag like Ctrl+C: the caller aborts the stream and reports."""
    if stop is not None and stop.is_set():
        raise KeyboardInterrupt


class SoundDeviceBackend:
    """Duplex stream on one device: two outputs, the mic on one input."""

    name = "sounddevice"

    def __init__(
        self,
        device_name: str,
        fs_hz: float,
        mic_input: int,
        *,
        sd: Any = None,  # noqa: ANN401 - sounddevice-like module, injectable for tests
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Find the device.

        Args:
            device_name: Part of the device name, case-insensitive (for example ``X-USB``).
            fs_hz: Sample rate of the stream.
            mic_input: 1-based input channel of the measurement mic.
            sd: The ``sounddevice`` module; loaded lazily when omitted.
            sleep: Poll delay function, injectable for tests.

        Raises:
            AudioDeviceError: PortAudio is missing, the device is not found or has too few
                channels.
        """
        if mic_input < 1:
            raise AudioDeviceError(f"mic input must be 1 or higher, got {mic_input}")
        if not device_name.strip():
            # An empty name would match every device and pick whichever comes first.
            raise AudioDeviceError("the audio device name is empty")
        self._sd = sd if sd is not None else load_sounddevice()
        self._sleep = sleep
        self.fs_hz = fs_hz
        self.mic_input = mic_input
        self.device_name = device_name
        self.index, self.info = self._find_device(device_name)

    def set_point(self, name: str) -> None:
        """Nothing to do in a real room."""

    def _find_device(self, name: str) -> tuple[int, dict[str, Any]]:
        devices = list(self._sd.query_devices())
        wanted = name.strip().lower()
        matches = [i for i, device in enumerate(devices) if wanted in device["name"].lower()]
        exact = [i for i in matches if devices[i]["name"].lower() == wanted]
        available = ", ".join(device["name"] for device in devices)
        if not matches:
            raise AudioDeviceError(
                f"audio device {name!r} not found; available: {available}",
                device=name,
                available=available,
            )
        if len(matches) > 1 and len(exact) != 1:
            names = ", ".join(devices[i]["name"] for i in matches)
            raise AudioDeviceError(
                f"audio device name {name!r} matches several devices ({names}); give the full name",
                device=name,
                available=available,
            )
        index = exact[0] if len(exact) == 1 else matches[0]
        device = devices[index]
        enough = (
            device["max_input_channels"] >= self.mic_input
            and device["max_output_channels"] >= OUTPUT_CHANNELS
        )
        if not enough:
            raise AudioDeviceError(
                f"device {device['name']} has {device['max_input_channels']} in / "
                f"{device['max_output_channels']} out: too few channels",
                device=device["name"],
            )
        return index, dict(device)

    @property
    def input_count(self) -> int:
        """Inputs the device offers."""
        return int(self.info["max_input_channels"])

    def describe(self) -> str:
        """One line about the device."""
        d = self.info
        return (
            f"{d['name']} (#{self.index}): {d['max_input_channels']} in / "
            f"{d['max_output_channels']} out, default {d['default_samplerate']:.0f} Hz"
        )

    def check_samplerate(self) -> float:
        """Check that the device runs at ``fs_hz`` in both directions.

        Returns:
            The device's default sample rate.

        Raises:
            AudioDeviceError: The sample rate is not supported.
        """
        try:
            self._sd.check_input_settings(
                device=self.index, channels=self.mic_input, samplerate=self.fs_hz, dtype="float32"
            )
            self._sd.check_output_settings(
                device=self.index, channels=OUTPUT_CHANNELS, samplerate=self.fs_hz, dtype="float32"
            )
        except Exception as error:
            # PortAudio raises its own error types; all of them mean "cannot run like this".
            raise AudioDeviceError(
                f"{self.fs_hz:.0f} Hz is not supported: {error}", fs_hz=self.fs_hz
            ) from error
        return float(self.info["default_samplerate"])

    def _stream(self, callback: Callable[..., None], in_channels: int) -> Any:  # noqa: ANN401 - sounddevice.Stream
        return self._sd.Stream(
            device=(self.index, self.index),
            samplerate=self.fs_hz,
            channels=(in_channels, OUTPUT_CHANNELS),
            dtype="float32",
            latency="high",
            blocksize=BLOCK_FRAMES,
            callback=callback,
        )

    @staticmethod
    def _silence(stream: Any) -> None:  # noqa: ANN401 - sounddevice.Stream
        """Drop the stream's buffers (immediate silence, no drain) and close it."""
        try:
            stream.abort()
        finally:
            stream.close()

    def _drive(  # noqa: PLR0913 - the stream, its failure box and the three loop hooks
        self,
        stream: Any,  # noqa: ANN401 - sounddevice.Stream
        failure: list[BaseException],
        keep_going: Callable[[], bool],
        each_poll: Callable[[], None],
        poll_s: float,
        *,
        stop: StopFlag | None = None,
    ) -> None:
        """Start the stream and poll until it ends; any way out but a clean end silences it.

        ``stream.start()`` is inside the guarded block: an interrupt that arrives while the
        stream is starting must abort it too. The stop flag is looked at on every poll.

        Raises:
            MeasurementAborted: Interrupted with Ctrl+C or by the stop flag.
            AudioDeviceError: The audio callback failed.
        """
        try:
            _check_stop(stop)  # a stop raised before the start: abort without playing a block
            stream.start()
            while keep_going() and stream.active:
                _check_stop(stop)
                self._sleep(poll_s)
                each_poll()
        except BaseException as error:
            self._silence(stream)
            if isinstance(error, KeyboardInterrupt):
                raise MeasurementAborted("interrupted; the output is silent") from None
            raise
        if failure:
            self._silence(stream)
            raise AudioDeviceError(f"the audio callback failed: {failure[0]}") from failure[0]
        stream.close()

    def playrec(
        self, out: Float32Array, events: Sequence[PlaybackEvent] | None = None
    ) -> tuple[Float32Array, list[str]]:
        """Play ``out`` on outputs 1+2 and record the mic input (one-microphone form)."""
        recording = self.playrec_multi(out, (self.mic_input,), events)
        return recording.channel(0), list(recording.flags)

    def playrec_multi(
        self,
        out: Float32Array,
        inputs: Sequence[int],
        events: Sequence[PlaybackEvent] | None = None,
        stop: StopFlag | None = None,
    ) -> MultiRecording:
        """Play ``out`` on outputs 1+2 and record the 1-based ``inputs`` in the same stream.

        Events run in the calling thread (never in the audio callback) as soon as playback has
        passed their sample index; events that did not fire before the stream ended still run.
        After an interrupt or a failure no further event runs: restoring console state is the
        caller's job.

        Raises:
            MeasurementAborted: Interrupted (Ctrl+C or ``stop``); the stream was aborted first.
            AudioDeviceError: ``out`` is not ``[n, 2]``, an input is outside the device, or the
                audio callback failed.
        """
        chosen = check_inputs(inputs, self.input_count)
        pending = sorted(events or [], key=lambda event: event[0])
        buffer = np.ascontiguousarray(out, dtype=np.float32)
        if buffer.ndim != 2 or buffer.shape[1] != OUTPUT_CHANNELS:  # noqa: PLR2004
            raise AudioDeviceError(f"output buffer must be [n, 2], got shape {buffer.shape}")
        n = len(buffer)
        rec = np.zeros((n, len(chosen)), dtype=np.float32)
        columns = [i - 1 for i in chosen]
        position = [0]
        flags: list[str] = []
        failure: list[BaseException] = []
        sd = self._sd

        def callback(indata: Any, outdata: Any, frames: int, _time: Any, status: Any) -> None:  # noqa: ANN401
            outdata.fill(0)  # whatever happens below, the block never holds garbage
            try:
                if status:
                    flags.append(str(status))
                start = position[0]
                count = max(0, min(frames, n - start))
                outdata[:count] = limit(buffer[start : start + count])  # final safety limiter
                rec[start : start + count] = indata[:count, columns]
                position[0] = start + count
            except Exception as error:
                failure.append(error)
                outdata.fill(0)
                raise sd.CallbackAbort from error
            if position[0] >= n:
                raise sd.CallbackStop

        def run_due_events() -> None:
            while pending and position[0] >= pending[0][0]:
                _check_stop(stop)  # a stop between two events wins over the next mute switch
                pending.pop(0)[1]()

        stream = self._stream(callback, max(chosen))
        self._drive(stream, failure, lambda: True, run_due_events, PLAYREC_POLL_S, stop=stop)
        for _, action in pending:  # the stream ended before an event fired: still run it
            action()
        return MultiRecording(rec, tuple(flags))

    def record(self, duration_s: float) -> Float32Array:
        """Record the mic while playing silence."""
        silent = np.zeros((int(duration_s * self.fs_hz), OUTPUT_CHANNELS), dtype=np.float32)
        rec, _ = self.playrec(silent)
        return rec

    def record_multi(
        self, duration_s: float, inputs: Sequence[int], stop: StopFlag | None = None
    ) -> MultiRecording:
        """Record ``inputs`` while playing silence."""
        silent = np.zeros((int(duration_s * self.fs_hz), OUTPUT_CHANNELS), dtype=np.float32)
        return self.playrec_multi(silent, inputs, stop=stop)

    def run_stream(
        self,
        next_block: Callable[[int], Float32Array | None],
        on_input: Callable[[Float32Array], None],
        on_tick: Callable[[], None] | None = None,
    ) -> None:
        """Duplex loop on the mic input (one-microphone form)."""
        self.run_stream_multi(
            next_block, (self.mic_input,), lambda block: on_input(block[:, 0].copy()), None, on_tick
        )

    def run_stream_multi(
        self,
        next_block: Callable[[int], Float32Array | None],
        inputs: Sequence[int],
        on_input: Callable[[Float32Array], None],
        stop: StopFlag | None = None,
        on_tick: Callable[[], None] | None = None,
    ) -> None:
        """Duplex loop: play what ``next_block`` returns until it returns None.

        Raises:
            MeasurementAborted: Interrupted (Ctrl+C or ``stop``); the stream was aborted first.
            AudioDeviceError: A block has the wrong shape, an input is outside the device, or a
                callback function failed; the stream was aborted first.
        """
        chosen = check_inputs(inputs, self.input_count)
        columns = [i - 1 for i in chosen]
        sd = self._sd
        done = threading.Event()
        failure: list[BaseException] = []

        def callback(indata: Any, outdata: Any, frames: int, _time: Any, _status: Any) -> None:  # noqa: ANN401
            outdata.fill(0)  # whatever happens below, the block never holds garbage
            try:
                block = next_block(frames)
                if block is not None:
                    if np.shape(block) != (frames, OUTPUT_CHANNELS):
                        raise AudioDeviceError(  # noqa: TRY301 - handled like any callback failure
                            f"output block must be [{frames}, 2], got shape {np.shape(block)}"
                        )
                    outdata[:] = limit(block)  # final safety limiter
                    on_input(indata[:, columns].copy())
            except Exception as error:
                failure.append(error)
                outdata.fill(0)
                raise sd.CallbackAbort from error
            if block is None:
                done.set()
                raise sd.CallbackStop

        self._drive(
            self._stream(callback, max(chosen)),
            failure,
            lambda: not done.is_set(),
            on_tick or (lambda: None),
            STREAM_POLL_S,
            stop=stop,
        )
