"""Reverberation time per octave band: Schroeder backward integration, EDT / T20 / T30."""

from dataclasses import dataclass

import numpy as np
from scipy import signal

from churchtune.analysis.bands import OCTAVES_HZ, octave_band_sos
from churchtune.domain.arrays import FloatArray

# A band-filtered response shorter than this after its peak cannot be evaluated.
MIN_DECAY_S = 0.2
# The noise floor is estimated from the last tenth of the response.
NOISE_TAIL_FRACTION = 0.9
ENVELOPE_WINDOW_S = 0.01
PEAK_SEARCH_S = 0.05
# The decay is truncated where its envelope falls to this much above the noise floor ...
TRUNCATION_ABOVE_NOISE_DB = 5.0
# ... but never before this time.
MIN_TRUNCATION_S = 0.1
MIN_FIT_POINTS = 10
# Dynamic range needed to trust a fit down to -25 dB (T20) and -35 dB (T30).
T20_MIN_DYNAMIC_DB = 35.0
T30_MIN_DYNAMIC_DB = 45.0
EDT_RANGE_DB = (0.0, -10.0)
T20_RANGE_DB = (-5.0, -25.0)
T30_RANGE_DB = (-5.0, -35.0)
# ISO 3382 curvature 100 * (T30 / T20 - 1); outside this range the decay is far from
# exponential (typical below 125 Hz) and T30 is dropped.
CURVATURE_RANGE_PCT = (-33.0, 50.0)
_POWER_FLOOR = 1e-30
_FLAT_SLOPE_DB_PER_S = -1e-6


@dataclass(frozen=True, slots=True)
class BandDecay:
    """Decay times of one octave band at one point; None when not measurable.

    Attributes:
        edt_s: Early decay time (fit 0..-10 dB).
        t20_s: RT60 from the fit -5..-25 dB.
        t30_s: RT60 from the fit -5..-35 dB.
        curvature_pct: ``100 * (T30 / T20 - 1)`` when both were measured.
        dynamic_db: Peak of the envelope over the noise floor.
    """

    edt_s: float | None
    t20_s: float | None
    t30_s: float | None
    curvature_pct: float | None
    dynamic_db: float


def schroeder_db(h: FloatArray, fs_hz: float) -> tuple[FloatArray | None, float]:
    """Backward-integrated energy decay with noise compensation (simplified Lundeby).

    The response is cut where its envelope reaches 5 dB above the noise floor, and the noise
    power is subtracted before integrating.

    Args:
        h: Band-filtered impulse response, ``float64[n]``.
        fs_hz: Sample rate.

    Returns:
        ``(decay_db, dynamic_db)``: the decay curve in dB (0 at its start), or None when the
        response is shorter than 0.2 s after its peak; and the dynamic range of the envelope.

    Raises:
        ValueError: ``h`` holds NaN or infinity.
    """
    if not np.isfinite(h).all():
        raise ValueError("schroeder_db needs finite samples (found NaN or infinity)")
    energy = np.asarray(h, dtype=np.float64) ** 2
    energy = energy[int(np.argmax(energy)) :]
    if len(energy) < fs_hz * MIN_DECAY_S:
        return None, 0.0
    noise = float(np.mean(energy[int(NOISE_TAIL_FRACTION * len(energy)) :]))
    window = max(1, int(ENVELOPE_WINDOW_S * fs_hz))
    envelope = np.convolve(energy, np.ones(window) / window, mode="same")
    envelope_db = 10 * np.log10(np.maximum(envelope, _POWER_FLOOR))
    noise_db = 10 * np.log10(max(noise, _POWER_FLOOR))
    peak_db = float(envelope_db[: int(PEAK_SEARCH_S * fs_hz)].max())
    dynamic_db = peak_db - noise_db
    above = np.nonzero(envelope_db > noise_db + TRUNCATION_ABOVE_NOISE_DB)[0]
    cut = int(above[-1]) if len(above) else len(energy) - 1
    cut = max(cut, int(MIN_TRUNCATION_S * fs_hz))
    compensated = np.clip(energy[:cut] - noise, 0, None)
    integral = np.cumsum(compensated[::-1])[::-1]
    integral = integral / max(integral[0], _POWER_FLOOR)
    decay_db = np.asarray(10 * np.log10(np.maximum(integral, _POWER_FLOOR)), dtype=np.float64)
    return decay_db, float(dynamic_db)


def _fit_rt60(
    decay_db: FloatArray, fs_hz: float, fit_range_db: tuple[float, float]
) -> float | None:
    """RT60 from a straight-line fit of the decay between two levels; None if not possible."""
    top_db, bottom_db = fit_range_db
    idx = np.nonzero((decay_db <= top_db) & (decay_db >= bottom_db))[0]
    if len(idx) < MIN_FIT_POINTS or decay_db.min() > bottom_db:
        return None
    slope, _ = np.polyfit(idx / fs_hz, decay_db[idx], 1)
    if slope >= _FLAT_SLOPE_DB_PER_S:
        return None
    return float(-60.0 / slope)


def rt60_octaves(
    ir: FloatArray, fs_hz: float, bands_hz: tuple[int, ...] = OCTAVES_HZ
) -> dict[int, BandDecay]:
    """EDT, T20 and T30 per octave band.

    T20 needs at least 35 dB and T30 at least 45 dB of dynamic range; T30 is also dropped when
    the decay is strongly non-exponential.

    Args:
        ir: Impulse response, ``float64[n]``.
        fs_hz: Sample rate.
        bands_hz: Octave band centres.

    Returns:
        Decay times keyed by band centre.
    """
    result: dict[int, BandDecay] = {}
    for centre in bands_hz:
        band = np.asarray(signal.sosfilt(octave_band_sos(centre, fs_hz), ir), dtype=np.float64)
        decay_db, dynamic_db = schroeder_db(band, fs_hz)
        if decay_db is None:
            result[centre] = BandDecay(None, None, None, None, 0.0)
            continue
        t20 = _fit_rt60(decay_db, fs_hz, T20_RANGE_DB) if dynamic_db >= T20_MIN_DYNAMIC_DB else None
        t30 = _fit_rt60(decay_db, fs_hz, T30_RANGE_DB) if dynamic_db >= T30_MIN_DYNAMIC_DB else None
        curvature = round(100 * (t30 / t20 - 1), 1) if (t20 and t30) else None
        if curvature is not None and not (
            CURVATURE_RANGE_PCT[0] <= curvature <= CURVATURE_RANGE_PCT[1]
        ):
            t30 = None
        result[centre] = BandDecay(
            edt_s=_fit_rt60(decay_db, fs_hz, EDT_RANGE_DB),
            t20_s=t20,
            t30_s=t30,
            curvature_pct=curvature,
            dynamic_db=round(dynamic_db, 1),
        )
    return result
