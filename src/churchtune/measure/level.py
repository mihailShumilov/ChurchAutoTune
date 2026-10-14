"""Setting the measurement level from the microphones, never from the output alone (SPEC S8, §9.4).

−6 dBFS at the computer does not limit the level in the hall: console gain and faders follow. So
loudness is controlled by what the microphones hear.

* ``LevelRamp`` raises pink noise from −50 dBFS peak in 2 dB steps every 0.5 s until the reference
  microphone reads the target RMS. It stops at the first of: target reached; slightly above it;
  the output reached the hard limit; a microphone clipped; the reference jumped more than 6 dB
  above the target; the output rose 12 dB over the last steps while the microphone rose less than
  6 dB (wrong microphone, unplugged, no sound from the speakers); the operator pressed stop.
* ``SweepLevelSearch`` plays short sweeps from −30 dBFS in 3 dB steps until the reference peak is in
  −12…−6 dBFS; it steps down and checks again when too loud, stops when the microphone does not
  follow, and never passes the hard limit or the level the ramp found.

Both are pure state machines. ``run_level_ramp`` drives the ramp on a stream: the audio thread only
copies blocks and watches for clipping or a jump; decisions run in the calling thread (``on_tick``).
The operator's stop fades the noise out within ``STOP_FADE_S`` and the backend aborts on it too.
"""

import math
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

import numpy as np

from churchtune.domain.arrays import FloatArray
from churchtune.domain.levels import HARD_LIMIT_DBFS, SILENCE_DBFS, db_to_lin, peak_dbfs, rms_dbfs
from churchtune.domain.recording import Float32Array, StopFlag
from churchtune.errors import MeasurementAborted
from churchtune.measure.backend import MeasureBackend
from churchtune.measure.config import QualityGates, SweepConfig
from churchtune.measure.stimulus import build_stimulus, make_sweep, pink_noise

PINK_LOOP_S = 2.0
NOISE_SEED = 7
FADE_IN_S = 0.05
STOP_FADE_S = 0.01
CLIP_PEAK_DBFS = QualityGates().clip_dbfs


class RampStop(StrEnum):
    """Why the level ramp ended."""

    # The stream ended by itself before any decision (a backend that stopped early).
    ENDED = "ended"
    TARGET = "target"
    ABOVE_TARGET = "above_target"
    OUTPUT_LIMIT = "output_limit"
    CLIP = "clip"
    JUMP = "jump"
    NO_RESPONSE = "no_response"
    USER = "user"


@dataclass(frozen=True, slots=True)
class RampConfig:
    """Level ramp thresholds (SPEC S8).

    Attributes:
        start_dbfs: First output level (peak of the noise, dBFS).
        step_db: Rise per step.
        step_s: Time per step.
        settle_s: Start of each step left out of the reading (latency and reverberation build-up).
        target_low_dbfs: Lowest wanted RMS at the reference microphone.
        target_high_dbfs: Highest wanted RMS at the reference microphone.
        jump_db: A reading this far above ``target_high_dbfs`` stops at once.
        stall_output_db: Output rise over recent steps after which the microphone must have
            followed ...
        stall_mic_db: ... by at least this much.

    Raises:
        ValueError: A level above the hard limit, a step that is not positive, a non-finite value.
    """

    start_dbfs: float = -50.0
    step_db: float = 2.0
    step_s: float = 0.5
    settle_s: float = 0.15
    target_low_dbfs: float = -20.0
    target_high_dbfs: float = -12.0
    jump_db: float = 6.0
    stall_output_db: float = 12.0
    stall_mic_db: float = 6.0

    def __post_init__(self) -> None:
        values = (
            self.start_dbfs, self.step_db, self.step_s, self.settle_s, self.target_low_dbfs,
            self.target_high_dbfs, self.jump_db, self.stall_output_db, self.stall_mic_db,
        )  # fmt: skip
        if not all(math.isfinite(v) for v in values) or self.settle_s < 0:
            raise ValueError("ramp settings must be finite numbers and the settle time positive")
        if self.start_dbfs > HARD_LIMIT_DBFS:
            raise ValueError(f"the ramp cannot start above {HARD_LIMIT_DBFS} dBFS")
        if self.step_db <= 0 or self.step_s <= self.settle_s:
            raise ValueError("the ramp needs a positive step longer than the settle time")


@dataclass(frozen=True, slots=True)
class RampReading:
    """What the microphones heard during one step.

    Attributes:
        mic_rms_dbfs: RMS at the reference microphone.
        peak_dbfs: Highest peak over every recorded microphone.
        levels_dbfs: RMS of every microphone, for the live meters.
    """

    mic_rms_dbfs: float
    peak_dbfs: float
    levels_dbfs: tuple[float, ...] = ()


