"""Frequency response from an impulse response.

Windowing, fractional-octave power smoothing, normalisation and averaging across points.
"""

import math

import numpy as np

from churchtune.domain.arrays import FloatArray

GRID_POINTS_PER_OCTAVE = 48
GRID_START_HZ = 20.0
GRID_END_HZ = 20000.0
# Impulse-response window used for the magnitude response.
FR_WINDOW_S = 0.5
# The last part of the window is faded out with half a Hann window.
WINDOW_FADE_FRACTION = 0.2
MIN_NFFT = 1 << 16
DEFAULT_SMOOTHING_FRACTION = 6
# Each point is normalised to 0 dB in this band.
NORM_BAND_HZ: tuple[float, float] = (500.0, 2000.0)
LOW_CUTOFF_SEARCH_HZ: tuple[float, float] = (25.0, 200.0)
DEFAULT_LOW_CUTOFF_DROP_DB = -10.0
# The pass band starts where the response stays above the drop for this long.
PASS_BAND_MIN_OCT = 1 / 3
_POWER_FLOOR = 1e-30
_MIN_BAND_WIDTH_HZ = 1e-9


def log_grid(
    start_hz: float = GRID_START_HZ,
    end_hz: float = GRID_END_HZ,
    points_per_octave: int = GRID_POINTS_PER_OCTAVE,
) -> FloatArray:
    """Logarithmic frequency grid from ``start_hz`` up to at most ``end_hz``."""
    n_octaves = math.ceil(math.log2(end_hz / start_hz))
    grid = start_hz * 2 ** (np.arange(0, n_octaves * points_per_octave + 1) / points_per_octave)
    return np.asarray(grid[grid <= end_hz], dtype=np.float64)


# The analysis grid: 1/48 octave from 20 Hz to 20 kHz (479 points).
GRID_HZ: FloatArray = log_grid()


def magnitude_db(
    ir: FloatArray,
    fs_hz: float,
    *,
    window_s: float = FR_WINDOW_S,
    frac: int = DEFAULT_SMOOTHING_FRACTION,
    grid_hz: FloatArray = GRID_HZ,
) -> FloatArray:
    """Fractional-octave power-smoothed magnitude of an impulse response.

    The response is windowed to its first ``window_s`` seconds (the last 20 % faded out), and
    the power spectrum is averaged over a band ``1/frac`` octave wide around each grid point.

    Args:
        ir: Impulse response, ``float64[n]``, starting shortly before the direct sound.
        fs_hz: Sample rate.
        window_s: Length of the analysis window.
        frac: Smoothing width as a fraction of an octave (6 = 1/6 octave).
        grid_hz: Frequencies to evaluate, ``float64[n_freqs]``.

    Returns:
        Magnitude in dB, ``float64[n_freqs]``; a unit impulse gives 0 dB everywhere.

    Raises:
        ValueError: ``ir`` is empty or not finite, or ``fs_hz`` is not positive.
    """
    if len(ir) == 0:
        raise ValueError("magnitude_db needs a non-empty impulse response")
    if not (math.isfinite(fs_hz) and fs_hz > 0):
        raise ValueError(f"fs_hz must be a positive finite number, got {fs_hz!r}")
    if not np.isfinite(ir).all():
        raise ValueError("magnitude_db needs finite samples (found NaN or infinity)")
    n = min(len(ir), int(window_s * fs_hz))
    h = np.array(ir[:n], dtype=np.float64)
    fade = int(WINDOW_FADE_FRACTION * n)
    if fade > 0:
        h[n - fade :] *= np.hanning(2 * fade)[fade:]
    nfft = max(1 << math.ceil(math.log2(max(n, 1) * 2)), MIN_NFFT)
    power = np.abs(np.fft.rfft(h, nfft)) ** 2
    freqs = np.fft.rfftfreq(nfft, 1 / fs_hz)
    df = freqs[1] - freqs[0]
    cumulative = np.concatenate([[0.0], np.cumsum(power) * df])
    edges = np.concatenate([[0.0], freqs + df / 2])  # cumulative integral at bin edges
    half_band = 2 ** (1 / (2 * frac))
    low, high = grid_hz / half_band, np.minimum(grid_hz * half_band, freqs[-1])
    band_power = (np.interp(high, edges, cumulative) - np.interp(low, edges, cumulative)) / (
        np.maximum(high - low, _MIN_BAND_WIDTH_HZ)
    )
    return np.asarray(10 * np.log10(np.maximum(band_power, _POWER_FLOOR)), dtype=np.float64)


