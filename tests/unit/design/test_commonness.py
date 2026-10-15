"""The optimiser's commonness rule judges a band where it acts, not over a fixed wide zone."""

import itertools

import numpy as np
import pytest
from scipy import signal

from churchtune.analysis.points import PointResponse, analyze_points
from churchtune.audio.fake.room import synthetic_room
from churchtune.design import system_eq_opt
from churchtune.design.system_eq_opt import band_half_oct, optimize_design
from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.domain.eq_model import band_sos
from churchtune.mixers.x32.params import X32EqGrid

FS = 48000.0
FLAT = SystemEq(True, tuple(Band(n, EqType.PEQ, 1000.0, 0.0, 1.0) for n in range(1, 7)))
RESONANCE = Band(2, EqType.PEQ, 400.0, 9.0, 6.0)


def hall(with_resonance: int) -> dict[str, PointResponse]:
    sos = band_sos(RESONANCE, FS)
    points = {}
    for i in range(8):
        room = synthetic_room(np.random.default_rng(300 + i), FS, length_s=1.0, drr_db=14.0)
        if i < with_resonance and sos is not None:
            room = signal.sosfilt(sos, room)
        points[f"p{i + 1}"] = PointResponse(np.asarray(room), FS)
    return points


def cut_near(proposal, freq_hz: float) -> bool:
    return any(
        b.band.gain_db < -0.5 and abs(np.log2(b.band.freq_hz / freq_hz)) < 1 / 3
        for b in proposal.bands
    )


def test_a_narrow_resonance_in_every_seat_is_cut():
    proposal = optimize_design(analyze_points(hall(8)), FLAT, X32EqGrid(), FS)
    assert cut_near(proposal, 400.0)


def test_the_same_resonance_in_three_seats_of_eight_is_left_alone():
    proposal = optimize_design(analyze_points(hall(3)), FLAT, X32EqGrid(), FS)
    assert not cut_near(proposal, 400.0)


@pytest.mark.parametrize(("q", "half"), [(0.5, 1 / 3), (1.5, 1 / 3), (6.0, 1 / 12), (3.0, 1 / 6)])
def test_the_zone_follows_the_band_width(q, half):
    assert band_half_oct(q) == pytest.approx(half)


def proposed_cuts(proposal) -> list[Band]:
    return sorted((b.band for b in proposal.bands if b.band.gain_db < 0), key=lambda b: b.freq_hz)


def assert_no_stacked_cuts(proposal) -> None:
    cuts = proposed_cuts(proposal)
    for low, high in itertools.pairwise(cuts):
        assert np.log2(high.freq_hz / low.freq_hz) >= system_eq_opt.STACKED_OCT, (low, high)


def test_a_single_resonance_gets_a_single_cut():
    """One cut, not the same cut spread over several bands (the solver did that before)."""
    proposal = optimize_design(analyze_points(hall(8)), FLAT, X32EqGrid(), FS)
    near = [b for b in proposed_cuts(proposal) if abs(np.log2(b.freq_hz / 400.0)) < 1 / 3]
    deep = [b for b in near if b.gain_db <= -3.0]
    assert len(deep) == 1
    assert sum(b.gain_db for b in near) >= -9.0
    assert_no_stacked_cuts(proposal)


def random_hall(seed: int) -> dict[str, PointResponse]:
    rng = np.random.default_rng(900 + seed)
    resonance = Band(2, EqType.PEQ, float(rng.uniform(150, 2000)), float(rng.uniform(4, 9)), 4.0)
    sos = band_sos(resonance, FS)
    points = {}
    for i in range(6):
        room = synthetic_room(
            np.random.default_rng(500 + 10 * seed + i), FS, length_s=1.0, drr_db=14.0
        )
        points[f"p{i + 1}"] = PointResponse(np.asarray(signal.sosfilt(sos, room)), FS)
    return points


@pytest.mark.parametrize("seed", range(6))
def test_spare_bands_never_make_the_proposal_worse_or_stacked(seed, monkeypatch):
    analysis = analyze_points(random_hall(seed))
    with_reseed = optimize_design(analysis, FLAT, X32EqGrid(), FS)
    assert_no_stacked_cuts(with_reseed)
    monkeypatch.setattr(system_eq_opt, "RESEED_ROUNDS", 0)
    plain = optimize_design(analysis, FLAT, X32EqGrid(), FS)
    assert with_reseed.prediction.rms_dev_after_db <= plain.prediction.rms_dev_after_db + 0.01
    assert with_reseed.prediction.max_dev_after_db <= plain.prediction.max_dev_after_db + 0.01


