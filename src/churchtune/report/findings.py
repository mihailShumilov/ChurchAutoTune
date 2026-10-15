"""The most important things a measurement says, in plain words (wizard S11 and the report).

Thresholds follow the room-measurement skill: RT60 averaged over the 500 Hz and 1 kHz octaves,
a hump of 3 dB or more above the target in 80 Hz - 4 kHz where the seats agree, spread between
seats above 3 dB, bass ratio above 1.3; the EDT/T20 bands (0.8, 1.1) are a heuristic.
"""

import numpy as np
from scipy.signal import find_peaks

from churchtune.analysis.points import Analysis
from churchtune.analysis.response import NORM_BAND_HZ
from churchtune.domain.arrays import FloatArray
from churchtune.report.model import Msg

RT60_DRY_S, RT60_GOOD_S, RT60_BOOMY_S = 0.8, 1.4, 2.0
MID_OCTAVES = (500, 1000)
HUMP_MIN_DB = 3.0
HUMP_BAND_HZ = (80.0, 4000.0)
SPREAD_BAND_HZ = (100.0, 4000.0)
LARGE_SPREAD_DB = 3.0
BOXY_FROM_HZ, SHARP_FROM_HZ = 300.0, 2000.0
BASS_OCTAVES = (125, 250, 500, 1000)
# EDT/T20 in the mids: shorter means strong direct sound at the seats, longer mostly reflections.
EDT_SHORT_RATIO, EDT_LONG_RATIO = 0.8, 1.1
BASS_LONG_RATIO = 1.3


def mid_rt60_s(analysis: Analysis) -> float | None:
    """RT60 (T20) averaged over the 500 Hz and 1 kHz octaves, if measured.

    Rounded to the 0.01 s it is shown with, so a verdict always agrees with the number printed.
    """
    mids = [analysis.rt60[o].t20.mean_s for o in MID_OCTAVES if o in analysis.rt60]
    values = [v for v in mids if v is not None]
    return round(float(np.mean(values)), 2) if values else None


def mid_edt_s(analysis: Analysis) -> float | None:
    """The measured early decay time averaged over the 500 Hz and 1 kHz octaves that have one."""
    mids = [analysis.rt60[o].edt.mean_s for o in MID_OCTAVES if o in analysis.rt60]
    values = [v for v in mids if v is not None]
    return round(float(np.mean(values)), 2) if values else None


def rt60_verdict(rt60_s: float) -> str:
    """dry, good, boomy or treatment."""
    if rt60_s < RT60_DRY_S:
        return "dry"
    if rt60_s < RT60_GOOD_S:
        return "good"
    return "boomy" if rt60_s < RT60_BOOMY_S else "treatment"


def mid_spread_db(analysis: Analysis) -> float:
    """Mean spread between the seats in 100 Hz - 4 kHz (0 with a single place)."""
    grid = analysis.grid_hz
    band = (grid >= SPREAD_BAND_HZ[0]) & (grid <= SPREAD_BAND_HZ[1])
    return float(np.nanmean(analysis.spread_db[band])) if band.any() else 0.0


def _hump(analysis: Analysis, target_db: FloatArray | None) -> tuple[float, float] | None:
    """The most prominent hump above the target where the places agree: (Hz, dB), or None.

    The deviation is level-aligned in 500 Hz - 2 kHz first (the curves are normalised there, the
    target need not be). Only a local peak that stands out from its surroundings by at least half
    the hump threshold counts: a target sloping away above 1 kHz turns a gentle shelf into a
    rising deviation, and the edge of the band is not a hump.
    """
    grid = analysis.grid_hz
    deviation = analysis.avg_db - target_db if target_db is not None else analysis.avg_db
    norm = (grid >= NORM_BAND_HZ[0]) & (grid <= NORM_BAND_HZ[1]) & np.isfinite(deviation)
    if not norm.any():
        return None
    deviation = deviation - float(np.mean(deviation[norm]))
    band = (
        (grid >= max(HUMP_BAND_HZ[0], analysis.f_low_hz))
        & (grid <= HUMP_BAND_HZ[1])
        & (analysis.spread_db <= LARGE_SPREAD_DB)
        & np.isfinite(deviation)
    )
    if not band.any():
        return None
    # Outside the band the curve is carried on flat from the nearest valid value, so a rise that
    # runs into an edge is a plateau, not a peak; holes are bridged linearly.
    index = np.arange(grid.size)
    curve = np.interp(index, index[band], deviation[band])
    peaks, _ = find_peaks(curve, prominence=HUMP_MIN_DB / 2)
    # A peak next to a hole may be the shoulder of a top where the places disagree: not reported.
    humps = [int(i) for i in peaks if band[i - 1 : i + 2].all() and curve[i] >= HUMP_MIN_DB]
    if not humps:
        return None
    best = max(humps, key=lambda i: (curve[i], -i))
    return float(grid[best]), round(float(curve[best]), 1)


def _hump_key(freq_hz: float) -> str:
    """Boomy in the low mids, boxy higher up, sharp in the presence range."""
    if freq_hz < BOXY_FROM_HZ:
        return "finding-hump"
    return "finding-hump-boxy" if freq_hz < SHARP_FROM_HZ else "finding-hump-sharp"


def findings(analysis: Analysis, target_db: FloatArray | None = None) -> list[Msg]:
    """The findings, most important first; the RT60 one followed by what RT60 means.

    A hump is measured against the target curve when one is given (a lift the target asks for
    is no problem), and only where the seats agree (a hump in a few seats is not the system's).
    """
    found: list[Msg] = []
    rt60 = mid_rt60_s(analysis)
    if rt60 is not None:
        found.append(Msg(f"finding-rt60-{rt60_verdict(rt60)}", {"rt60_s": rt60}))
        found.append(Msg("glossary-rt60"))
    hump = _hump(analysis, target_db)
    if hump is not None:
        freq_hz, gain_db = hump
        found.insert(0, Msg(_hump_key(freq_hz), {"freq_hz": round(freq_hz), "gain_db": gain_db}))
    spread = mid_spread_db(analysis)
    key = "finding-spread-large" if spread > LARGE_SPREAD_DB else "finding-spread-small"
    found.append(Msg(key, {"spread_db": round(spread, 1)}))
    return found


def edt_ratio(analysis: Analysis) -> float | None:
    """EDT over T20 in the 500 Hz and 1 kHz octaves (direct sound against reverberation).

    Taken point by point (EDT and T20 of the same recording), then averaged; None without a pair.
    """
    pairs = [
        (decay[o].edt_s, decay[o].t20_s)
        for decay in analysis.rt60_points.values()
        for o in MID_OCTAVES
        if o in decay
    ]
    ratios = [edt / t20 for edt, t20 in pairs if edt is not None and t20]
    return float(np.mean(ratios)) if ratios else None


def bass_ratio(analysis: Analysis) -> float | None:
    """(T125 + T250) / (T500 + T1k) from T20: above about 1.3 the bass rings on."""
    values = [analysis.rt60[o].t20.mean_s if o in analysis.rt60 else None for o in BASS_OCTAVES]
    if any(v is None for v in values):
        return None
    t125, t250, t500, t1k = (float(v) for v in values if v is not None)
    return (t125 + t250) / (t500 + t1k) if t500 + t1k > 0 else None
