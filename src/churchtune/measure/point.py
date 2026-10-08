"""One measurement point: sweep -> record -> deconvolve -> impulse response and quality.

Pure of files: storing the result is the job of ``storage``.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

from churchtune.domain.arrays import FloatArray
from churchtune.measure.config import QualityGates, SweepConfig
from churchtune.measure.deconvolution import deconvolve, extract_ir
from churchtune.measure.gates import PointQuality, assess
from churchtune.measure.stimulus import build_stimulus, make_sweep


class PlayRecBackend(Protocol):
    """The part of an audio backend a point measurement needs."""

    def playrec(self, out: npt.NDArray[np.float32]) -> tuple[FloatArray, Sequence[str]]:
        """Play ``out`` (``float32[n, 2]``) and record the mic in the same stream.

        Returns:
            ``(recording, flags)``: ``float[n]`` on the time base of ``out`` and the stream
            status flags seen (empty when clean).
        """
        ...


@dataclass(frozen=True, slots=True)
class PointMeasurement:
    """Result of measuring one point.

    Attributes:
        recording: The first sweep as recorded, ``float64[n]`` (kept for inspection).
        ir: Impulse response cut around its peak, ``float64[n_ir]``, from the mean of all sweeps.
        ir_full: The whole circular impulse response, ``float64[n_fft]``.
        peak_idx: Index of the peak in ``ir_full`` (latency in samples).
        quality: Quality numbers and verdict.
        level_dbfs: Peak level of the sweep that was played.
        n_avg: Number of sweeps averaged.
        fs_hz: Sample rate.
    """

    recording: FloatArray
    ir: FloatArray
    ir_full: FloatArray
    peak_idx: int
    quality: PointQuality
    level_dbfs: float
    n_avg: int
    fs_hz: float


def measure_point(  # noqa: PLR0913 - explicit configuration instead of globals
    backend: PlayRecBackend,
    *,
    fs_hz: float,
    sweep: SweepConfig,
    gates: QualityGates,
    level_dbfs: float | None = None,
    n_avg: int = 1,
    on_sweep: Callable[[int, int], None] | None = None,
) -> PointMeasurement:
    """Measure one point with ``n_avg`` sweeps.

    Args:
        backend: Plays the stimulus and records the mic in one stream.
        fs_hz: Sample rate of the stream.
        sweep: Sweep parameters.
        gates: Quality thresholds.
        level_dbfs: Sweep peak level; ``sweep.level_dbfs`` when omitted.
        n_avg: Number of sweeps to average (at least 1).
        on_sweep: Called with ``(index, n_avg)`` before each sweep, for progress display.

    Returns:
        The measurement.

    Raises:
        LevelError: The level is above the hard output limit; nothing is played.
        ValueError: ``n_avg`` is below 1.
    """
    if n_avg < 1:
        raise ValueError(f"n_avg must be at least 1, got {n_avg}")
    level = sweep.level_dbfs if level_dbfs is None else level_dbfs
    stimulus = build_stimulus(make_sweep(fs_hz, sweep, level), fs_hz, sweep.preroll_s, sweep.tail_s)
    ref = stimulus[:, 0].astype(np.float64)
    recordings: list[FloatArray] = []
    flags: list[str] = []
    for index in range(n_avg):
        if on_sweep is not None:
            on_sweep(index + 1, n_avg)
        rec, rec_flags = backend.playrec(stimulus)
        recordings.append(np.asarray(rec, dtype=np.float64))
        flags.extend(rec_flags)
    mean_rec = np.asarray(np.mean(recordings, axis=0), dtype=np.float64)
    ir_full = deconvolve(mean_rec, ref, fs_hz, sweep.f1_hz, sweep.f2_hz)
    ir, peak_idx = extract_ir(ir_full, fs_hz, sweep.ir_length_s, sweep.ir_pre_ms)
    abs_max = np.asarray(np.max(np.abs(recordings), axis=0), dtype=np.float64)
    quality = assess(
        mean_rec, abs_max, ir_full, peak_idx, fs_hz=fs_hz, sweep=sweep, flags=flags, gates=gates
    )
    return PointMeasurement(
        recording=recordings[0],
        ir=ir,
        ir_full=ir_full,
        peak_idx=peak_idx,
        quality=quality,
        level_dbfs=level,
        n_avg=n_avg,
        fs_hz=fs_hz,
    )