SILENT_READING = RampReading(SILENCE_DBFS, SILENCE_DBFS)


@dataclass(frozen=True, slots=True)
class LevelRamp:
    """State of the pink-noise ramp.

    Attributes:
        config: Thresholds.
        output_dbfs: Output peak level of the current step.
        history: ``(output_dbfs, mic_rms_dbfs)`` of the finished steps.
        stopped: Why the ramp ended, or None while it runs.
    """

    config: RampConfig
    output_dbfs: float
    history: tuple[tuple[float, float], ...] = ()
    stopped: RampStop | None = None

    @classmethod
    def start(cls, config: RampConfig | None = None) -> "LevelRamp":
        """A ramp at its first step."""
        cfg = config or RampConfig()
        return cls(cfg, cfg.start_dbfs)

    def _stalled(self, reading: RampReading) -> bool:
        """The output rose ``stall_output_db`` since an earlier step; the mic did not follow."""
        cfg = self.config
        for output, mic in reversed(self.history):
            if self.output_dbfs - output >= cfg.stall_output_db:
                return reading.mic_rms_dbfs - mic < cfg.stall_mic_db
        return False

    def step(self, reading: RampReading, *, user_stop: bool = False) -> "LevelRamp":
        """Take one reading and decide: stop (``stopped`` set) or the next output level."""
        cfg = self.config
        checks = (
            (user_stop, RampStop.USER),
            (reading.peak_dbfs >= CLIP_PEAK_DBFS, RampStop.CLIP),
            (reading.mic_rms_dbfs > cfg.target_high_dbfs + cfg.jump_db, RampStop.JUMP),
            (reading.mic_rms_dbfs > cfg.target_high_dbfs, RampStop.ABOVE_TARGET),
            (reading.mic_rms_dbfs >= cfg.target_low_dbfs, RampStop.TARGET),
            (self._stalled(reading), RampStop.NO_RESPONSE),
            (self.output_dbfs + cfg.step_db > HARD_LIMIT_DBFS, RampStop.OUTPUT_LIMIT),
        )
        history = (*self.history, (self.output_dbfs, reading.mic_rms_dbfs))
        for condition, reason in checks:
            if condition:
                return replace(self, history=history, stopped=reason)
        return replace(self, output_dbfs=self.output_dbfs + cfg.step_db, history=history)


class SweepStop(StrEnum):
    """Why the sweep-level search ended."""

    FOUND = "found"
    OUTPUT_LIMIT = "output_limit"
    TOO_LOUD_AT_MIN = "too_loud_at_min"
    NO_RESPONSE = "no_response"


@dataclass(frozen=True, slots=True)
class SweepLevelSearch:
    """State of the sweep-level search (SPEC S8).

    Attributes:
        level_dbfs: Sweep peak level to try next.
        step_db: Change per try.
        peak_low_dbfs: Lowest wanted recorded peak at the reference microphone.
        peak_high_dbfs: Highest wanted recorded peak.
        max_level_dbfs: Never try above this (the hard limit, or the level the ramp found).
        min_level_dbfs: Never go below this when too loud: then the console gain is too high.
        stall_level_db: Level rise after which the microphone must have followed ...
        stall_peak_db: ... by at least this much.
        first: ``(level, peak)`` of the first try.
        went_down: True once a try was too loud (no rise after that).
        stopped: Why the search ended, or None while it runs.
    """

    level_dbfs: float = -30.0
    step_db: float = 3.0
    peak_low_dbfs: float = -12.0
    peak_high_dbfs: float = -6.0
    max_level_dbfs: float = HARD_LIMIT_DBFS
    min_level_dbfs: float = -50.0
    stall_level_db: float = 12.0
    stall_peak_db: float = 6.0
    first: tuple[float, float] | None = None
    went_down: bool = False
    stopped: SweepStop | None = None

    def step(self, recorded_peak_dbfs: float) -> "SweepLevelSearch":
        """Take the reference peak of the last try and decide."""
        first = self.first or (self.level_dbfs, recorded_peak_dbfs)
        state = replace(self, first=first)
        if recorded_peak_dbfs > self.peak_high_dbfs:
            lower = self.level_dbfs - self.step_db
            if lower < self.min_level_dbfs:
                return replace(state, stopped=SweepStop.TOO_LOUD_AT_MIN)
            return replace(state, level_dbfs=lower, went_down=True)
        if recorded_peak_dbfs >= self.peak_low_dbfs or self.went_down:
            return replace(state, stopped=SweepStop.FOUND)
        rose = self.level_dbfs - first[0]
        if rose >= self.stall_level_db and recorded_peak_dbfs - first[1] < self.stall_peak_db:
            return replace(state, stopped=SweepStop.NO_RESPONSE)
        if self.level_dbfs + self.step_db > min(self.max_level_dbfs, HARD_LIMIT_DBFS):
            return replace(state, stopped=SweepStop.OUTPUT_LIMIT)
        return replace(state, level_dbfs=self.level_dbfs + self.step_db)


