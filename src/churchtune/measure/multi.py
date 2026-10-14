"""One measurement round with several microphones: each sweep in one stream, every mic deconvolved.

All microphones of a sweep share the stream and its clock, so their impulse responses share one
time base (SPEC §8.1). Each sweep is its own stream: the sweeps are aligned on the impulse-response
peak before averaging, and a shift between them is reported. Each microphone gets the point gates
of ``gates.assess`` plus the repeatability gate of SPEC §8.2: two sweeps must agree within
``max_repeat_db`` in 1/3 octaves from 100 Hz to 8 kHz.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

import numpy as np

from churchtune.analysis.response import magnitude_db, third_octave_centres
from churchtune.domain.arrays import FloatArray
from churchtune.domain.recording import MultiRecording, StopFlag
from churchtune.measure.backend import MeasureBackend
from churchtune.measure.config import QualityGates, SweepConfig
from churchtune.measure.deconvolution import deconvolve, extract_ir
from churchtune.measure.gates import (
    PointQuality,
    QualityWarning,
    Verdict,
    WarningCode,
    assess,
    worse,
)
from churchtune.measure.stimulus import build_stimulus, make_sweep

REPEAT_LOW_HZ, REPEAT_HIGH_HZ = 100.0, 8000.0
REPEAT_SMOOTHING_FRACTION = 3
MIN_SWEEPS_FOR_REPEATABILITY = 2


@dataclass(frozen=True, slots=True)
class MicResult:
    """What one microphone of a round measured.

    Attributes:
        usb_input: 1-based USB input of the microphone.
        ir: Impulse response cut around its peak, ``float64[n_ir]``.
        ir_full: Whole circular impulse response, ``float64[n_fft]``.
        peak_idx: Peak index in ``ir_full`` (latency in samples).
        quality: Point gates, the repeatability gate included.
        repeat_db: Largest 1/3-octave difference between the first two sweeps; None with one sweep.
    """

    usb_input: int
    ir: FloatArray
    ir_full: FloatArray
    peak_idx: int
    quality: PointQuality
    repeat_db: float | None


@dataclass(frozen=True, slots=True)
class RoundMeasurement:
    """A whole round.

    Attributes:
        mics: Results in the order of the requested inputs.
        level_dbfs: Peak level of the sweep that was played.
        n_avg: Sweeps averaged.
        fs_hz: Sample rate.
    """

    mics: tuple[MicResult, ...]
    level_dbfs: float
    n_avg: int
    fs_hz: float

    def result(self, usb_input: int) -> MicResult:
        """Result of one microphone.

        Raises:
            KeyError: That input was not part of the round.
        """
        for mic in self.mics:
            if mic.usb_input == usb_input:
                return mic
        raise KeyError(usb_input)


def _sweep_irs(
    recs: Sequence[FloatArray], ref: FloatArray, fs_hz: float, sweep: SweepConfig
) -> tuple[list[FloatArray], list[int]]:
    """Circular impulse response and peak index of every single sweep."""
    irs = [deconvolve(rec, ref, fs_hz, sweep.f1_hz, sweep.f2_hz) for rec in recs]
    return irs, [int(np.argmax(np.abs(ir))) for ir in irs]


def repeat_difference_db(
    first: FloatArray, second: FloatArray, fs_hz: float, sweep: SweepConfig
) -> float:
    """Largest 1/3-octave level difference (100 Hz-8 kHz) between the responses of two sweeps."""
    centres = third_octave_centres(REPEAT_LOW_HZ, REPEAT_HIGH_HZ)
    curves = []
    for ir_full in (first, second):
        ir, _ = extract_ir(ir_full, fs_hz, sweep.ir_length_s, sweep.ir_pre_ms)
        curves.append(magnitude_db(ir, fs_hz, frac=REPEAT_SMOOTHING_FRACTION, grid_hz=centres))
    return float(np.max(np.abs(curves[0] - curves[1])))


def _aligned_mean(
    recs: Sequence[FloatArray], peaks: Sequence[int], n_fft: int
) -> tuple[FloatArray, int]:
    """Mean of the recordings after shifting each to the first sweep's peak; the largest shift."""
    shifts = []
    for peak in peaks:
        shift = peak - peaks[0]
        if shift > n_fft // 2:  # circular wrap of the impulse response
            shift -= n_fft
        elif shift < -n_fft // 2:
            shift += n_fft
        shifts.append(shift)
    aligned = [np.roll(rec, -shift) for rec, shift in zip(recs, shifts, strict=True)]
    return np.asarray(np.mean(aligned, axis=0), dtype=np.float64), max(abs(s) for s in shifts)


