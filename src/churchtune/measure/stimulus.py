"""Test signals: Farina exponential sweep, the play/record buffer around it, and pink noise.

Every generator refuses a level above the hard output limit.
"""

import math

import numpy as np
import numpy.typing as npt

from churchtune.domain.arrays import FloatArray
from churchtune.domain.levels import HARD_LIMIT_DBFS, db_to_lin
from churchtune.errors import LevelError
from churchtune.measure.config import SweepConfig

PINK_F1_HZ = 20.0
PINK_F2_HZ = 20000.0
DEFAULT_PINK_RMS_DBFS = -27.0


def check_level(level_dbfs: float) -> None:
    """Refuse a level above the hard output limit.

    Raises:
        LevelError: ``level_dbfs`` is above ``HARD_LIMIT_DBFS`` or not a number.
    """
    if not level_dbfs <= HARD_LIMIT_DBFS:
        raise LevelError(
            f"level {level_dbfs:+.1f} dBFS is above the hard limit {HARD_LIMIT_DBFS:+.1f} dBFS",
            level_dbfs=level_dbfs,
            limit_dbfs=HARD_LIMIT_DBFS,
        )


def _check_fs(fs_hz: float) -> None:
    if not (math.isfinite(fs_hz) and fs_hz > 0):
        raise ValueError(f"fs_hz must be a positive finite number, got {fs_hz!r}")


def half_cos_fade(n_samples: int) -> FloatArray:
    """Rising half-cosine ramp from 0 to 1, ``float64[n_samples]`` (empty for ``n <= 0``)."""
    if n_samples <= 0:
        return np.ones(0, dtype=np.float64)
    return np.asarray(0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n_samples)), dtype=np.float64)


def make_sweep(fs_hz: float, sweep: SweepConfig, level_dbfs: float | None = None) -> FloatArray:
    """Exponential (log) sine sweep with half-cosine fades.

    Args:
        fs_hz: Sample rate.
        sweep: Sweep parameters.
        level_dbfs: Peak level; ``sweep.level_dbfs`` when omitted.

    Returns:
        The sweep, ``float64[round(duration_s * fs_hz)]``, peak at ``level_dbfs``.

    Raises:
        LevelError: The level is above the hard output limit.
        ValueError: ``fs_hz`` or the sweep range is invalid.
    """
    level = sweep.level_dbfs if level_dbfs is None else level_dbfs
    check_level(level)
    _check_fs(fs_hz)
    if not 0 < sweep.f1_hz < sweep.f2_hz:
        raise ValueError(f"sweep range must satisfy 0 < f1 < f2, got {sweep.f1_hz}..{sweep.f2_hz}")
    n = round(sweep.duration_s * fs_hz)
    t = np.arange(n) / fs_hz
    rate = math.log(sweep.f2_hz / sweep.f1_hz)
    phase = 2 * np.pi * sweep.f1_hz * sweep.duration_s / rate
    x = np.sin(phase * (np.exp(t * rate / sweep.duration_s) - 1.0))
    n_in, n_out = int(sweep.fade_in_s * fs_hz), int(sweep.fade_out_s * fs_hz)
    x[:n_in] *= half_cos_fade(n_in)
    x[n - n_out :] *= half_cos_fade(n_out)[::-1]
    return np.asarray(x * db_to_lin(level), dtype=np.float64)


def build_stimulus(
    signal: FloatArray, fs_hz: float, preroll_s: float, tail_s: float
) -> npt.NDArray[np.float32]:
    """Wrap a mono signal in silence and duplicate it to two output channels.

    Args:
        signal: Mono test signal, ``float64[n]``.
        fs_hz: Sample rate.
        preroll_s: Silence before the signal.
        tail_s: Silence after the signal (reverb tail).

    Returns:
        ``float32[n_total, 2]`` buffer for the audio device.
    """
    _check_fs(fs_hz)
    pre = np.zeros(int(preroll_s * fs_hz))
    post = np.zeros(int(tail_s * fs_hz))
    mono = np.concatenate([pre, signal, post])
    return np.stack([mono, mono], axis=1).astype(np.float32)


def pink_noise(
    duration_s: float,
    fs_hz: float,
    rng: np.random.Generator,
    rms_level_dbfs: float = DEFAULT_PINK_RMS_DBFS,
    peak_limit_dbfs: float = HARD_LIMIT_DBFS,
) -> FloatArray:
    """Seamlessly loopable pink noise (1/f power, 20 Hz..20 kHz), peak-limited.

    Args:
        duration_s: Length of the loop.
        fs_hz: Sample rate.
        rng: Random generator (pass a seeded one for reproducible noise).
        rms_level_dbfs: Wanted RMS level; lowered if the peak would exceed the limit.
        peak_limit_dbfs: Highest peak allowed.

    Returns:
        ``float64[int(duration_s * fs_hz)]``.

    Raises:
        LevelError: A requested level is above the hard output limit.
        ValueError: ``fs_hz`` is invalid.
    """
    check_level(rms_level_dbfs)
    check_level(peak_limit_dbfs)
    _check_fs(fs_hz)
    n = int(duration_s * fs_hz)
    spectrum = rng.normal(size=n // 2 + 1) + 1j * rng.normal(size=n // 2 + 1)
    freqs = np.fft.rfftfreq(n, 1 / fs_hz)
    shape = np.zeros_like(freqs)
    band = (freqs >= PINK_F1_HZ) & (freqs <= PINK_F2_HZ)
    shape[band] = 1 / np.sqrt(freqs[band])
    x = np.fft.irfft(spectrum * shape, n)
    x /= np.sqrt(np.mean(x**2))
    x *= db_to_lin(rms_level_dbfs)
    limit = db_to_lin(peak_limit_dbfs)
    peak = float(np.max(np.abs(x)))
    if peak > limit:
        x *= limit / peak
    return np.asarray(x, dtype=np.float64)