def band_mean_db(
    curve_db: FloatArray, low_hz: float, high_hz: float, grid_hz: FloatArray = GRID_HZ
) -> float:
    """Power mean of a curve between two frequencies, in dB.

    Raises:
        ValueError: No grid point lies in the band.
    """
    in_band = (grid_hz >= low_hz) & (grid_hz <= high_hz)
    if not in_band.any():
        raise ValueError(f"no grid point between {low_hz} and {high_hz} Hz")
    return float(10 * np.log10(np.mean(10 ** (curve_db[in_band] / 10))))


def energy_average(curves_db: FloatArray) -> FloatArray:
    """Power average across points.

    Args:
        curves_db: ``float64[n_points, n_freqs]``.

    Returns:
        ``float64[n_freqs]``.
    """
    curves = np.asarray(curves_db, dtype=np.float64)
    return np.asarray(10 * np.log10(np.mean(10 ** (curves / 10), axis=0)), dtype=np.float64)


def interpolate_at(
    freqs_hz: FloatArray, curve_db: FloatArray, grid_hz: FloatArray = GRID_HZ
) -> FloatArray:
    """Read a curve at arbitrary frequencies (linear in dB over log frequency)."""
    return np.asarray(np.interp(np.log(freqs_hz), np.log(grid_hz), curve_db), dtype=np.float64)


def sixth_octave_centres(low_hz: float = 31.5, high_hz: float = 16000.0) -> FloatArray:
    """1/6-octave centre frequencies around 1 kHz within ``[low_hz, high_hz]``."""
    centres = 1000 * 2 ** (np.arange(-30, 31) / 6.0)
    return np.asarray(centres[(centres >= low_hz * 0.99) & (centres <= high_hz * 1.01)])


def third_octave_centres(low_hz: float = 25.0, high_hz: float = 20000.0) -> FloatArray:
    """1/3-octave centre frequencies around 1 kHz within ``[low_hz, high_hz]``."""
    centres = 1000 * 2 ** (np.arange(-16, 15) / 3.0)
    return np.asarray(centres[(centres >= low_hz * 0.99) & (centres <= high_hz * 1.01)])


def calibration_on_grid(
    freqs_hz: FloatArray, correction_db: FloatArray, grid_hz: FloatArray = GRID_HZ
) -> FloatArray:
    """Interpolate a microphone calibration curve onto the analysis grid.

    Args:
        freqs_hz: Frequencies of the calibration file, ascending, ``float64[n]``.
        correction_db: Microphone response at those frequencies, in dB.
        grid_hz: Analysis grid.

    Returns:
        The response on the grid, ``float64[n_freqs]``; subtract it from a measured curve.
    """
    return np.asarray(np.interp(np.log(grid_hz), np.log(freqs_hz), correction_db), dtype=np.float64)


def low_cutoff_hz(
    avg_db: FloatArray,
    grid_hz: FloatArray = GRID_HZ,
    drop_db: float = DEFAULT_LOW_CUTOFF_DROP_DB,
) -> float:
    """Where the speakers' own roll-off ends: the start of the pass band, 25..200 Hz.

    Scanning up from 25 Hz, the first frequency above which the averaged response stays within
    ``drop_db`` of 0 dB for at least a third of an octave. A dip above that point (a sub/top
    cancellation, a room null) is not a roll-off: it is the junction's or the room's, and the
    design rules decide what to do with it. Below the result the system is not corrected.
    """
    search = (grid_hz >= LOW_CUTOFF_SEARCH_HZ[0]) & (grid_hz <= LOW_CUTOFF_SEARCH_HZ[1])
    freqs, levels = grid_hz[search], avg_db[search]
    steady = max(1, round(len(freqs) / np.log2(freqs[-1] / freqs[0]) * PASS_BAND_MIN_OCT))
    inside = levels >= drop_db
    for start in range(len(freqs) - steady + 1):  # a full third of an octave each time
        if inside[start : start + steady].all():
            return float(freqs[start])
    return float(freqs[-1])
