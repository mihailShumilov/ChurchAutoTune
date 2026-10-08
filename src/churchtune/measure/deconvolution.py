"""From a recorded sweep to an impulse response."""

import math

import numpy as np

from churchtune.domain.arrays import FloatArray
from churchtune.domain.levels import rms_dbfs

# Regularisation of the spectral division, relative to the median in-band power of the reference:
# light inside the sweep band, heavy outside it where the reference has no energy.
IN_BAND_REGULARISATION = 1e-4
OUT_OF_BAND_REGULARISATION = 1e2
# The impulse-response noise floor is taken before the direct sound, between the distortion
# products and the peak.
NOISE_WINDOW_START_S = 0.25
NOISE_WINDOW_END_S = 0.02


def deconvolve(
    rec: FloatArray, ref: FloatArray, fs_hz: float, f1_hz: float, f2_hz: float
) -> FloatArray:
    """Regularised spectral division ``IR = Y X* / (|X|^2 + eps(f))``.

    ``ref`` is the exact signal that was sent, on the same time base as ``rec``, so the system
    latency shows up as the position of the IR peak. Harmonic distortion products land at
    negative time (wrapped to the end of the buffer) and are cut away by :func:`extract_ir`.

    Args:
        rec: Recorded signal, ``float64[n]``.
        ref: Reference signal that was played, ``float64[n]``.
        fs_hz: Sample rate.
        f1_hz: Lower edge of the sweep band.
        f2_hz: Upper edge of the sweep band.

    Returns:
        Circular impulse response, ``float64[n_fft]`` with ``n_fft`` the next power of two of
        ``len(rec) + len(ref)``.

    Raises:
        ValueError: An input is empty or holds NaN or infinity.
    """
    if len(rec) == 0 or len(ref) == 0:
        raise ValueError("deconvolve needs a non-empty recording and reference")
    if not (np.isfinite(rec).all() and np.isfinite(ref).all()):
        raise ValueError("deconvolve needs finite samples (found NaN or infinity)")
    n_fft = int(2 ** math.ceil(math.log2(len(rec) + len(ref))))
    rec_spectrum = np.fft.rfft(rec, n_fft)
    ref_spectrum = np.fft.rfft(ref, n_fft)
    freqs = np.fft.rfftfreq(n_fft, 1 / fs_hz)
    power = np.abs(ref_spectrum) ** 2
    in_band = (freqs >= f1_hz) & (freqs <= f2_hz)
    ref_level = np.median(power[in_band]) if in_band.any() else power.max()
    eps = np.where(in_band, IN_BAND_REGULARISATION, OUT_OF_BAND_REGULARISATION) * ref_level
    ir = np.fft.irfft(rec_spectrum * np.conj(ref_spectrum) / (power + eps), n_fft)
    return np.asarray(ir, dtype=np.float64)


def extract_ir(
    ir_full: FloatArray, fs_hz: float, length_s: float, pre_ms: float
) -> tuple[FloatArray, int]:
    """Cut the causal part of the impulse response around its main peak.

    Args:
        ir_full: Circular impulse response from :func:`deconvolve`.
        fs_hz: Sample rate.
        length_s: How much to keep after the peak.
        pre_ms: How much to keep before the peak.

    Returns:
        ``(ir, peak_idx)``: the cut response and the index of the peak in ``ir_full``. The peak
        is searched in the first half only; the second half holds the distortion products.
    """
    half = len(ir_full) // 2
    peak_idx = int(np.argmax(np.abs(ir_full[:half])))
    start = max(0, peak_idx - int(pre_ms * 1e-3 * fs_hz))
    stop = min(len(ir_full), peak_idx + int(length_s * fs_hz))
    return ir_full[start:stop].copy(), peak_idx


def ir_noise_dbfs(ir_full: FloatArray, peak_idx: int, fs_hz: float) -> float:
    """Noise floor of the impulse response, measured in ``[-0.25 s, -0.02 s]`` before the peak."""
    start = max(0, peak_idx - int(NOISE_WINDOW_START_S * fs_hz))
    stop = max(start + 1, peak_idx - int(NOISE_WINDOW_END_S * fs_hz))
    return rms_dbfs(ir_full[start:stop])