def with_repeatability(
    quality: PointQuality, repeat_db: float, gates: QualityGates, shift_samples: int = 0
) -> PointQuality:
    """Add the repeatability gate to a point's quality.

    A latency that moved between sweeps (each sweep is its own stream) is reported even when the
    levels agree: the sweeps were aligned before averaging, but the system was not steady.
    """
    if repeat_db <= gates.max_repeat_db and shift_samples == 0:
        return quality
    bad = repeat_db > gates.bad_repeat_db
    params = {"repeat_db": round(repeat_db, 1), "bad": bad, "shift_samples": shift_samples}
    warning = QualityWarning(WarningCode.UNSTEADY, params)
    verdict = worse(quality.verdict, Verdict.BAD if bad else Verdict.WARN)
    return replace(quality, verdict=verdict, warnings=(*quality.warnings, warning))


def measure_round(  # noqa: PLR0913 - explicit configuration instead of globals
    backend: MeasureBackend,
    inputs: Sequence[int],
    *,
    fs_hz: float,
    sweep: SweepConfig,
    gates: QualityGates,
    level_dbfs: float | None = None,
    n_avg: int = 2,
    stop: StopFlag | None = None,
    on_sweep: Callable[[int, int], None] | None = None,
) -> RoundMeasurement:
    """Measure every microphone of a round with ``n_avg`` sweeps (one stream per sweep).

    Raises:
        LevelError: The level is above the hard output limit; nothing is played.
        MeasurementAborted: Stopped; the output is already silent.
        ValueError: ``n_avg`` is below 1 or no input is given.
    """
    if n_avg < 1:
        raise ValueError(f"n_avg must be at least 1, got {n_avg}")
    if not inputs:
        raise ValueError("a round needs at least one microphone input")
    level = sweep.level_dbfs if level_dbfs is None else level_dbfs
    stimulus = build_stimulus(make_sweep(fs_hz, sweep, level), fs_hz, sweep.preroll_s, sweep.tail_s)
    ref = stimulus[:, 0].astype(np.float64)
    takes: list[MultiRecording] = []
    for index in range(n_avg):
        if on_sweep is not None:
            on_sweep(index + 1, n_avg)
        takes.append(backend.playrec_multi(stimulus, inputs, stop=stop))
    flags = [flag for take in takes for flag in take.flags]
    results = []
    for column, usb_input in enumerate(inputs):
        recs = [np.asarray(take.channel(column), dtype=np.float64) for take in takes]
        single_irs, peaks = _sweep_irs(recs, ref, fs_hz, sweep)
        mean_rec, shift = _aligned_mean(recs, peaks, len(single_irs[0]))
        ir_full = deconvolve(mean_rec, ref, fs_hz, sweep.f1_hz, sweep.f2_hz)
        ir, peak_idx = extract_ir(ir_full, fs_hz, sweep.ir_length_s, sweep.ir_pre_ms)
        abs_max = np.asarray(np.max(np.abs(recs), axis=0), dtype=np.float64)
        quality = assess(
            mean_rec, abs_max, ir_full, peak_idx, fs_hz=fs_hz, sweep=sweep, flags=flags, gates=gates
        )
        repeat_db = None
        if len(recs) >= MIN_SWEEPS_FOR_REPEATABILITY:
            repeat_db = repeat_difference_db(single_irs[0], single_irs[1], fs_hz, sweep)
            quality = with_repeatability(quality, repeat_db, gates, shift)
        results.append(MicResult(usb_input, ir, ir_full, peak_idx, quality, repeat_db))
    return RoundMeasurement(tuple(results), level, n_avg, fs_hz)