# ------------------------------------------------------------------ driving a backend
@dataclass(slots=True)
class _NoiseFeed:
    """Pink noise at the ramp's level, faded in; faded out fast on a stop, an alarm or the end.

    Only the audio thread changes the fade counter; other threads ask for it with ``stopping``.
    """

    loop: FloatArray
    fs_hz: float
    level_dbfs: float
    stop: StopFlag | None
    position: int = 0
    fade_out_frames: int | None = None
    alarm: RampStop | None = None
    stopping: threading.Event = field(default_factory=threading.Event)

    def block(self, frames: int) -> Float32Array | None:
        asked = self.stopping.is_set() or self.alarm is not None
        if self.fade_out_frames is None and (
            asked or (self.stop is not None and self.stop.is_set())
        ):
            self.fade_out_frames = int(STOP_FADE_S * self.fs_hz)
        if self.fade_out_frames is not None and self.fade_out_frames <= 0:
            return None
        index = (self.position + np.arange(frames)) % len(self.loop)
        ramp_in = np.clip((self.position + np.arange(frames)) / (FADE_IN_S * self.fs_hz), 0, 1)
        mono = self.loop[index] * db_to_lin(min(self.level_dbfs, HARD_LIMIT_DBFS)) * ramp_in
        if self.fade_out_frames is not None:
            remaining = self.fade_out_frames - np.arange(frames)
            mono = mono * np.clip(remaining / max(STOP_FADE_S * self.fs_hz, 1), 0.0, 1.0)
            self.fade_out_frames -= frames
        self.position += frames
        return np.stack([mono, mono], axis=1).astype(np.float32)


@dataclass(slots=True)
class _RampDriver:
    """Collects blocks (audio thread) and steps the ramp (calling thread)."""

    ramp: LevelRamp
    feed: _NoiseFeed
    reference: int
    fs_hz: float
    on_step: Callable[[LevelRamp, RampReading], None] | None
    blocks: list[Float32Array] = field(default_factory=list)
    frames: int = 0
    last: RampReading = SILENT_READING
    lock: threading.Lock = field(default_factory=threading.Lock)

    def on_input(self, block: Float32Array) -> None:
        """Audio thread: keep the block; raise the alarm on clipping or a jump right away."""
        cfg = self.ramp.config
        loud = float(np.sqrt(np.mean(np.square(block[:, self.reference], dtype=np.float64))))
        if self.feed.alarm is None:
            if float(np.max(np.abs(block))) >= db_to_lin(CLIP_PEAK_DBFS):
                self.feed.alarm = RampStop.CLIP
            elif loud > db_to_lin(cfg.target_high_dbfs + cfg.jump_db):
                self.feed.alarm = RampStop.JUMP
        with self.lock:
            self.blocks.append(block)
            self.frames += len(block)

    def _take(self) -> FloatArray:
        with self.lock:  # only swap the list under the lock; the audio thread never waits long
            blocks, self.blocks = self.blocks, []
            self.frames = 0
        data = np.concatenate(blocks) if blocks else np.zeros((1, 1))
        return np.asarray(data, dtype=np.float64)

    def on_tick(self) -> None:
        """Calling thread: once a step is long enough (or a stop or alarm came), decide."""
        cfg = self.ramp.config
        if self.ramp.stopped is not None:
            return
        user = self.feed.stop is not None and self.feed.stop.is_set()
        alarm = self.feed.alarm
        ready = self.frames >= int(cfg.step_s * self.fs_hz)
        if not (ready or user or alarm):
            return
        data = self._take()
        if alarm is not None and not user:
            # Clipping or a jump: stop for that reason, never average it into a "target".
            levels = tuple(rms_dbfs(data[:, c]) for c in range(data.shape[1]))
            reference = levels[self.reference] if self.reference < len(levels) else SILENCE_DBFS
            self.last = RampReading(reference, peak_dbfs(data), levels)
            history = (*self.ramp.history, (self.ramp.output_dbfs, reference))
            self.ramp = replace(self.ramp, history=history, stopped=alarm)
            self.feed.stopping.set()
            if self.on_step is not None:
                self.on_step(self.ramp, self.last)
            return
        settle = int(cfg.settle_s * self.fs_hz)
        steady = data[settle:] if len(data) > settle else data
        levels = tuple(rms_dbfs(steady[:, c]) for c in range(steady.shape[1]))
        reference = levels[self.reference] if self.reference < len(levels) else SILENCE_DBFS
        self.last = RampReading(reference, peak_dbfs(data), levels)
        self.ramp = self.ramp.step(self.last, user_stop=user)
        if self.on_step is not None:
            self.on_step(self.ramp, self.last)
        if self.ramp.stopped is None:
            self.feed.level_dbfs = self.ramp.output_dbfs
        else:
            self.feed.stopping.set()


