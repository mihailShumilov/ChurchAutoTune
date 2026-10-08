"""Octave-band filters and levels."""

import math

import numpy as np
from scipy import signal

from churchtune.domain.arrays import FloatArray
from churchtune.domain.levels import rms_dbfs

# Octave band centres used for RT60 and noise analysis, in Hz.
OCTAVES_HZ: tuple[int, ...] = (63, 125, 250, 500, 1000, 2000, 4000, 8000)
DEFAULT_ORDER = 3
# The upper band edge is kept below this fraction of the sample rate.
MAX_EDGE_FRACTION = 0.45


def octave_band_sos(centre_hz: float, fs_hz: float, order: int = DEFAULT_ORDER) -> FloatArray:
    """Butterworth band-pass of one octave around ``centre_hz``.

    Args:
        centre_hz: Band centre.
        fs_hz: Sample rate.
        order: Butterworth order of each band edge.

    Returns:
        SOS array ``float64[order, 6]``.

    Raises:
        ValueError: The band does not fit below ``0.45 * fs_hz``.
    """
    low = centre_hz / math.sqrt(2)
    high = min(centre_hz * math.sqrt(2), fs_hz * MAX_EDGE_FRACTION)
    if not 0 < low < high:
        raise ValueError(f"octave band at {centre_hz} Hz does not fit a sample rate of {fs_hz} Hz")
    sos = signal.butter(order, [low, high], btype="bandpass", fs=fs_hz, output="sos")
    return np.asarray(sos, dtype=np.float64)


def octave_levels(
    x: FloatArray, fs_hz: float, bands_hz: tuple[int, ...] = OCTAVES_HZ
) -> dict[int, float]:
    """RMS level per octave band, in dBFS.

    Args:
        x: Signal, ``float64[n]``.
        fs_hz: Sample rate.
        bands_hz: Band centres.

    Returns:
        Level in dBFS keyed by band centre.
    """
    return {
        centre: rms_dbfs(np.asarray(signal.sosfilt(octave_band_sos(centre, fs_hz), x)))
        for centre in bands_hz
    }
