"""The verify verdict (SPEC §8.6): better, no noticeable difference, or worse.

Only points measured both before and after are compared. ``D(p, f)`` is a point's deviation
from the target on third-octave centres. The four numbers:

* ``M``: RMS over 60 Hz-10 kHz of the power-averaged response's deviation from the target;
* ``W``: per point, RMS of ``D`` over 80 Hz-8 kHz; the worst point is the one with the largest
  ``W`` before, and ``ΔW_worst`` is its ``W`` after minus before;
* ``ΔB``: the largest growth of ``|D|`` over points and bands in 80 Hz-8 kHz (catches a local
  harm that averages hide);
* ``Δσ``: change of the mean spread between points over 100 Hz-4 kHz.

Below the speakers' roll-off (``f_low_hz`` of the baseline) nothing is corrected and the target
does not apply, so every range starts at ``f_low_hz`` at the lowest. The junction criterion ``J``
of a pure alignment package is not used yet: the wizard writes no alignment (it is advice).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from churchtune.analysis.points import Analysis
from churchtune.analysis.response import interpolate_at, third_octave_centres
from churchtune.domain.arrays import FloatArray


class Verdict(StrEnum):
    """What the check measurement says."""

    BETTER = "better"
    NO_CHANGE = "no_change"
    WORSE = "worse"


@dataclass(frozen=True, slots=True)
class VerifyThresholds:
    """Thresholds of §8.6 (dB).

    Attributes:
        m_better_db: ``M`` must drop at least this much for "better".
        m_worse_db: ``M`` growing this much means "worse".
        worst_ok_db: ``ΔW_worst`` at most this for "better".
        worst_bad_db: ``ΔW_worst`` above this means "worse".
        band_ok_db: ``ΔB`` at most this for "better".
        band_bad_db: ``ΔB`` above this means "worse".
        spread_ok_db: ``Δσ`` at most this for "better".
        spread_bad_db: ``Δσ`` above this means "worse".
    """

    m_better_db: float = 0.5
    m_worse_db: float = 0.5
    worst_ok_db: float = 0.5
    worst_bad_db: float = 1.0
    band_ok_db: float = 1.5
    band_bad_db: float = 3.0
    spread_ok_db: float = 0.5
    spread_bad_db: float = 1.0


DEFAULT_THRESHOLDS = VerifyThresholds()
M_RANGE_HZ = (60.0, 10000.0)
W_RANGE_HZ = (80.0, 8000.0)
SPREAD_RANGE_HZ = (100.0, 4000.0)


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """The numbers of §8.6 and the verdict.

    Attributes:
        points: The points compared.
        m_before_db: ``M`` before.
        m_after_db: ``M`` after.
        worst_point: The baseline's worst point.
        worst_delta_db: ``ΔW_worst``.
        band_delta_db: ``ΔB``.
        band_point: Where ``ΔB`` happened: point.
        band_hz: Where ``ΔB`` happened: third-octave centre.
        spread_delta_db: ``Δσ``.
        verdict: The verdict.
    """

    points: tuple[str, ...]
    m_before_db: float
    m_after_db: float
    worst_point: str
    worst_delta_db: float
    band_delta_db: float
    band_point: str
    band_hz: float
    spread_delta_db: float
    verdict: Verdict


def _centres(low_hz: float, high_hz: float, f_low_hz: float) -> FloatArray:
    return third_octave_centres(max(low_hz, f_low_hz), high_hz)


def _deviation(
    curve_db: FloatArray, target_db: FloatArray, centres: FloatArray, grid_hz: FloatArray
) -> FloatArray:
    return interpolate_at(centres, curve_db - target_db, grid_hz)


def _rms(values: FloatArray) -> float:
    return float(np.sqrt(np.mean(values**2)))


def common_points(before: Analysis, after: Analysis) -> tuple[str, ...]:
    """Points measured both times, in the baseline's order."""
    return tuple(p for p in before.point_names if p in after.norm_db)


def worst_point(analysis: Analysis, target_db: FloatArray, points: Sequence[str]) -> str:
    """The point with the largest ``W`` (RMS deviation over 80 Hz-8 kHz).

    Raises:
        ValueError: No point given.
    """
    if not points:
        raise ValueError("no point to choose from")
    centres = _centres(*W_RANGE_HZ, analysis.f_low_hz)
    return max(
        points,
        key=lambda p: _rms(_deviation(analysis.norm_db[p], target_db, centres, analysis.grid_hz)),
    )


def judge(
    m_delta_db: float,
    worst_delta_db: float,
    band_delta_db: float,
    spread_delta_db: float,
    thresholds: VerifyThresholds = DEFAULT_THRESHOLDS,
) -> Verdict:
    """The verdict table of §8.6."""
    th = thresholds
    if (
        m_delta_db >= th.m_worse_db
        or worst_delta_db > th.worst_bad_db
        or band_delta_db > th.band_bad_db
        or spread_delta_db > th.spread_bad_db
    ):
        return Verdict.WORSE
    if (
        m_delta_db <= -th.m_better_db
        and worst_delta_db <= th.worst_ok_db
        and band_delta_db <= th.band_ok_db
        and spread_delta_db <= th.spread_ok_db
    ):
        return Verdict.BETTER
    return Verdict.NO_CHANGE


def verify(
    before: Analysis,
    after: Analysis,
    target_db: FloatArray,
    thresholds: VerifyThresholds = DEFAULT_THRESHOLDS,
) -> VerifyResult:
    """Compare a check measurement with the baseline on the points both have.

    Both analyses must be made from exactly the common points (the power average and the
    spread of ``after`` and ``before`` then describe the same seats).

    Raises:
        ValueError: The analyses share no point, or are not on the same grid.
    """
    points = common_points(before, after)
    if not points:
        raise ValueError("the check measurement shares no point with the baseline")
    if before.grid_hz.shape != after.grid_hz.shape or not np.allclose(
        before.grid_hz, after.grid_hz
    ):
        raise ValueError("the analyses are not on the same grid")
    f_low, grid = before.f_low_hz, before.grid_hz
    m_centres = _centres(*M_RANGE_HZ, f_low)
    m_before = _rms(_deviation(before.avg_db, target_db, m_centres, grid))
    m_after = _rms(_deviation(after.avg_db, target_db, m_centres, grid))
    w_centres = _centres(*W_RANGE_HZ, f_low)
    worst = worst_point(before, target_db, points)
    w_before = _rms(_deviation(before.norm_db[worst], target_db, w_centres, grid))
    w_after = _rms(_deviation(after.norm_db[worst], target_db, w_centres, grid))
    band_delta, band_point, band_hz = -np.inf, points[0], float(w_centres[0])
    for point in points:
        growth = np.abs(_deviation(after.norm_db[point], target_db, w_centres, grid)) - np.abs(
            _deviation(before.norm_db[point], target_db, w_centres, grid)
        )
        index = int(np.argmax(growth))
        if growth[index] > band_delta:
            band_delta, band_point, band_hz = float(growth[index]), point, float(w_centres[index])
    s_centres = _centres(*SPREAD_RANGE_HZ, f_low)
    spread_before = float(np.mean(interpolate_at(s_centres, before.spread_db, grid)))
    spread_after = float(np.mean(interpolate_at(s_centres, after.spread_db, grid)))
    spread_delta = spread_after - spread_before
    verdict = judge(m_after - m_before, w_after - w_before, band_delta, spread_delta, thresholds)
    return VerifyResult(
        points,
        m_before,
        m_after,
        worst,
        w_after - w_before,
        float(band_delta),
        band_point,
        band_hz,
        spread_delta,
        verdict,
    )
