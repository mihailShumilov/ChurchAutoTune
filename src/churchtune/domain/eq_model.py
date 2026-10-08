"""Approximate magnitude-response model of the console's six-band EQ.

Parametric bands and shelves are RBJ biquads; Butterworth / Bessel / Linkwitz-Riley slopes use the
analog prototype magnitude. The exact console internals are not public, so the model is used for
predictions and for the room simulator, never as a statement of what the console outputs.

The model lives in ``domain`` because ``design``, ``safety`` and the fake audio backend all need it
and may depend only on ``domain`` (docs/adr/0002).
"""

import math
from typing import Literal

import numpy as np
from scipy import signal

from churchtune.domain.arrays import FloatArray
from churchtune.domain.eq import Band, EqType, SystemEq

# Magnitudes below this (linear) are reported as -240 dB instead of -inf.
MAGNITUDE_FLOOR = 1e-12
# Parametric bands with |gain| below this are treated as exactly flat.
FLAT_GAIN_DB = 1e-9
# Shelves need a finite slope; the console's Q range ends at 0.3.
MIN_SHELF_Q = 0.3
# Q of the fixed second-order LCut/HCut (Butterworth).
CUT_Q = 0.707
# Biquad corner frequencies are kept below this fraction of the sample rate.
MAX_CORNER_FRACTION = 0.49
# Slope types are a low cut on bands 1..3 and a high cut on bands 4..6.
LAST_LOW_CUT_BAND = 3

_SLOPE_ORDER: dict[EqType, int] = {
    EqType.BU6: 1,
    EqType.BU12: 2,
    EqType.BS12: 2,
    EqType.LR12: 1,
    EqType.BU18: 3,
    EqType.BU24: 4,
    EqType.BS24: 4,
    EqType.LR24: 2,
}
_BESSEL = frozenset({EqType.BS12, EqType.BS24})
_LINKWITZ_RILEY = frozenset({EqType.LR12, EqType.LR24})
_PARAMETRIC = frozenset({EqType.PEQ, EqType.VEQ})


def _check_fs(fs_hz: float) -> None:
    if not (math.isfinite(fs_hz) and fs_hz > 0):
        raise ValueError(f"fs_hz must be a positive finite number, got {fs_hz!r}")


def _rbj(kind: str, f0_hz: float, q: float, gain_db: float, fs_hz: float) -> FloatArray:
    """RBJ cookbook biquad as one normalised SOS row ``[b0, b1, b2, 1, a1, a2]``."""
    amp = 10 ** (gain_db / 40.0)
    w0 = 2 * math.pi * min(f0_hz, fs_hz * MAX_CORNER_FRACTION) / fs_hz
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2 * q)
    if kind == "peak":
        b = [1 + alpha * amp, -2 * cw, 1 - alpha * amp]
        a = [1 + alpha / amp, -2 * cw, 1 - alpha / amp]
    elif kind == "lowshelf":
        s = 2 * math.sqrt(amp) * alpha
        b = [
            amp * ((amp + 1) - (amp - 1) * cw + s),
            2 * amp * ((amp - 1) - (amp + 1) * cw),
            amp * ((amp + 1) - (amp - 1) * cw - s),
        ]
        a = [
            (amp + 1) + (amp - 1) * cw + s,
            -2 * ((amp - 1) + (amp + 1) * cw),
            (amp + 1) + (amp - 1) * cw - s,
        ]
    elif kind == "highshelf":
        s = 2 * math.sqrt(amp) * alpha
        b = [
            amp * ((amp + 1) + (amp - 1) * cw + s),
            -2 * amp * ((amp - 1) + (amp + 1) * cw),
            amp * ((amp + 1) + (amp - 1) * cw - s),
        ]
        a = [
            (amp + 1) - (amp - 1) * cw + s,
            2 * ((amp - 1) - (amp + 1) * cw),
            (amp + 1) - (amp - 1) * cw - s,
        ]
    elif kind == "hp":
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "lp":
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    else:
        raise ValueError(f"unknown biquad kind {kind!r}")
    return np.array([*b, *a], dtype=np.float64) / a[0]


def _biquad(band: Band, fs_hz: float) -> FloatArray | None:
    """SOS row of a band realised as one RBJ biquad; None for slope types."""
    if band.type in _PARAMETRIC:
        return _rbj("peak", band.freq_hz, band.q, band.gain_db, fs_hz)
    if band.type is EqType.LSHV:
        return _rbj("lowshelf", band.freq_hz, max(band.q, MIN_SHELF_Q), band.gain_db, fs_hz)
    if band.type is EqType.HSHV:
        return _rbj("highshelf", band.freq_hz, max(band.q, MIN_SHELF_Q), band.gain_db, fs_hz)
    if band.type is EqType.LCUT:
        return _rbj("hp", band.freq_hz, CUT_Q, 0.0, fs_hz)
    if band.type is EqType.HCUT:
        return _rbj("lp", band.freq_hz, CUT_Q, 0.0, fs_hz)
    return None


