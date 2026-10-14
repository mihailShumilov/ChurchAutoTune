"""A simulated hall with several measurement microphones, for multi-mic development and tests.

Output 1 drives the left top, output 2 the right top, their average the sub. Every microphone sits
at a named point; each point has its own impulse response per speaker, built deterministically
from the point name, so the same name always gives the same hall. Microphones differ like real
units: sensitivity, timing, polarity, a response shape and noise. A scratch on a microphone (the
auto-mapping gesture) adds a short burst to that input.

Playback runs block by block like a real stream: events fire at block boundaries and the stop flag
is checked between blocks, so the silence after a stop is at most one block late. Everything that
"left the computer" is kept in ``played`` after the same limiter as the real backend.
"""

import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy import signal

from churchtune.audio.base import (
    Float32Array,
    MultiRecording,
    PlaybackEvent,
    StopFlag,
    check_inputs,
    limit,
)
from churchtune.audio.fake.room import DIRECT_DELAY_S, synthetic_room
from churchtune.domain.arrays import FloatArray
from churchtune.domain.levels import db_to_lin
from churchtune.errors import AudioDeviceError, MeasurementAborted

SPEAKERS = ("top_l", "top_r", "sub")
BLOCK_FRAMES = 1024
OUTPUT_CHANNELS = 2
DEFAULT_INPUTS = 32
ROOM_LENGTH_S = 2.5
CROSSOVER_HZ = 1800.0
TWEETER_OFFSET_MS = 0.12
TWEETER_LEVEL = 1.1
DECORRELATION_HZ = 1700.0
EARLY_PART_S = 0.004
CAPSULE_STEP_MS = 0.07
MAX_CAPSULE_MS = 0.3
UNUSED_INPUT_NOISE_DBFS = -100.0
SPEED_OF_SOUND_M_S = 343.0
TOP_HIGHPASS_HZ = 70.0
SUB_LOWPASS_HZ = 160.0
SCRATCH_S = 0.25
SHAPE_TAPS = 255
CALIBRATION_POINT = "cal"
SpeakerGains = Callable[[], Mapping[str, float]]


@dataclass(frozen=True, slots=True)
class FakeMic:
    """One simulated measurement microphone.

    Attributes:
        sensitivity_db: Level offset against a nominal microphone.
        delay_ms: Timing offset (capsule position, cable, converter).
        inverted: True for a microphone wired with inverted polarity.
        shape: Response deviation as ``(freq_hz, gain_db)`` points (a damaged capsule).
        noise_dbfs: RMS of the self-noise.
    """

    sensitivity_db: float = 0.0
    delay_ms: float = 0.0
    inverted: bool = False
    shape: tuple[tuple[float, float], ...] = ()
    noise_dbfs: float = -85.0


@dataclass(slots=True)
class _Scratch:
    usb_input: int
    at_s: float
    level_dbfs: float


