"""The verify verdict on synthetic curves (SPEC §8.6): one test per threshold."""

import dataclasses

import numpy as np
import pytest

from churchtune.analysis.points import Analysis
from churchtune.analysis.response import GRID_HZ, band_mean_db, energy_average
from churchtune.analysis.verdict import (
    Verdict,
    VerifyThresholds,
    common_points,
    judge,
    verify,
    worst_point,
)
from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.domain.eq_model import eq_response_db

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


# ------------------------------------------------------------ as the analysis really normalises
def normalised(curves: dict[str, np.ndarray]) -> Analysis:
    return analysis({p: c - band_mean_db(c, 500.0, 2000.0) for p, c in curves.items()})


HALL_CUT = SystemEq(
    True, tuple(Band(n, EqType.PEQ, 1000.0, 0.0, 1.4) for n in range(1, 7))
).with_band(Band(2, EqType.PEQ, 479.8, -9.0, 1.5))


def test_a_correction_inside_the_normalisation_band_is_better_not_worse():
    cut = eq_response_db(HALL_CUT, GRID_HZ, 48000.0)
    room = {f"p{i}": -cut + bump(2500, 0.5 * i, 0.4) for i in range(1, 5)}
    room["p2"] = room["p2"] + bump(100, 4.0, 0.3)  # a mode the EQ does not touch
    after = {p: c + cut for p, c in room.items()}
    result = verify(normalised(room), normalised(after), FLAT)
    assert result.verdict is Verdict.BETTER
    assert result.band_delta_db < 0.5


def test_a_narrow_new_notch_counts_about_the_same_wherever_it_falls():
    """Band values instead of samples at the centres: a notch between two centres is split over
    two bands but still counts; sampled at the centres it would almost vanish (5.2 vs 2.2 dB).
    """
    flat_eq = SystemEq(True, tuple(Band(n, EqType.PEQ, 1000.0, 0.0, 1.4) for n in range(1, 7)))
    found = []
    for notch_hz in (2000.0, 2245.0, 2520.0):
        notch = flat_eq.with_band(Band(2, EqType.PEQ, notch_hz, -6.0, 6.0))
        harm = eq_response_db(notch, GRID_HZ, 48000.0)
        after = {p: c + harm for p, c in ROOM.items()}
        found.append(verify(analysis(ROOM), analysis(after), FLAT).band_delta_db)
    assert min(found) > 2.0
    assert max(found) / min(found) < 1.7


def test_repeat_noise_alone_is_rarely_worse():
    rng = np.random.default_rng(5)
    worse = 0
    for _ in range(100):
        before = {p: c + rng.normal(0, 0.7, size=GRID_HZ.size) for p, c in ROOM.items()}
        after = {p: c + rng.normal(0, 0.7, size=GRID_HZ.size) for p, c in ROOM.items()}
        worse += verify(analysis(before), analysis(after), FLAT).verdict is Verdict.WORSE
    assert worse <= 1


def test_the_averages_are_made_from_the_common_points_only():
    full = analysis({**ROOM, "p9": bump(300, 12.0)})
    after = analysis(corrected(4.5))
    assert verify(full, after, FLAT).m_before_db == pytest.approx(
        verify(analysis(ROOM), after, FLAT).m_before_db
    )
