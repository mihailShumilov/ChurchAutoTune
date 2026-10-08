"""Level helpers and the one hard output limit.

``HARD_LIMIT_DBFS`` is the loudest peak that may ever leave the computer (SPEC §9.4). Stimulus
generation refuses to exceed it and the audio backends clip to it.
"""

import numpy as np

from churchtune.domain.arrays import FloatArray

HARD_LIMIT_DBFS = -6.0
# Reported for empty or all-zero signals instead of -inf.
SILENCE_DBFS = -200.0
_AMPLITUDE_FLOOR = 1e-12
_POWER_FLOOR = 1e-20


def db_to_lin(level_db: float) -> float:
    """Convert a level in dB to a linear amplitude factor."""
    return float(10.0 ** (level_db / 20.0))


def amplitude_db(x: FloatArray) -> FloatArray:
    """Amplitude in dB of each element, floored at -240 dB.

    Args:
        x: Linear amplitudes, any shape.

    Returns:
        ``float64`` array of the same shape.
    """
    return np.asarray(20 * np.log10(np.maximum(np.abs(x), _AMPLITUDE_FLOOR)), dtype=np.float64)


def rms_dbfs(x: FloatArray) -> float:
    """RMS level of a signal in dBFS; ``SILENCE_DBFS`` for an empty or silent signal."""
    samples = np.asarray(x, dtype=np.float64)
    if samples.size == 0:
        return SILENCE_DBFS
    return float(10 * np.log10(max(float(np.mean(samples * samples)), _POWER_FLOOR)))


def peak_dbfs(x: FloatArray) -> float:
    """Peak level of a signal in dBFS; ``SILENCE_DBFS`` for an empty signal."""
    samples = np.asarray(x, dtype=np.float64)
    if samples.size == 0:
        return SILENCE_DBFS
    return float(20 * np.log10(max(float(np.max(np.abs(samples))), _AMPLITUDE_FLOOR)))
