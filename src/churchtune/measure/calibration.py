"""Relative calibration of a set of measurement microphones (SPEC S9, skill room-measurement).

All microphones stand together (capsules within 10 cm, same height and direction) and record the
same sweeps in one stream. Each one is compared with the median of the set in 1/3 octaves:

* sensitivity: mean level difference in 200 Hz-2 kHz;
* shape: what is left after the sensitivity, used as a correction curve;
* delay: arrival time against the median of the set;
* polarity: sign of the direct sound against the majority.

A microphone is rejected when its shape deviates more than ±3 dB in 100 Hz-8 kHz, its delay
differs more than 0.5 ms, its polarity is inverted or it is much noisier than the others.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from churchtune.analysis.response import magnitude_db, third_octave_centres
from churchtune.domain.arrays import FloatArray
from churchtune.measure.multi import RoundMeasurement

CENTRES_LOW_HZ, CENTRES_HIGH_HZ = 31.5, 16000.0
SENSITIVITY_BAND_HZ = (200.0, 2000.0)
SHAPE_BAND_HZ = (100.0, 8000.0)
SMOOTHING_FRACTION = 3
MAX_SHAPE_DB = 3.0
MAX_DELAY_MS = 0.5
MAX_EXTRA_NOISE_DB = 10.0
MIN_MICS = 2
SET_ID_CHARS = 12


class MicProblem(StrEnum):
    """Why a microphone of the set is rejected."""

    SHAPE = "shape"
    DELAY = "delay"
    POLARITY = "polarity"
    NOISE = "noise"


@dataclass(frozen=True, slots=True)
class MicCalibration:
    """One microphone against the median of its set.

    Attributes:
        usb_input: 1-based USB input of the microphone.
        sensitivity_db: Level above the set median (subtract it from this mic's curves).
        delay_ms: Arrival later than the set median.
        inverted: True when the direct sound has the opposite sign of the majority.
        shape_db: Response deviation after the sensitivity, per band of ``centres_hz``.
        max_shape_db: Largest ``|shape_db|`` in 100 Hz-8 kHz.
        noise_db: Room-noise level above the set median.
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
    """

    centres_hz: tuple[float, ...]
    mics: tuple[MicCalibration, ...]

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
        shape = np.interp(np.log(freqs_hz), np.log(self.centres_hz), mic.shape_db)
        return np.asarray(shape + mic.sensitivity_db, dtype=np.float64)


def mic_set_id(mics: Sequence[tuple[int, str]]) -> str:
    """Stable id of a microphone set from its ``(usb_input, model)`` pairs."""
    text = ";".join(f"{usb}:{model.strip().lower()}" for usb, model in sorted(mics))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:SET_ID_CHARS]


def _band(centres: FloatArray, span: tuple[float, float]) -> np.ndarray:
    return (centres >= span[0]) & (centres <= span[1])


def calibrate(measurement: RoundMeasurement) -> MicSetCalibration:
    """Calibrate the microphones of a round taken with all of them side by side.

    Raises:
        ValueError: Fewer than two microphones.
    """
    if len(measurement.mics) < MIN_MICS:
        raise ValueError("a microphone set needs at least two microphones")
    fs_hz = measurement.fs_hz
    centres = third_octave_centres(CENTRES_LOW_HZ, CENTRES_HIGH_HZ)
    curves = np.array(
        [
            magnitude_db(m.ir, fs_hz, frac=SMOOTHING_FRACTION, grid_hz=centres)
            for m in measurement.mics
        ]
    )
    median = np.median(curves, axis=0)
    sensitivity_band, shape_band = (
        _band(centres, SENSITIVITY_BAND_HZ),
        _band(centres, SHAPE_BAND_HZ),
    )
    latency_ms = np.array([1000.0 * m.peak_idx / fs_hz for m in measurement.mics])
    signs = np.array([np.sign(m.ir_full[m.peak_idx]) for m in measurement.mics])
    majority = 1.0 if np.sum(signs) >= 0 else -1.0
    noise = np.array([m.quality.noise_dbfs for m in measurement.mics])
    mics = []
    for index, result in enumerate(measurement.mics):
        difference = curves[index] - median
        sensitivity = float(np.mean(difference[sensitivity_band]))
        shape = difference - sensitivity
        max_shape = float(np.max(np.abs(shape[shape_band])))
        delay = float(latency_ms[index] - np.median(latency_ms))
        extra_noise = float(noise[index] - np.median(noise))
        inverted = bool(signs[index] != majority)
        problems = [
            problem
            for problem, bad in (
                (MicProblem.SHAPE, max_shape > MAX_SHAPE_DB),
                (MicProblem.DELAY, abs(delay) > MAX_DELAY_MS),
                (MicProblem.POLARITY, inverted),
                (MicProblem.NOISE, extra_noise > MAX_EXTRA_NOISE_DB),
            )
            if bad
        ]
        mics.append(
            MicCalibration(
                result.usb_input,
                round(sensitivity, 3),
                round(delay, 3),
                inverted,
                tuple(round(float(v), 3) for v in shape),
                round(max_shape, 2),
                round(extra_noise, 1),
                tuple(problems),
            )
        )
    return MicSetCalibration(tuple(float(c) for c in centres), tuple(mics))