def run_level_ramp(  # noqa: PLR0913 - every argument is a knob of the step
    backend: MeasureBackend,
    inputs: Sequence[int],
    reference: int,
    fs_hz: float,
    *,
    config: RampConfig | None = None,
    stop: StopFlag | None = None,
    on_step: Callable[[LevelRamp, RampReading], None] | None = None,
) -> tuple[LevelRamp, RampReading]:
    """Play the pink-noise ramp and return the final state and reading.

    Args:
        backend: Multichannel backend.
        inputs: Every measurement microphone (all are watched for clipping).
        reference: The microphone at point 1, which sets the level.
        fs_hz: Sample rate.
        config: Ramp thresholds.
        stop: The operator's stop (Space / Ctrl+C): silence within ``STOP_FADE_S`` plus one block.
        on_step: Called after each step in the calling thread, for the live meters.

    Raises:
        ValueError: ``reference`` is not among ``inputs``.
    """
    if reference not in inputs:
        raise ValueError(f"the reference microphone {reference} is not among {list(inputs)}")
    ramp = LevelRamp.start(config)
    loop = pink_noise(PINK_LOOP_S, fs_hz, np.random.default_rng(NOISE_SEED))
    # Unit peak: the ramp's level is the peak level of the noise, so it can never pass the limit.
    loop = loop / float(np.max(np.abs(loop)))
    feed = _NoiseFeed(loop, fs_hz, ramp.output_dbfs, stop)
    driver = _RampDriver(ramp, feed, list(inputs).index(reference), fs_hz, on_step)
    try:
        backend.run_stream_multi(feed.block, inputs, driver.on_input, stop, driver.on_tick)
    except MeasurementAborted:
        if stop is None or not stop.is_set():
            raise
    driver.on_tick()  # decide on what is left (a stop or an alarm in the last blocks)
    final = driver.ramp
    if final.stopped is None:
        user = stop is not None and stop.is_set()
        final = replace(final, stopped=RampStop.USER if user else feed.alarm or RampStop.ENDED)
    return final, driver.last


@dataclass(frozen=True, slots=True)
class TrialSweep:
    """The short sweep of the level search.

    Attributes:
        duration_s: Sweep length.
        preroll_s: Silence before.
        tail_s: Silence after.
    """

    duration_s: float = 1.0
    preroll_s: float = 0.2
    tail_s: float = 0.5


def find_sweep_level(  # noqa: PLR0913 - explicit configuration instead of globals
    backend: MeasureBackend,
    inputs: Sequence[int],
    fs_hz: float,
    *,
    reference: int | None = None,
    search: SweepLevelSearch | None = None,
    trial: TrialSweep | None = None,
    stop: StopFlag | None = None,
) -> SweepLevelSearch:
    """Play trial sweeps until the reference microphone peaks in the wanted window.

    Clipping on any microphone counts as too loud.

    Raises:
        MeasurementAborted: Stopped; the output is already silent.
        ValueError: ``reference`` is not among ``inputs``, or a level is not a finite number.
    """
    ref = inputs[0] if reference is None else reference
    if ref not in inputs:
        raise ValueError(f"the reference microphone {ref} is not among {list(inputs)}")
    column = list(inputs).index(ref)
    state = search or SweepLevelSearch()
    if not (math.isfinite(state.level_dbfs) and math.isfinite(state.max_level_dbfs)):
        raise ValueError("sweep levels must be finite numbers")
    # The first try never passes the ceiling (the level the ramp found) or the hard limit.
    state = replace(state, level_dbfs=min(state.level_dbfs, state.max_level_dbfs, HARD_LIMIT_DBFS))
    shape = trial or TrialSweep()
    config = SweepConfig(
        duration_s=shape.duration_s, preroll_s=shape.preroll_s, tail_s=shape.tail_s
    )
    while state.stopped is None:
        if stop is not None and stop.is_set():
            raise MeasurementAborted("stopped during the sweep-level search")
        stimulus = build_stimulus(
            make_sweep(fs_hz, config, state.level_dbfs), fs_hz, config.preroll_s, config.tail_s
        )
        recording = backend.playrec_multi(stimulus, inputs, stop=stop)
        peak = peak_dbfs(recording.channel(column))
        if peak_dbfs(recording.samples) >= CLIP_PEAK_DBFS:
            peak = max(peak, CLIP_PEAK_DBFS)
        state = state.step(peak)
    return state
