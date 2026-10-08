"""Target curve and the thresholds of the system-EQ design."""

import math
from dataclasses import dataclass

import numpy as np

from churchtune.domain.arrays import FloatArray

MIN_BANDWIDTH_OCT = 0.05


@dataclass(frozen=True, slots=True)
class EqDesignConfig:
    """Thresholds of the system-EQ design (SPEC §8.5); defaults proven in the reference hall.

    Attributes:
        peak_min_db: Averaged excess over the target needed to consider a cut.
        peak_point_db: Excess a single point needs to count as showing the peak.
        peak_common: Share of points that must show a peak.
        dip_min_db: Averaged shortfall needed to consider a boost.
        dip_point_db: Shortfall a single point needs to count as showing the dip.
        dip_common: Share of points that must show a dip.
        dip_min_oct: Only dips at least this wide may be boosted.
        dip_max_spread_db: ... and only when the points agree this well.
        max_boost_db: Highest boost.
        max_cut_db: Deepest cut.
        q_range: Q range of the greedy pass.
        approx_above_hz: Above this an uncalibrated mic is unreliable; results are flagged.
        merge_oct: An existing band this close to a feature is reused for it.
        reuse_oct: An existing cut this close to a new peak is moved instead of using a band.
        correction: Share of a deviation that is corrected (never flatten 100 %).
        min_effect_db: Bands with a smaller computed gain are dropped.
        active_gain_db: A band with at least this much gain counts as in use.
        min_low_hz: Nothing is corrected below this, whatever the roll-off.
        max_hz: Nothing is corrected above this.
        notch_q: Existing cuts at least this narrow are treated as feedback notches.
        low_weight_below_hz: Below this, modes are position dependent and weigh less.
        low_weight: That weight.
        mode_below_hz: Cuts below this are treated as room modes.
        mode_max_cut_db: Their depth is a compromise and is capped here.
    """

    peak_min_db: float = 2.5
    peak_point_db: float = 2.0
    peak_common: float = 2 / 3
    dip_min_db: float = 3.0
    dip_point_db: float = 2.0
    dip_common: float = 0.8
    dip_min_oct: float = 1.0
    dip_max_spread_db: float = 3.0
    max_boost_db: float = 3.0
    max_cut_db: float = -9.0
    q_range: tuple[float, float] = (0.6, 6.0)
    approx_above_hz: float = 10000.0
    merge_oct: float = 1 / 3
    reuse_oct: float = 0.5
    correction: float = 0.85
    min_effect_db: float = 0.75
    active_gain_db: float = 0.25
    min_low_hz: float = 35.0
    max_hz: float = 16000.0
    notch_q: float = 5.0
    low_weight_below_hz: float = 70.0
    low_weight: float = 0.6
    mode_below_hz: float = 120.0
    mode_max_cut_db: float = -6.0


def target_curve(
    grid_hz: FloatArray,
    tilt_db_per_oct: float,
    tilt_from_hz: float,
    bass_lift_db: float,
    bass_from_hz: float,
) -> FloatArray:
    """Target response on a frequency grid.

    Flat up to ``tilt_from_hz``, then ``tilt_db_per_oct``; plus a smooth low shelf of
    ``bass_lift_db`` (half of it at ``bass_from_hz``, about all of it an octave below).

    Returns:
        Target in dB, ``float64[n_freqs]``.
    """
    target = np.zeros_like(grid_hz, dtype=np.float64)
    above = grid_hz > tilt_from_hz
    target[above] = tilt_db_per_oct * np.log2(grid_hz[above] / tilt_from_hz)
    if bass_lift_db:
        target = target + bass_lift_db / (1.0 + (grid_hz / bass_from_hz) ** 4)
    return target


def bandwidth_to_q(bandwidth_oct: float) -> float:
    """Q of a peaking filter whose half-gain width is ``bandwidth_oct`` octaves."""
    ratio = 2 ** max(bandwidth_oct, MIN_BANDWIDTH_OCT)
    return math.sqrt(ratio) / (ratio - 1)