@dataclass(slots=True)
class FakeHall:
    """The simulated hall and its microphones on a 32-in / 32-out device.

    Attributes:
        fs_hz: Sample rate.
        mics: Microphone per 1-based USB input.
        gain_db: Gain from output to a nominal microphone at a nominal point.
        latency_samples: Fixed system latency.
        speaker_gains: Returns the linear gain of each speaker path now (console mutes); all on
            when omitted.
        input_count: Inputs of the simulated device.
        seed: Seed of the noise.
    """

    fs_hz: float
    mics: Mapping[int, FakeMic]
    gain_db: float = -10.0
    latency_samples: int = 1800
    speaker_gains: SpeakerGains | None = None
    input_count: int = DEFAULT_INPUTS
    seed: int = 11
    name: str = "simulator-hall"
    played: list[Float32Array] = field(default_factory=list)
    positions: dict[int, str] = field(default_factory=dict)
    stopped_at_frame: int | None = None
    # Frame of the buffer being played now (block start); lets tests note when a key was pressed.
    current_frame: int = 0
    # Development rig: the order in which a simulated person scratches the microphones.
    auto_scratch: list[int] = field(default_factory=list)
    # Per microphone: a capsule offset at the calibration point (0 = elsewhere).
    variants: dict[int, int] = field(default_factory=dict)
    _scratches: list[_Scratch] = field(default_factory=list, init=False, repr=False)
    _irs: dict[tuple[str, int], dict[str, FloatArray]] = field(
        default_factory=dict, init=False, repr=False
    )
    _shapes: dict[int, FloatArray] = field(default_factory=dict, init=False, repr=False)
    _rng: np.random.Generator = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = np.random.default_rng(self.seed)

    # ------------------------------------------------------------------ setup
    def describe(self) -> str:
        """One line about the simulator."""
        return f"SIMULATOR: hall with {len(self.mics)} microphones"

    def place(self, usb_input: int, point: str) -> None:
        """Put the microphone on ``usb_input`` at ``point``."""
        self.positions[usb_input] = point
        self.variants[usb_input] = 0

    def place_all(self, point: str) -> None:
        """Put every microphone side by side at one point (the mic-set calibration position).

        Capsules are a few centimetres apart: they share the direct sound and the early
        reflections, arrive up to 0.3 ms apart, and their late reverberation above ~1.7 kHz is
        independent, like real capsules within 10 cm.
        """
        for index, usb_input in enumerate(self.mics, start=1):
            self.positions[usb_input] = point
            self.variants[usb_input] = index

    def scratch_next(self) -> None:
        """Scratch the next microphone of ``auto_scratch`` in the next recording (dev rig)."""
        if self.auto_scratch:
            self.scratch(self.auto_scratch.pop(0))

    def scratch(self, usb_input: int, at_s: float = 0.5, level_dbfs: float = -20.0) -> None:
        """Make the next recording contain a scratch on ``usb_input`` at ``at_s`` seconds."""
        self._scratches.append(_Scratch(usb_input, at_s, level_dbfs))

    def point_irs(self, point: str, variant: int = 0) -> dict[str, FloatArray]:
        """Impulse response of each speaker at a point (deterministic from the name).

        ``variant`` > 0 is a capsule next to the point: the same direct sound and early part,
        an independent late tail above ``DECORRELATION_HZ`` and a small extra delay.
        """
        key = (point, variant)
        if key not in self._irs:
            base = self._irs.setdefault((point, 0), self._build_point(point))
            self._irs[key] = base if variant == 0 else self._capsule(point, base, variant)
        return self._irs[key]

    def _speaker_filter(self, room: FloatArray, speaker: str) -> FloatArray:
        """A two-way top (woofer + offset tweeter, LR4 at 1.8 kHz, HP 70 Hz) or a sub (LP 160)."""
        fs = self.fs_hz
        if speaker == "sub":
            sos = signal.butter(4, SUB_LOWPASS_HZ, "lowpass", fs=fs, output="sos")
            return np.asarray(signal.sosfilt(sos, room), dtype=np.float64)
        high = signal.butter(2, TOP_HIGHPASS_HZ, "highpass", fs=fs, output="sos")
        lr_low = signal.butter(2, CROSSOVER_HZ, "lowpass", fs=fs, output="sos")
        lr_high = signal.butter(2, CROSSOVER_HZ, "highpass", fs=fs, output="sos")
        x = signal.sosfilt(high, room)
        woofer = signal.sosfilt(lr_low, signal.sosfilt(lr_low, x))
        tweeter = signal.sosfilt(lr_high, signal.sosfilt(lr_high, x))
        tweeter = fractional_delay(tweeter, TWEETER_OFFSET_MS * 1e-3 * fs) * TWEETER_LEVEL
        return np.asarray(woofer + tweeter, dtype=np.float64)

    def _build_point(self, point: str) -> dict[str, FloatArray]:
        code = zlib.crc32(point.encode("utf-8"))
        rng = np.random.default_rng(code)
        distance_m = 4.0 + (code % 1000) / 100.0
        delay_s = DIRECT_DELAY_S + distance_m / SPEED_OF_SOUND_M_S
        irs: dict[str, FloatArray] = {}
        for index, speaker in enumerate(SPEAKERS):
            offset_s = 0.0015 * index  # left, right and sub arrive a little apart
            room = synthetic_room(
                rng, self.fs_hz, length_s=ROOM_LENGTH_S, direct_delay_s=delay_s + offset_s
            )
            irs[speaker] = self._speaker_filter(room, speaker)
        return irs

    def _capsule(
        self, point: str, base: dict[str, FloatArray], variant: int
    ) -> dict[str, FloatArray]:
        fs = self.fs_hz
        rng = np.random.default_rng(zlib.crc32(f"{point}/{variant}".encode()))
        split = signal.butter(4, DECORRELATION_HZ, "highpass", fs=fs, output="sos")
        offset = (variant * CAPSULE_STEP_MS % MAX_CAPSULE_MS) * 1e-3 * fs
        irs: dict[str, FloatArray] = {}
        for speaker, ir in base.items():
            onset = int(np.argmax(np.abs(ir)))
            early_end = onset + int(EARLY_PART_S * fs)
            own = synthetic_room(rng, fs, length_s=ROOM_LENGTH_S)
            own = self._speaker_filter(own, speaker)
            late_shared, late_own = ir.copy(), own.copy()
            late_shared[:early_end] = 0.0
            late_own[:early_end] = 0.0
            scale = np.sqrt(np.sum(late_shared**2) / max(np.sum(late_own**2), 1e-30))
            high_shared = signal.sosfiltfilt(split, late_shared)
            high_own = signal.sosfiltfilt(split, late_own) * scale
            capsule = ir - high_shared + high_own  # same lows and early part, own high late tail
            irs[speaker] = fractional_delay(capsule, offset)
        return irs

    def _shape(self, usb_input: int) -> FloatArray | None:
        mic = self.mics[usb_input]
        if not mic.shape:
            return None
        if usb_input not in self._shapes:
            nyquist = self.fs_hz / 2
            freqs = [0.0, *(f for f, _ in mic.shape), nyquist]
            gains = [mic.shape[0][1], *(g for _, g in mic.shape), mic.shape[-1][1]]
            amplitude = [db_to_lin(g) for g in gains]
            self._shapes[usb_input] = signal.firwin2(SHAPE_TAPS, freqs, amplitude, fs=self.fs_hz)
        return self._shapes[usb_input]

    # ------------------------------------------------------------------ the acoustic chain
    def _gains_now(self) -> dict[str, float]:
        gains = dict.fromkeys(SPEAKERS, 1.0)
        if self.speaker_gains is not None:
            gains.update(self.speaker_gains())
        return gains

    def _speaker_feeds(
        self, out: FloatArray, gains: Mapping[str, FloatArray | float] | None = None
    ) -> dict[str, FloatArray]:
        """Signal of each speaker; ``gains`` may vary per sample (mutes switched mid-buffer)."""
        level = gains if gains is not None else self._gains_now()
        left, right = out[:, 0], out[:, 1]
        return {
            "top_l": left * level["top_l"],
            "top_r": right * level["top_r"],
            "sub": (left + right) / 2 * level["sub"],
        }

    def _noise(self, usb_input: int, n: int) -> FloatArray:
        """Self-noise of the input; a more sensitive capsule hears the room noise louder too."""
        mic = self.mics.get(usb_input)
        level = mic.noise_dbfs + mic.sensitivity_db if mic else UNUSED_INPUT_NOISE_DBFS
        return self._rng.normal(scale=db_to_lin(level), size=n)

    def _hear_full(self, usb_input: int, feeds: Mapping[str, FloatArray]) -> FloatArray | None:
        """Full-length response of a microphone to the feeds, delay included; None when silent."""
        mic = self.mics.get(usb_input)
        if mic is None or usb_input not in self.positions:
            return None
        irs = self.point_irs(self.positions[usb_input], self.variants.get(usb_input, 0))
        n = len(next(iter(feeds.values())))
        y = np.zeros(n + max(len(ir) for ir in irs.values()) - 1)
        for speaker, feed in feeds.items():
            if np.any(feed):
                part = signal.fftconvolve(feed, irs[speaker])
                y[: len(part)] += part
        shape = self._shape(usb_input)
        if shape is not None:
            y = signal.lfilter(shape, [1.0], y)
            y = np.concatenate([y[SHAPE_TAPS // 2 :], np.zeros(SHAPE_TAPS // 2)])
        y = fractional_delay(y, mic.delay_ms * 1e-3 * self.fs_hz)
        y = np.concatenate([np.zeros(self.latency_samples), y])
        return y * db_to_lin(self.gain_db + mic.sensitivity_db) * (-1.0 if mic.inverted else 1.0)

    def _hear(self, usb_input: int, feeds: Mapping[str, FloatArray], n: int) -> FloatArray:
        heard = self._hear_full(usb_input, feeds)
        noise = self._noise(usb_input, n)
        return noise if heard is None else heard[:n] + noise

    def _add_scratches(self, rec: FloatArray, inputs: Sequence[int]) -> None:
        for scratch in self._scratches:
            if scratch.usb_input not in inputs:
                continue
            column = inputs.index(scratch.usb_input)
            start = int(scratch.at_s * self.fs_hz)
            count = min(int(SCRATCH_S * self.fs_hz), len(rec) - start)
            if count > 0:
                burst = self._rng.normal(scale=db_to_lin(scratch.level_dbfs), size=count)
                rec[start : start + count, column] += burst
        self._scratches.clear()

    def _record(
        self,
        out: FloatArray,
        inputs: Sequence[int],
        gains: Mapping[str, FloatArray | float] | None = None,
    ) -> FloatArray:
        n = len(out)
        feeds = self._speaker_feeds(out, gains)
        rec = np.stack([self._hear(i, feeds, n) for i in inputs], axis=1)
        self._add_scratches(rec, inputs)
        return np.clip(rec, -1.0, 1.0)

    # ------------------------------------------------------------------ streaming
    def _play_blocks(
        self,
        out: Float32Array,
        events: Sequence[PlaybackEvent] | None,
        stop: StopFlag | None,
    ) -> tuple[FloatArray, dict[str, FloatArray]]:
        """Push ``out`` block by block.

        Returns:
            What was really played (zeros after a stop) and the gain of each speaker per sample,
            as the console's mutes stood while each block played.
        """
        buffer = limit(np.asarray(out, dtype=np.float32))
        if buffer.ndim != 2 or buffer.shape[1] != OUTPUT_CHANNELS:  # noqa: PLR2004
            raise AudioDeviceError(f"output buffer must be [n, 2], got shape {buffer.shape}")
        played = np.zeros_like(buffer)
        gains = {speaker: np.ones(len(buffer)) for speaker in SPEAKERS}
        pending = sorted(events or [], key=lambda event: event[0])
        self.stopped_at_frame = None
        for start in range(0, len(buffer), BLOCK_FRAMES):
            self.current_frame = start
            while pending and pending[0][0] <= start:
                pending.pop(0)[1]()
            if stop is not None and stop.is_set():
                self.stopped_at_frame = start
                self.played.append(played)
                raise MeasurementAborted("stopped; the output is silent")
            end = start + BLOCK_FRAMES
            played[start:end] = buffer[start:end]
            for speaker, value in self._gains_now().items():
                gains[speaker][start:end] = value
        for _, action in pending:
            action()
        self.played.append(played)
        return np.asarray(played, dtype=np.float64), gains

    def playrec_multi(
        self,
        out: Float32Array,
        inputs: Sequence[int],
        events: Sequence[PlaybackEvent] | None = None,
        stop: StopFlag | None = None,
    ) -> MultiRecording:
        """Play through the hall and record ``inputs`` (1-based USB inputs)."""
        chosen = check_inputs(inputs, self.input_count)
        played, gains = self._play_blocks(out, events, stop)
        rec = self._record(played, chosen, gains)
        return MultiRecording(rec.astype(np.float32), ())

    def record_multi(
        self, duration_s: float, inputs: Sequence[int], stop: StopFlag | None = None
    ) -> MultiRecording:
        """Record ``inputs`` while playing silence."""
        silent = np.zeros((int(duration_s * self.fs_hz), OUTPUT_CHANNELS), dtype=np.float32)
        return self.playrec_multi(silent, inputs, stop=stop)

    def run_stream_multi(
        self,
        next_block: Callable[[int], Float32Array | None],
        inputs: Sequence[int],
        on_input: Callable[[Float32Array], None],
        stop: StopFlag | None = None,
        on_tick: Callable[[], None] | None = None,
    ) -> None:
        """Block-wise loop; room tails carry over from block to block (overlap-add)."""
        chosen = check_inputs(inputs, self.input_count)
        tails: dict[int, FloatArray] = {}
        while True:
            if stop is not None and stop.is_set():
                raise MeasurementAborted("stopped; the output is silent")
            block = next_block(BLOCK_FRAMES)
            if block is None:
                return
            limited = limit(np.asarray(block, dtype=np.float32))
            self.played.append(limited)
            feeds = self._speaker_feeds(np.asarray(limited, dtype=np.float64))
            n = len(limited)
            columns = []
            for usb_input in chosen:
                full = self._hear_full(usb_input, feeds)
                tail = tails.get(usb_input, np.zeros(0))
                if full is None:
                    full = np.zeros(n)
                if len(tail) > len(full):
                    full = np.concatenate([full, np.zeros(len(tail) - len(full))])
                full[: len(tail)] += tail
                tails[usb_input] = full[n:]
                columns.append(full[:n] + self._noise(usb_input, n))
            rec = np.clip(np.stack(columns, axis=1), -1.0, 1.0)
            on_input(rec.astype(np.float32))
            if on_tick is not None:
                on_tick()


def demo_rig(fs_hz: float) -> FakeHall:
    """The development rig of ``--audio fake``: four microphones on USB inputs 10-13.

    Their sensitivities differ like real ECM8000 units; they stand at points p1-p4 until a
    calibration puts them together. A mapping finds them in the order 12, 10, 13, 11.
    """
    mics = {10: FakeMic(0.0), 11: FakeMic(1.2), 12: FakeMic(-0.8), 13: FakeMic(0.5, delay_ms=0.05)}
    hall = FakeHall(fs_hz, mics, gain_db=-6.0, auto_scratch=[12, 10, 13, 11])
    for number, usb_input in enumerate(mics, start=1):
        hall.place(usb_input, f"p{number}")
    return hall


def fractional_delay(x: FloatArray, delay_samples: float) -> FloatArray:
    """Delay ``x`` by a fractional number of samples (frequency-domain phase shift, zero-padded)."""
    if delay_samples == 0:
        return np.asarray(x, dtype=np.float64)
    whole = int(np.floor(delay_samples))
    n = len(x) + whole + 64
    spectrum = np.fft.rfft(x, n)
    freqs = np.fft.rfftfreq(n)
    shifted = np.fft.irfft(spectrum * np.exp(-2j * np.pi * freqs * delay_samples), n)
    return np.asarray(shifted[: len(x)], dtype=np.float64)