def _biquad_db(row: FloatArray, freqs_hz: FloatArray, fs_hz: float) -> FloatArray:
    z = np.exp(-1j * 2 * np.pi * freqs_hz / fs_hz)
    h = (row[0] + row[1] * z + row[2] * z * z) / (row[3] + row[4] * z + row[5] * z * z)
    return np.asarray(20 * np.log10(np.maximum(np.abs(h), MAGNITUDE_FLOOR)), dtype=np.float64)


def _slope_btype(band: Band) -> Literal["highpass", "lowpass"]:
    return "highpass" if band.band <= LAST_LOW_CUT_BAND else "lowpass"


def _analog_slope_db(band: Band, freqs_hz: FloatArray) -> FloatArray:
    """Magnitude of a Butterworth / Bessel / Linkwitz-Riley slope from its analog prototype."""
    order = _SLOPE_ORDER[band.type]
    wc = 2 * math.pi * band.freq_hz
    if band.type in _BESSEL:
        b, a = signal.bessel(order, wc, btype=_slope_btype(band), analog=True, norm="mag")
    else:
        b, a = signal.butter(order, wc, btype=_slope_btype(band), analog=True)
    _, h = signal.freqs(b, a, worN=2 * np.pi * freqs_hz)
    db = np.asarray(20 * np.log10(np.maximum(np.abs(h), MAGNITUDE_FLOOR)), dtype=np.float64)
    # Linkwitz-Riley is a squared Butterworth.
    return 2 * db if band.type in _LINKWITZ_RILEY else db


def band_response_db(band: Band, freqs_hz: FloatArray, fs_hz: float) -> FloatArray:
    """Approximate magnitude response of one EQ band.

    Args:
        band: The band to model.
        freqs_hz: Frequencies in Hz, ``float64[n_freqs]``.
        fs_hz: Sample rate in Hz (affects the biquad types near Nyquist).

    Returns:
        Magnitude in dB, ``float64[n_freqs]``.

    Raises:
        ValueError: ``fs_hz`` is not a positive finite number.
    """
    _check_fs(fs_hz)
    freqs = np.asarray(freqs_hz, dtype=np.float64)
    if band.type in _PARAMETRIC and abs(band.gain_db) < FLAT_GAIN_DB:
        return np.zeros_like(freqs)
    row = _biquad(band, fs_hz)
    if row is not None:
        return _biquad_db(row, freqs, fs_hz)
    return _analog_slope_db(band, freqs)


def eq_response_db(
    eq: SystemEq, freqs_hz: FloatArray, fs_hz: float, *, only_adjustable: bool = False
) -> FloatArray:
    """Approximate magnitude response of a whole EQ block.

    Args:
        eq: The EQ block; a block that is switched off is flat.
        freqs_hz: Frequencies in Hz, ``float64[n_freqs]``.
        fs_hz: Sample rate in Hz.
        only_adjustable: Leave cut filters out of the sum.

    Returns:
        Magnitude in dB, ``float64[n_freqs]``.

    Raises:
        ValueError: ``fs_hz`` is not a positive finite number.
    """
    _check_fs(fs_hz)
    freqs = np.asarray(freqs_hz, dtype=np.float64)
    total = np.zeros_like(freqs)
    if not eq.on:
        return total
    for band in eq.bands:
        if only_adjustable and not band.adjustable:
            continue
        total = total + band_response_db(band, freqs, fs_hz)
    return total


def band_sos(band: Band, fs_hz: float) -> FloatArray | None:
    """Second-order sections of one band, for applying the EQ to audio in the simulator.

    Args:
        band: The band to realise.
        fs_hz: Sample rate in Hz.

    Returns:
        SOS array ``float64[n_sections, 6]``, or None when the band is flat (a parametric band
        or a high shelf with zero gain) and can be skipped.

    Raises:
        ValueError: ``fs_hz`` is not a positive finite number.
    """
    _check_fs(fs_hz)
    flat = abs(band.gain_db) < FLAT_GAIN_DB
    if flat and (band.type in _PARAMETRIC or band.type is EqType.HSHV):
        return None
    row = _biquad(band, fs_hz)
    if row is not None:
        return row.reshape(1, 6)
    order = _SLOPE_ORDER[band.type]
    if band.type in _BESSEL:
        sos = signal.bessel(
            order, band.freq_hz, btype=_slope_btype(band), fs=fs_hz, norm="mag", output="sos"
        )
    else:
        sos = signal.butter(order, band.freq_hz, btype=_slope_btype(band), fs=fs_hz, output="sos")
    sections = np.asarray(sos, dtype=np.float64)
    return np.vstack([sections, sections]) if band.type in _LINKWITZ_RILEY else sections
