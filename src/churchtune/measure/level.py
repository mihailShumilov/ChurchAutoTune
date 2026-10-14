"""Setting the measurement level from the microphones, never from the output alone (SPEC S8, §9.4).

−6 dBFS at the computer does not limit the level in the hall: console gain and faders follow. So
loudness is controlled by what the microphones hear.

* ``LevelRamp`` raises pink noise from −50 dBFS peak in 2 dB steps every 0.5 s until the reference
  microphone reads the target RMS. It stops at the first of: target reached; the output reached the
  hard limit; a microphone clipped; the reference jumped more than 6 dB above the target; the output
  rose 12 dB while the microphone rose less than 6 dB (wrong microphone, unplugged, no sound from
  the speakers); the operator pressed stop.
* ``SweepLevelSearch`` plays short sweeps from −30 dBFS in 3 dB steps until the recorded peak is in
  −12…−6 dBFS, without passing the hard output limit.

Both are pure state machines; ``run_level_ramp`` and ``find_sweep_level`` drive them on a backend.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol

import numpy as np

from churchtune.domain.levels import HARD_LIMIT_DBFS, db_to_lin, peak_dbfs, rms_dbfs
from churchtune.domain.recording import Float32Array, MultiRecording, StopFlag
from churchtune.errors import MeasurementAborted
from churchtune.measure.config import SweepConfig
from churchtune.measure.stimulus import build_stimulus, make_sweep, pink_noise

PINK_LOOP_S = 2.0
FADE_S = 0.05
CLIP_PEAK_DBFS = -0.5
STEP_FRAMES_MIN = 1


class RampStop(StrEnum):
    """Why the level ramp ended."""

    TARGET = "target"
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
        target_low_dbfs: Lowest wanted RMS at the reference microphone.
        target_high_dbfs: Highest wanted RMS at the reference microphone.
        jump_db: A reading this far above ``target_high_dbfs`` stops at once.
        stall_output_db: Output rise after which the microphone must have followed ...
        stall_mic_db: ... by at least this much.
    """

    start_dbfs: float = -50.0
    step_db: float = 2.0
    step_s: float = 0.5
    target_low_dbfs: float = -20.0
    target_high_dbfs: float = -12.0
    jump_db: float = 6.0
    stall_output_db: float = 12.0
    stall_mic_db: float = 6.0


@dataclass(frozen=True, slots=True)
class RampReading:
    """What the microphones heard during one step.

    Attributes:
        mic_rms_dbfs: RMS at the reference microphone.
        peak_dbfs: Highest peak over every recorded microphone.
    """

    mic_rms_dbfs: float
    peak_dbfs: float


@dataclass(frozen=True, slots=True)
class LevelRamp:
    """State of the pink-noise ramp.

    Attributes:
        config: Thresholds.
        output_dbfs: Output peak level of the current step.
        first_mic_dbfs: Reference RMS at the first step (None before it).
        first_output_dbfs: Output level at the first step.
        stopped: Why the ramp ended, or None while it runs.
    """

    config: RampConfig = field(default_factory=RampConfig)
    output_dbfs: float = -50.0
    first_mic_dbfs: float | None = None
    first_output_dbfs: float = -50.0
    stopped: RampStop | None = None

    @classmethod
    def start(cls, config: RampConfig | None = None) -> "LevelRamp":
        """A ramp at its first step."""
        cfg = config or RampConfig()
        return cls(cfg, cfg.start_dbfs, None, cfg.start_dbfs)

    def step(self, reading: RampReading, *, user_stop: bool = False) -> "LevelRamp":
        """Take one reading and decide: stop (``stopped`` set) or the next output level."""
        cfg = self.config
        first = self.first_mic_dbfs if self.first_mic_dbfs is not None else reading.mic_rms_dbfs
        state = replace(self, first_mic_dbfs=first)
        mic_rise = reading.mic_rms_dbfs - first
        output_rise = self.output_dbfs - self.first_output_dbfs
        checks = (
            (user_stop, RampStop.USER),
            (reading.peak_dbfs >= CLIP_PEAK_DBFS, RampStop.CLIP),
            (reading.mic_rms_dbfs > cfg.target_high_dbfs + cfg.jump_db, RampStop.JUMP),
            (reading.mic_rms_dbfs >= cfg.target_low_dbfs, RampStop.TARGET),
            (
                output_rise >= cfg.stall_output_db and mic_rise < cfg.stall_mic_db,
                RampStop.NO_RESPONSE,
            ),
            (self.output_dbfs + cfg.step_db > HARD_LIMIT_DBFS, RampStop.OUTPUT_LIMIT),
        )
        for condition, reason in checks:
            if condition:
                return replace(state, stopped=reason)
        return replace(state, output_dbfs=self.output_dbfs + cfg.step_db)


class SweepStop(StrEnum):
    """Why the sweep-level search ended."""

    FOUND = "found"
    OUTPUT_LIMIT = "output_limit"
    TOO_LOUD = "too_loud"