def test_the_optimisation_is_deterministic():
    analysis = analyze_points(hall(8))
    first = optimize_design(analysis, FLAT, X32EqGrid(), FS)
    assert optimize_design(analysis, FLAT, X32EqGrid(), FS) == first


def score(value=10.0, rms=1.0, peak=2.0, stacked=0):
    return system_eq_opt._Score(value=value, rms_db=rms, max_db=peak, stacked=stacked)


@pytest.mark.parametrize(
    ("candidate", "better"),
    [
        (score(value=9.0), True),
        (score(value=10.0 - system_eq_opt.BETTER_SCORE), False),  # not clearly better
        (score(value=9.0, rms=1.01), True),  # within NO_WORSE_DB of the plain solve
        (score(value=9.0, rms=1.02), False),
        (score(value=9.0, peak=2.02), False),
        (score(value=9.0, stacked=1), False),  # adds a stacked cut
    ],
)
def test_a_reseeded_solution_is_kept_only_when_better_and_no_worse(candidate, better):
    plain = score()
    assert candidate.better_than(plain, plain) is better


def test_a_candidate_may_keep_what_the_plain_solve_stacked():
    plain = score(stacked=1)
    assert score(value=9.0, stacked=1).better_than(plain, plain)


@pytest.mark.parametrize(("value", "better"), [(10.15, True), (10.25, False)])
def test_fewer_stacked_cuts_may_score_slightly_worse(value, better):
    plain = score(stacked=1)
    assert score(value=value, stacked=0).better_than(plain, plain) is better


@pytest.mark.parametrize(
    ("bands", "stacked"),
    [
        ([(400.0, -4.0, 4.0), (1000.0, -4.0, 4.0)], 0),
        ([(400.0, -4.0, 4.0), (430.0, -4.0, 4.0)], 1),  # 0.10 octave apart
        ([(400.0, -3.0, 6.0), (445.0, -3.0, 6.0)], 0),  # 0.15 octave: one grid step from 1/6
        ([(400.0, -8.0, 2.0), (520.0, -8.0, 2.0)], 1),  # far enough, but -9 dB is exceeded
        ([(400.0, 3.0, 1.0), (420.0, 3.0, 1.0)], 0),  # boosts are not cuts
        ([], 0),
    ],
)
def test_stacked_cuts(bands, stacked):
    peq = [Band(n + 1, EqType.PEQ, f, g, q) for n, (f, g, q) in enumerate(bands)]
    grid = np.geomspace(20.0, 20000.0, 2000)
    assert system_eq_opt._stacked(peq, grid, FS, -9.0) == stacked


def mode_problem() -> system_eq_opt._Problem:
    slots = (1, 2, 3)
    kinds = dict.fromkeys(slots, system_eq_opt._Kind.CUT)
    lower = (np.log(20.0), -9.0, np.log(0.5)) * 3
    upper = (np.log(20000.0), 0.0, np.log(8.0)) * 3
    return system_eq_opt._Problem(slots, kinds, lower, lower, upper)


def x_of(*bands):
    return np.array([v for f, g, q in bands for v in (np.log(f), g, np.log(q))])


def test_an_idle_band_is_spare():
    x = x_of((50.0, -5.0, 6.0), (500.0, -3.0, 2.0), (900.0, -0.1, 2.0))
    spare = system_eq_opt._spare_slot(x, mode_problem(), lambda x, i: i != 2, 120.0)
    assert spare is not None
    assert spare[0] == 2
    assert np.array_equal(spare[1], x)
    assert spare[2] is False


def test_two_cuts_on_one_mode_become_one():
    x = x_of((50.0, -5.0, 6.0), (54.0, -3.0, 6.0), (500.0, -3.0, 2.0))
    spare = system_eq_opt._spare_slot(x, mode_problem(), lambda x, i: True, 120.0)
    assert spare is not None
    slot, merged, was_merged = spare
    assert slot == 1
    assert was_merged
    assert merged[1] == pytest.approx(-8.0)  # the deeper cut takes both gains
    assert merged[4] > -0.2  # the spare band is (almost) flat


def test_two_close_cuts_above_the_modes_become_one():
    x = x_of((50.0, -5.0, 6.0), (500.0, -3.0, 6.0), (530.0, -2.0, 6.0))
    spare = system_eq_opt._spare_slot(x, mode_problem(), lambda x, i: True, 120.0)
    assert spare is not None
    assert spare[0] == 2
    assert spare[1][4] == pytest.approx(-5.0)


def test_cuts_on_two_modes_stay_apart():
    x = x_of((60.0, -6.0, 2.0), (121.0 * 0.95, -6.0, 5.0), (500.0, -3.0, 2.0))
    assert system_eq_opt._spare_slot(x, mode_problem(), lambda x, i: True, 120.0) is None
