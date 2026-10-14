"""Relative calibration of a set of measurement microphones (SPEC S9, skill room-measurement).

All microphones stand together (capsules within 10 cm, same height and direction) and record the
same sweeps in one stream. Each one is compared with the set:

* sensitivity: mean level difference to the set median in 200 Hz-2 kHz (long window);
* shape: the remaining difference in 1/3 octaves, from a short window above 500 Hz (the direct
  sound and a few milliseconds after it) so the uncorrelated late reverberation of capsules a few
  centimetres apart does not enter it; it is faded to zero outside 100 Hz-8 kHz, where it is not
  checked;
* delay and polarity: band-limited cross-correlation of the direct sound with the set's median
  impulse response, with sub-sample interpolation;
* noise: room noise against the set median, corrected by the sensitivity.

A microphone is rejected when its shape deviates more than ±3 dB, its delay differs more than
0.5 ms, its polarity is inverted against a clear majority (every microphone when there is no
majority), or it is much noisier. With two
microphones the pair is compared and both are flagged ("one of the two differs"). The calibration
is relative; a manufacturer's file, when given, applies first (SPEC S9).
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from scipy import signal

from churchtune.analysis.response import magnitude_db, third_octave_centres
from churchtune.domain.arrays import FloatArray
from churchtune.measure.gates import Verdict
from churchtune.measure.multi import MicResult, RoundMeasurement

MIN_MICS = 2
PAIR = 2
SET_ID_CHARS = 12


@dataclass(frozen=True, slots=True)
class CalibrationConfig:
    """Thresholds and windows of the set calibration (SPEC S9).

    Attributes:
        centres_low_hz: Lowest 1/3-octave centre of the curves.
        centres_high_hz: Highest 1/3-octave centre.
        sensitivity_band_hz: Band of the sensitivity average.
        shape_band_hz: Band where the shape is checked and applied.
        short_window_from_hz: Above this the shape uses the short window.
        short_window_s: Short window from the start of the cut impulse response (which begins
            ``SweepConfig.ir_pre_ms`` before its peak), so it holds the direct sound and the first
            few milliseconds after it.
        max_shape_db: Shape deviation that rejects a microphone.
        max_delay_ms: Delay difference that rejects a microphone.
        max_extra_noise_db: Noise above the set (after sensitivity) that rejects a microphone.
        polarity_band_hz: Band of the direct-sound correlation.
        direct_pre_s: Correlation window before the direct sound.
        direct_post_s: Correlation window after it.
        max_lag_ms: Largest lag searched (beyond the rejection limit, so a late mic is measured).
    """

    centres_low_hz: float = 31.5
    centres_high_hz: float = 16000.0
    sensitivity_band_hz: tuple[float, float] = (200.0, 2000.0)
    shape_band_hz: tuple[float, float] = (100.0, 8000.0)
    short_window_from_hz: float = 500.0
    short_window_s: float = 0.010
    max_shape_db: float = 3.0
    max_delay_ms: float = 0.5
    max_extra_noise_db: float = 10.0
    polarity_band_hz: tuple[float, float] = (200.0, 4000.0)
    direct_pre_s: float = 0.001
    direct_post_s: float = 0.005
    max_lag_ms: float = 2.5


class MicProblem(StrEnum):
    """Why a microphone of the set is rejected."""

    SHAPE = "shape"
    DELAY = "delay"
    POLARITY = "polarity"
    NOISE = "noise"


class CalibrationError(ValueError):
    """The calibration round is not good enough to calibrate from."""


@dataclass(frozen=True, slots=True)
class MicCalibration:
    """One microphone against its set.

    Attributes:
        usb_input: 1-based USB input of the microphone.
        sensitivity_db: Level above the set (subtract it from this mic's curves).
        delay_ms: Arrival later than the set.
        inverted: True when the direct sound has the opposite sign of a clear majority, or when
            there is no majority (then every microphone of the set is flagged).
        shape_db: Response deviation after the sensitivity, per band of ``centres_hz``.
        max_shape_db: Largest ``|shape_db|`` in the shape band.
        noise_db: Room noise above the set, after the sensitivity.
        problems: Reasons to reject; empty for a good microphone.
    """

    usb_input: int
    sensitivity_db: float
    delay_ms: float
    inverted: bool
    shape_db: tuple[float, ...]
    max_shape_db: float
    noise_db: float
    problems: tuple[MicProblem, ...]

    @property
    def ok(self) -> bool:
        """True when the microphone can be used."""
        return not self.problems


@dataclass(frozen=True, slots=True)
class MicSetCalibration:
    """The calibration of a whole set.

    Attributes:
        centres_hz: 1/3-octave centres of the shape curves.
        mics: One entry per microphone, in input order.
        polarity_ambiguous: No clear majority of polarities: every microphone is flagged; check
            every cable.
        pair: Two microphones only: a flagged problem may belong to either one.
    """

    centres_hz: tuple[float, ...]
    mics: tuple[MicCalibration, ...]
    polarity_ambiguous: bool = False
    pair: bool = False

    @property
    def rejected(self) -> tuple[MicCalibration, ...]:
        """Microphones that should be checked or left out."""
        return tuple(m for m in self.mics if not m.ok)

    def of(self, usb_input: int) -> MicCalibration:
        """Calibration of one microphone.

        Raises:
            KeyError: The input is not part of the set.
        """
        for mic in self.mics:
            if mic.usb_input == usb_input:
                return mic
        raise KeyError(usb_input)

    def correction_db(self, usb_input: int, freqs_hz: FloatArray) -> FloatArray:
        """What to subtract from this microphone's curve at ``freqs_hz``: sensitivity and shape."""
        mic = self.of(usb_input)
        shape = np.interp(np.log(freqs_hz), np.log(self.centres_hz), mic.shape_db, left=0, right=0)
        return np.asarray(shape + mic.sensitivity_db, dtype=np.float64)


def mic_set_id(mics: Sequence[tuple[int, str]]) -> str:
    """Stable id of a microphone set from its ``(usb_input, model)`` pairs."""
    text = ";".join(f"{usb}:{model.strip().lower()}" for usb, model in sorted(mics))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:SET_ID_CHARS]