@dataclass(frozen=True, slots=True)
class SweepLevelSearch:
    """State of the sweep-level search (SPEC S8).

    Attributes:
        level_dbfs: Sweep peak level to try next.
        step_db: Rise per try.
        peak_low_dbfs: Lowest wanted recorded peak.
        peak_high_dbfs: Highest wanted recorded peak.
        stopped: Why the search ended, or None while it runs.
    """

    level_dbfs: float = -30.0
    step_db: float = 3.0
    peak_low_dbfs: float = -12.0
    peak_high_dbfs: float = -6.0
    stopped: SweepStop | None = None

    def step(self, recorded_peak_dbfs: float) -> "SweepLevelSearch":
        """Take the recorded peak of the last try and decide."""
        if recorded_peak_dbfs > self.peak_high_dbfs:
            # Too loud already: go back one step if possible, else stop.
            lower = self.level_dbfs - self.step_db
            return replace(self, level_dbfs=lower, stopped=SweepStop.TOO_LOUD)
        if recorded_peak_dbfs >= self.peak_low_dbfs:
            return replace(self, stopped=SweepStop.FOUND)
        if self.level_dbfs + self.step_db > HARD_LIMIT_DBFS:
            return replace(self, stopped=SweepStop.OUTPUT_LIMIT)
        return replace(self, level_dbfs=self.level_dbfs + self.step_db)


# ------------------------------------------------------------------ driving a backend
class StreamBackend(Protocol):
    """The part of a multichannel backend the level steps need."""

    def run_stream_multi(
        self,
        next_block: Callable[[int], Float32Array | None],
        inputs: Sequence[int],
        on_input: Callable[[Float32Array], None],
        stop: StopFlag | None = None,
    ) -> None:
        """Duplex loop until ``next_block`` returns None."""
        ...

    def playrec_multi(
        self,
        out: Float32Array,
        inputs: Sequence[int],
        events: None = None,
        stop: StopFlag | None = None,
    ) -> MultiRecording:
        """Play and record in one stream."""
        ...


@dataclass(slots=True)
class _NoiseFeed:
    """Pink noise at the ramp's level, faded in, faded out once the ramp stops."""

    loop: np.ndarray
    fs_hz: float
    level_dbfs: float
    position: int = 0
    fade_out: int | None = None

    def block(self, frames: int) -> Float32Array | None:
        fade = int(FADE_S * self.fs_hz)
        if self.fade_out is not None and self.fade_out <= 0:
            return None
        index = (self.position + np.arange(frames)) % len(self.loop)
        mono = self.loop[index] * db_to_lin(self.level_dbfs)
        ramp_in = np.clip((self.position + np.arange(frames)) / max(fade, 1), 0.0, 1.0)
        mono = mono * ramp_in
        if self.fade_out is not None:
            remaining = self.fade_out - np.arange(frames)
            mono = mono * np.clip(remaining / max(fade, 1), 0.0, 1.0)
            self.fade_out -= frames
        self.position += frames
        return np.stack([mono, mono], axis=1).astype(np.float32)


def run_level_ramp(  # noqa: PLR0913 - every argument is a knob of the step
    backend: StreamBackend,
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
        stop: The operator's stop (Space / Ctrl+C); it ends the ramp quietly.
        on_step: Called after each step, for the live meter.

    Raises:
        ValueError: ``reference`` is not among ``inputs``.
    """
    if reference not in inputs:
        raise ValueError(f"the reference microphone {reference} is not among {list(inputs)}")
    ramp = LevelRamp.start(config)
    loop = pink_noise(PINK_LOOP_S, fs_hz, np.random.default_rng(7))
    # Unit peak: the ramp's level is the peak level of the noise, so it can never pass the limit.
    loop = loop / float(np.max(np.abs(loop)))
    feed = _NoiseFeed(loop, fs_hz, ramp.output_dbfs)
    step_frames = max(STEP_FRAMES_MIN, int(ramp.config.step_s * fs_hz))
    column = list(inputs).index(reference)
    collected: list[Float32Array] = []
    last = RampReading(-200.0, -200.0)

    def on_input(block: Float32Array) -> None:
        nonlocal ramp, last
        collected.append(block)
        if sum(len(b) for b in collected) < step_frames or ramp.stopped is not None:
            return
        data = np.concatenate(collected)
        collected.clear()
        last = RampReading(rms_dbfs(data[:, column]), peak_dbfs(data))
        user = stop is not None and stop.is_set()
        ramp = ramp.step(last, user_stop=user)
        feed.level_dbfs = min(ramp.output_dbfs, HARD_LIMIT_DBFS)
        if on_step is not None:
            on_step(ramp, last)
        if ramp.stopped is not None:
            feed.fade_out = int(FADE_S * fs_hz)

    backend.run_stream_multi(feed.block, inputs, on_input)
    return ramp, last


def find_sweep_level(
    backend: StreamBackend,
    inputs: Sequence[int],
    fs_hz: float,
    *,
    search: SweepLevelSearch | None = None,
    stop: StopFlag | None = None,
) -> SweepLevelSearch:
    """Play 1 s trial sweeps until the loudest microphone peaks in the wanted window.

    Raises:
        MeasurementAborted: Stopped; the output is already silent.
    """
    state = search or SweepLevelSearch()
    trial = SweepConfig(duration_s=1.0, preroll_s=0.2, tail_s=0.5)
    while state.stopped is None:
        if stop is not None and stop.is_set():
            raise MeasurementAborted("stopped during the sweep-level search")
        stimulus = build_stimulus(
            make_sweep(fs_hz, trial, state.level_dbfs), fs_hz, trial.preroll_s, trial.tail_s
        )
        recording = backend.playrec_multi(stimulus, inputs, stop=stop)
        state = state.step(peak_dbfs(recording.samples))
    return state
