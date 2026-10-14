"""The verify verdict (SPEC §8.6): better, no noticeable difference, or worse.

Only points measured both before and after are compared. ``D(p, f)`` is a point's deviation
from the target on the third-octave grid, as **band values** (the power mean of the smoothed
curve over each third octave), so a narrow feature counts the same wherever it falls between
centres. The four numbers:

* ``M``: RMS over 60 Hz-10 kHz of the power-averaged response's deviation from the target;
* ``W``: per point, RMS of ``D`` over 80 Hz-8 kHz; the worst point is the one with the largest
  ``W`` before, and ``ΔW_worst`` is its ``W`` after minus before;
* ``ΔB``: the largest growth of ``|D|`` over points and bands in 80 Hz-8 kHz (catches a local
  harm that averages hide);
* ``Δσ``: change of the mean spread between points over 100 Hz-4 kHz.

**Level alignment.** Every point is normalised to 0 dB in 500 Hz-2 kHz (``analysis.points``).
A system EQ band near that range (a cut at 480 Hz) moves the whole normalised "after" curve, so
|D| would grow where nothing changed acoustically. Before comparing, each "after" curve (and the
power average) is shifted by the median over the 80 Hz-8 kHz bands of after minus before: an EQ
touches fewer than half of those bands, so the median follows the level, not the correction.
The deviation from the target is then taken without the overall level (the median band
deviation before, taken off both): a normalised curve sits at an arbitrary level against it.

Below the speakers' roll-off nothing is corrected (the design starts at 1.25 x ``f_low_hz``), so
every range starts there at the lowest. The junction criterion ``J`` of a pure alignment package
is not used yet: the wizard writes no alignment (it is advice).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from churchtune.analysis.points import Analysis
from churchtune.analysis.response import energy_average, third_octave_centres
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
UNCORRECTED_BELOW = 1.25  # x f_low_hz, as in design.system_eq
THIRD_HALF_OCT = 1 / 6


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
    centres = third_octave_centres(max(low_hz, UNCORRECTED_BELOW * f_low_hz), high_hz)
    if centres.size == 0:
        raise ValueError(f"no third octave between {low_hz} and {high_hz} Hz above the roll-off")
    return centres


def band_values(
    curve_db: FloatArray, centres: FloatArray, grid_hz: FloatArray, *, power: bool = True
) -> FloatArray:
    """Mean of a curve over each third octave (nearest grid point for a narrow band).

    A level in dB is averaged as power; a spread in dB (``power=False``) as it is.
    """
    values = np.empty(len(centres))
    for i, centre in enumerate(centres):
        low, high = centre * 2**-THIRD_HALF_OCT, centre * 2**THIRD_HALF_OCT
        inside = (grid_hz >= low) & (grid_hz < high)
        if not inside.any():
            inside = np.abs(np.log(grid_hz / centre)) == np.min(np.abs(np.log(grid_hz / centre)))
        part = curve_db[inside]
        values[i] = 10 * np.log10(np.mean(10 ** (part / 10))) if power else np.mean(part)
    return values


def _rms(values: FloatArray) -> float:
    return float(np.sqrt(np.mean(values**2)))


def common_points(before: Analysis, after: Analysis) -> tuple[str, ...]:
    """Points measured both times, in the baseline's order."""
    return tuple(p for p in before.point_names if p in after.norm_db)


def _deviations(
    analysis: Analysis, target_db: FloatArray, points: Sequence[str], centres: FloatArray
) -> dict[str, FloatArray]:
    return {p: band_values(analysis.norm_db[p] - target_db, centres, analysis.grid_hz)
            for p in points}  # fmt: skip


def worst_point(analysis: Analysis, target_db: FloatArray, points: Sequence[str]) -> str:
    """The point with the largest ``W`` (RMS band deviation over 80 Hz-8 kHz).

    Raises:
        ValueError: No point given.
    """
    if not points:
        raise ValueError("no point to choose from")
    centres = _centres(*W_RANGE_HZ, analysis.f_low_hz)
    deviations = _deviations(analysis, target_db, points, centres)
    return max(points, key=lambda p: _rms(deviations[p]))


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


def level_offset(before_db: FloatArray, after_db: FloatArray, grid_hz: FloatArray, f_low_hz: float
                 ) -> float:  # fmt: skip
    """How much the normalisation moved a curve: median band difference over 80 Hz-8 kHz."""
    centres = _centres(*W_RANGE_HZ, f_low_hz)
    return float(np.median(band_values(after_db - before_db, centres, grid_hz)))


def level_free(
    before_db: FloatArray,
    after_db: FloatArray,
    target_db: FloatArray,
    centres: FloatArray,
    grid_hz: FloatArray,
) -> tuple[FloatArray, FloatArray]:
    """Band deviations from the target without the overall level, before and after.

    The normalised curves sit at an arbitrary level against the target; the median band
    deviation before is that level, and the same amount is taken off the (aligned) "after".
    """
    before = band_values(before_db - target_db, centres, grid_hz)
    after = band_values(after_db - target_db, centres, grid_hz)
    level = float(np.median(before))
    return before - level, after - level


def verify(
    before: Analysis,
    after: Analysis,
    target_db: FloatArray,
    thresholds: VerifyThresholds = DEFAULT_THRESHOLDS,
) -> VerifyResult:
    """Compare a check measurement with the baseline on the points both have.

    The power averages and spreads are computed again from the common points only.

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
    was = {p: before.norm_db[p] for p in points}
    now = {p: after.norm_db[p] - level_offset(was[p], after.norm_db[p], grid, f_low)
           for p in points}  # fmt: skip
    was_stack = np.array([was[p] for p in points])
    now_stack = np.array([now[p] for p in points])

    m_centres = _centres(*M_RANGE_HZ, f_low)
    m_before, m_after = (
        _rms(d)
        for d in level_free(
            energy_average(was_stack), energy_average(now_stack), target_db, m_centres, grid
        )
    )

    w_centres = _centres(*W_RANGE_HZ, f_low)
    pairs = {p: level_free(was[p], now[p], target_db, w_centres, grid) for p in points}
    d_before = {p: pair[0] for p, pair in pairs.items()}
    d_after = {p: pair[1] for p, pair in pairs.items()}
    worst = max(points, key=lambda p: _rms(d_before[p]))
    worst_delta = _rms(d_after[worst]) - _rms(d_before[worst])

    band_delta, band_point, band_hz = -np.inf, points[0], float(w_centres[0])
    for point in points:
        growth = np.abs(d_after[point]) - np.abs(d_before[point])
        index = int(np.argmax(growth))
        if growth[index] > band_delta:
            band_delta, band_point, band_hz = float(growth[index]), point, float(w_centres[index])

    s_centres = _centres(*SPREAD_RANGE_HZ, f_low)
    spread_before = float(np.mean(band_values(was_stack.std(axis=0), s_centres, grid, power=False)))
    spread_after = float(np.mean(band_values(now_stack.std(axis=0), s_centres, grid, power=False)))
    spread_delta = spread_after - spread_before
    verdict = judge(m_after - m_before, worst_delta, band_delta, spread_delta, thresholds)
    return VerifyResult(
        points, m_before, m_after, worst, worst_delta, band_delta, band_point, band_hz,
        spread_delta, verdict,
    )  # fmt: skip