def _band(centres: FloatArray, span: tuple[float, float]) -> np.ndarray:
    return (centres >= span[0]) & (centres <= span[1])


def _curves(
    mics: Sequence[MicResult], fs_hz: float, cfg: CalibrationConfig
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Centres, long-window curves and shape curves (short window above the split)."""
    centres = third_octave_centres(cfg.centres_low_hz, cfg.centres_high_hz)
    long_curves = np.array([magnitude_db(m.ir, fs_hz, frac=3, grid_hz=centres) for m in mics])
    short = np.array(
        [
            magnitude_db(m.ir, fs_hz, frac=3, grid_hz=centres, window_s=cfg.short_window_s)
            for m in mics
        ]
    )
    shape_curves = np.where(centres >= cfg.short_window_from_hz, short, long_curves)
    return centres, long_curves, shape_curves


def _direct(ir: FloatArray, peak: int, fs_hz: float, cfg: CalibrationConfig) -> FloatArray:
    sos = signal.butter(2, cfg.polarity_band_hz, "bandpass", fs=fs_hz, output="sos")
    filtered = signal.sosfiltfilt(sos, ir)
    start = max(0, peak - int(cfg.direct_pre_s * fs_hz))
    return np.asarray(filtered[start : peak + int(cfg.direct_post_s * fs_hz)], dtype=np.float64)


def _lag_and_sign(x: FloatArray, ref: FloatArray, max_lag: int) -> tuple[float, float]:
    """Sub-sample lag of ``x`` against ``ref`` and the sign of the correlation at that lag."""
    n = min(len(x), len(ref))
    corr = signal.correlate(x[:n], ref[:n], mode="full")
    lags = np.arange(-n + 1, n)
    keep = np.abs(lags) <= max_lag
    corr, lags = corr[keep], lags[keep]
    i = int(np.argmax(np.abs(corr)))
    shift = 0.0
    if 0 < i < len(corr) - 1:
        a, b, c = np.abs(corr[i - 1]), np.abs(corr[i]), np.abs(corr[i + 1])
        denominator = a - 2 * b + c
        shift = 0.5 * (a - c) / denominator if denominator else 0.0
    return float(lags[i] + shift), float(np.sign(corr[i]))


def _timing(measurement: RoundMeasurement, cfg: CalibrationConfig) -> tuple[FloatArray, FloatArray]:
    """Delay (ms, against the set) and correlation sign of every microphone."""
    fs = measurement.fs_hz
    mics = measurement.mics
    peaks = np.array([m.peak_idx for m in mics])
    anchor = int(np.median(peaks))
    windows = [_direct(m.ir_full, anchor, fs, cfg) for m in mics]
    # The reference is one real microphone (the one nearest the median arrival): a sample-wise
    # median of two windows would be their mean, and each mic would correlate with its own half.
    reference = windows[int(np.argmin(np.abs(peaks - anchor)))]
    max_lag = int(cfg.max_lag_ms * 1e-3 * fs)
    results = [_lag_and_sign(w, reference, max_lag) for w in windows]
    lags = np.array([r[0] for r in results])
    return 1000.0 * (lags - np.median(lags)) / fs, np.array([r[1] for r in results])


def _check_quality(measurement: RoundMeasurement) -> None:
    bad = [m.usb_input for m in measurement.mics if m.quality.verdict is Verdict.BAD]
    if bad:
        raise CalibrationError(f"the calibration sweeps are bad at inputs {bad}; repeat them")


def calibrate(
    measurement: RoundMeasurement, cfg: CalibrationConfig | None = None
) -> MicSetCalibration:
    """Calibrate the microphones of a round taken with all of them side by side.

    Raises:
        ValueError: Fewer than two microphones.
        CalibrationError: A microphone's sweeps were bad (clipping, noise, movement, dropouts).
    """
    config = cfg or CalibrationConfig()
    mics = measurement.mics
    if len(mics) < MIN_MICS:
        raise ValueError("a microphone set needs at least two microphones")
    _check_quality(measurement)
    centres, long_curves, shape_curves = _curves(mics, measurement.fs_hz, config)
    sensitivity_band = _band(centres, config.sensitivity_band_hz)
    shape_band = _band(centres, config.shape_band_hz)
    sensitivity = np.mean(
        (long_curves - np.median(long_curves, axis=0))[:, sensitivity_band], axis=1
    )
    shapes = shape_curves - np.median(shape_curves, axis=0) - sensitivity[:, None]
    shapes[:, ~shape_band] = 0.0
    delays, signs = _timing(measurement, config)
    noise = np.array([m.quality.noise_dbfs for m in mics]) - sensitivity
    pair = len(mics) == PAIR
    if pair:  # the median halves every difference: judge the pair as a whole
        shapes_checked = np.repeat(np.max(np.abs(shapes[0] - shapes[1]))[None], 2)
        delays_checked = np.repeat(abs(delays[0] - delays[1]), 2)
        noise_checked = np.repeat(abs(noise[0] - noise[1]), 2)
    else:
        shapes_checked = np.max(np.abs(shapes[:, shape_band]), axis=1)
        delays_checked = np.abs(delays)
        noise_checked = noise - np.median(noise)
    total = float(np.sum(signs))
    # As many of each sign: nobody can tell which are wrong, so every microphone is suspect.
    ambiguous = abs(total) < 1
    majority = 1.0 if total > 0 else -1.0
    result = []
    for index, mic in enumerate(mics):
        inverted = ambiguous or bool(signs[index] != majority)
        problems = [
            problem
            for problem, bad in (
                (MicProblem.SHAPE, shapes_checked[index] > config.max_shape_db),
                (MicProblem.DELAY, delays_checked[index] > config.max_delay_ms),
                (MicProblem.POLARITY, inverted),
                (MicProblem.NOISE, noise_checked[index] > config.max_extra_noise_db),
            )
            if bad
        ]
        result.append(
            MicCalibration(
                mic.usb_input,
                round(float(sensitivity[index]), 3),
                round(float(delays[index]), 3),
                inverted,
                tuple(round(float(v), 3) for v in shapes[index]),
                round(float(shapes_checked[index]), 2),
                round(float(noise_checked[index]), 1),
                tuple(problems),
            )
        )
    return MicSetCalibration(tuple(float(c) for c in centres), tuple(result), ambiguous, pair)
