"""The verify verdict on synthetic curves (SPEC §8.6): one test per threshold."""

import dataclasses

import numpy as np
import pytest

from churchtune.analysis.points import Analysis
from churchtune.analysis.response import GRID_HZ, energy_average
from churchtune.analysis.verdict import (
    Verdict,
    VerifyThresholds,
    common_points,
    judge,
    verify,
    worst_point,
)

FLAT = np.zeros_like(GRID_HZ)


def bump(freq_hz: float, gain_db: float, width_oct: float = 0.5) -> np.ndarray:
    return gain_db * np.exp(-0.5 * (np.log2(GRID_HZ / freq_hz) / width_oct) ** 2)


def analysis(curves: dict[str, np.ndarray], f_low_hz: float = 50.0) -> Analysis:
    stack = np.array(list(curves.values()))
    return Analysis(
        grid_hz=GRID_HZ,
        raw_db=curves,
        norm_db=curves,
        avg_db=energy_average(stack),
        spread_db=np.std(stack, axis=0),
        min_db=stack.min(axis=0),
        max_db=stack.max(axis=0),
        rt60_points={},
        rt60={},
        f_low_hz=f_low_hz,
    )


ROOM = {
    "p1": bump(500, 5.0) + bump(2000, 1.0),
    "p2": bump(500, 4.0) - bump(3000, 1.0),
    "p3": bump(500, 6.0),
    "p4": bump(500, 4.5) + bump(200, 1.0),
}


def corrected(cut_db: float) -> dict[str, np.ndarray]:
    return {p: c - bump(500, cut_db) for p, c in ROOM.items()}


def test_a_cut_on_the_hump_is_better():
    result = verify(analysis(ROOM), analysis(corrected(4.5)), FLAT)
    assert result.verdict is Verdict.BETTER
    assert result.m_after_db < result.m_before_db - 0.5
    assert result.worst_point == "p3"
    assert result.points == ("p1", "p2", "p3", "p4")


def test_the_same_response_is_no_change():
    result = verify(analysis(ROOM), analysis(ROOM), FLAT)
    assert result.verdict is Verdict.NO_CHANGE
    assert result.band_delta_db == pytest.approx(0.0, abs=1e-9)


def test_a_new_hole_is_worse():
    after = {p: c - bump(1000, 6.0, 0.3) for p, c in ROOM.items()}
    result = verify(analysis(ROOM), analysis(after), FLAT)
    assert result.verdict is Verdict.WORSE
    assert result.band_hz == pytest.approx(1000, rel=0.15)


@pytest.mark.parametrize(("harm_db", "verdict"), [(4.0, Verdict.NO_CHANGE), (6.0, Verdict.WORSE)])
def test_a_local_harm_in_one_seat_is_caught_by_the_band_criterion(harm_db, verdict):
    after = corrected(4.5)
    after["p2"] = after["p2"] + bump(4000, harm_db, 0.2)
    result = verify(analysis(ROOM), analysis(after), FLAT)
    assert result.m_after_db < result.m_before_db - 0.5  # the average alone says "better"
    assert result.band_point == "p2"
    assert result.band_hz == pytest.approx(4000, rel=0.15)
    assert result.verdict is verdict


def test_only_common_points_are_compared():
    after = {k: v for k, v in corrected(4.5).items() if k != "p4"}
    before = analysis({k: v for k, v in ROOM.items() if k != "p4"})
    assert common_points(analysis(ROOM), analysis(after)) == ("p1", "p2", "p3")
    assert verify(before, analysis(after), FLAT).points == ("p1", "p2", "p3")


def test_no_common_point_or_another_grid_is_refused():
    with pytest.raises(ValueError, match="shares no point"):
        verify(analysis(ROOM), analysis({"x": FLAT}), FLAT)
    shifted = dataclasses.replace(analysis(ROOM), grid_hz=GRID_HZ * 1.01)
    with pytest.raises(ValueError, match="same grid"):
        verify(analysis(ROOM), shifted, FLAT)
    with pytest.raises(ValueError, match="no point"):
        worst_point(analysis(ROOM), FLAT, [])


@pytest.mark.parametrize(
    ("m", "w", "b", "s", "verdict"),
    [
        (-0.5, 0.5, 1.5, 0.5, Verdict.BETTER),  # every "better" bound inclusive
        (-0.49, 0.0, 0.0, 0.0, Verdict.NO_CHANGE),
        (-1.0, 0.51, 0.0, 0.0, Verdict.NO_CHANGE),
        (-1.0, 0.0, 1.51, 0.0, Verdict.NO_CHANGE),
        (-1.0, 0.0, 0.0, 0.51, Verdict.NO_CHANGE),
        (0.5, 0.0, 0.0, 0.0, Verdict.WORSE),
        (-2.0, 1.01, 0.0, 0.0, Verdict.WORSE),
        (-2.0, 0.0, 3.01, 0.0, Verdict.WORSE),
        (-2.0, 0.0, 0.0, 1.01, Verdict.WORSE),
        (0.49, 1.0, 3.0, 1.0, Verdict.NO_CHANGE),  # every "worse" bound exclusive or below
    ],
)
def test_the_verdict_table(m, w, b, s, verdict):
    assert judge(m, w, b, s) is verdict


def test_thresholds_are_configurable():
    strict = VerifyThresholds(m_better_db=2.0)
    assert judge(-1.0, 0.0, 0.0, 0.0, strict) is Verdict.NO_CHANGE
