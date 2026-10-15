"""The optimiser's commonness rule judges a band where it acts, not over a fixed wide zone."""

import itertools
from dataclasses import replace

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
    assert len(near) == 1
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


def resonance_hz(seed: int) -> float:
    return float(np.random.default_rng(900 + seed).uniform(150, 2000))


@pytest.mark.parametrize("seed", [0, 1, 2, 4, 5, 7, 9, 11, 12, 19])  # 1-19: flagged in review
def test_one_resonance_gets_one_cut_at_its_centre(seed):
    """A second cut on one resonance must buy more than a band costs (DSP review)."""
    proposal = optimize_design(analyze_points(random_hall(seed)), FLAT, X32EqGrid(), FS)
    f0 = resonance_hz(seed)
    near = [b for b in proposed_cuts(proposal) if abs(np.log2(b.freq_hz / f0)) < 1 / 3]
    assert len(near) == 1, near
    assert abs(np.log2(near[0].freq_hz / f0)) < 1 / 12
    assert_no_stacked_cuts(proposal)


@pytest.mark.parametrize(("seed", "level"), [(4, 1e-7), (6, 1e-4), (8, 1e-7), (8, 1e-4)])
def test_noise_does_not_change_the_cuts(seed, level):
    analysis = analyze_points(random_hall(seed))
    reference = proposed_cuts(optimize_design(analysis, FLAT, X32EqGrid(), FS))
    for k in range(2):
        rng = np.random.default_rng(k)
        norm = {p: c + rng.normal(0.0, level, c.shape) for p, c in analysis.norm_db.items()}
        noisy = optimize_design(replace(analysis, norm_db=norm), FLAT, X32EqGrid(), FS)
        assert proposed_cuts(noisy) == reference, k


def test_the_optimisation_is_deterministic():
    analysis = analyze_points(hall(8))
    first = optimize_design(analysis, FLAT, X32EqGrid(), FS)
    assert optimize_design(analysis, FLAT, X32EqGrid(), FS) == first


def score(rms=1.0, gain=10.0, stacked=0, crowded=0):
    return system_eq_opt._Score(weighted_rms_db=rms, gain_db=gain, stacked=stacked, crowded=crowded)


def test_a_second_cut_on_one_problem_must_buy_more_than_a_band_costs():
    one = score(rms=1.0)
    cost = system_eq_opt.BAND_COST_DB
    assert score(rms=1.0 - cost / 2, crowded=1).total_db > one.total_db
    assert score(rms=1.0 - 2 * cost, crowded=1).total_db < one.total_db


def test_a_stacked_pair_costs_like_a_band():
    assert score(stacked=1).total_db == pytest.approx(score(crowded=1).total_db)


def test_gain_is_only_a_tie_break():
    assert score(gain=10.0).total_db < score(gain=11.0).total_db
    assert score(rms=0.99, gain=15.0).total_db < score(rms=1.0, gain=10.0).total_db


@pytest.mark.parametrize(
    ("cuts", "crowded"),
    [
        ([(400.0, 6.0), (1000.0, 6.0)], 0),
        ([(400.0, 6.0), (430.0, 6.0)], 1),  # 0.10 octave: closer than 1/6
        ([(400.0, 6.0), (492.0, 6.0)], 0),  # 0.30 octave, both narrow: two resonances
        ([(400.0, 1.0), (492.0, 6.0)], 1),  # 0.30 octave, one wide: within its half width
        ([(400.0, 6.0), (430.0, 6.0), (462.0, 6.0)], 2),
        ([(400.0, 6.0)], 0),
    ],
)
def test_crowded_cuts(cuts, crowded):
    bands = [Band(n + 1, EqType.PEQ, f, -3.0, q) for n, (f, q) in enumerate(cuts)]
    assert system_eq_opt._crowded(bands) == crowded


def multi_hall(features) -> dict[str, PointResponse]:
    """Six seats sharing the given peaking features (frequency, gain, Q)."""
    sos = [band_sos(Band(2, EqType.PEQ, f, g, q), FS) for f, g, q in features]
    points = {}
    for i in range(6):
        room = synthetic_room(np.random.default_rng(700 + i), FS, length_s=1.0, drr_db=14.0)
        for section in sos:
            if section is not None:
                room = signal.sosfilt(section, room)
        points[f"p{i + 1}"] = PointResponse(np.asarray(room), FS)
    return points


def test_two_narrow_resonances_in_a_rough_hall_get_two_cuts():
    """Two resonances 0.42 octave apart are two problems, not one wide cut between them."""
    dips = [(f, -12.0, 8.0) for f in (180.0, 700.0, 1300.0, 2600.0, 3300.0, 5200.0, 7000.0)]
    points = multi_hall([(300.0, 4.0, 6.0), (400.0, 4.0, 6.0), *dips])
    proposal = optimize_design(analyze_points(points), FLAT, X32EqGrid(), FS)
    cuts = proposed_cuts(proposal)
    for f0 in (300.0, 400.0):
        assert any(abs(np.log2(b.freq_hz / f0)) < 1 / 12 for b in cuts), (f0, cuts)
    assert not any(300.0 < b.freq_hz < 400.0 and b.q < 3.0 for b in cuts), cuts


@pytest.mark.parametrize(
    ("bands", "stacked"),
    [
        ([(400.0, -4.0, 4.0), (1000.0, -4.0, 4.0)], 0),
        ([(400.0, -4.0, 4.0), (430.0, -4.0, 4.0)], 1),  # 0.10 octave apart
        ([(400.0, -3.0, 6.0), (445.0, -3.0, 6.0)], 0),  # 0.15 octave: on the console grid
        ([(400.0, -8.0, 2.0), (520.0, -8.0, 2.0)], 1),  # far enough, but -9 dB is exceeded
        ([(400.0, 3.0, 1.0), (420.0, 3.0, 1.0)], 0),  # boosts are not cuts
        ([], 0),
    ],
)
def test_stacked_cuts(bands, stacked):
    peq = [Band(n + 1, EqType.PEQ, f, g, q) for n, (f, g, q) in enumerate(bands)]
    grid = np.geomspace(20.0, 20000.0, 2000)
    assert system_eq_opt._stacked(peq, grid, FS, -9.0) == stacked


def cut_problem() -> system_eq_opt._Problem:
    slots = (1, 2, 3)
    kinds = dict.fromkeys(slots, system_eq_opt._Kind.CUT)
    lower = (np.log(20.0), -9.0, np.log(0.5)) * 3
    upper = (np.log(20000.0), 0.0, np.log(8.0)) * 3
    return system_eq_opt._Problem(slots, kinds, lower, lower, upper)


def x_of(*bands):
    return np.array([v for f, g, q in bands for v in (np.log(f), g, np.log(q))])


def test_an_idle_band_is_spare():
    x = x_of((50.0, -5.0, 6.0), (500.0, -3.0, 2.0), (900.0, -0.1, 2.0))
    assert system_eq_opt._spare_slot(x, cut_problem(), lambda x, i: i != 2) == 2
    assert system_eq_opt._spare_slot(x, cut_problem(), lambda x, i: True) is None


def held(bounds, slot):
    lower, upper = bounds
    return upper[3 * slot + 1] == 0.0 and lower[3 * slot + 1] > -1e-5


def test_one_band_fewer_holds_the_removed_and_the_idle_cuts_flat():
    """Holding only the removed band would let the solver move the cut into the idle one."""
    x = x_of((300.0, -5.0, 6.0), (1500.0, -3.0, 2.0), (900.0, -0.1, 2.0))
    starts = system_eq_opt._fewer_band_starts(x, cut_problem(), 120.0)
    assert len(starts) == 2  # no pair on one problem: each cut taken out, the shallowest first
    (first, first_bounds), (_, second_bounds) = starts
    assert [held(first_bounds, slot) for slot in range(3)] == [False, True, True]
    assert [held(second_bounds, slot) for slot in range(3)] == [True, False, True]
    assert first[4] > -1e-5


def test_two_close_cuts_are_merged_between_them():
    x = x_of((500.0, -3.0, 6.0), (530.0, -1.0, 6.0), (2000.0, -2.0, 4.0))
    merged, bounds = system_eq_opt._fewer_band_starts(x, cut_problem(), 120.0)[0]
    assert merged[1] == pytest.approx(-4.0)  # both gains
    assert 500.0 < np.exp(merged[0]) < 515.0  # centred by gain, nearer the deeper cut
    assert np.exp(merged[2]) < 6.0  # widened
    assert held(bounds, 1)


def test_two_cuts_on_one_mode_are_merged_and_two_modes_are_not():
    one_mode = x_of((50.0, -5.0, 6.0), (60.0, -3.0, 6.0), (500.0, -3.0, 2.0))
    merged, _ = system_eq_opt._fewer_band_starts(one_mode, cut_problem(), 120.0)[0]
    assert merged[1] == pytest.approx(-8.0)
    two_modes = x_of((60.0, -6.0, 2.0), (115.0, -6.0, 5.0), (500.0, -3.0, 2.0))
    starts = system_eq_opt._fewer_band_starts(two_modes, cut_problem(), 120.0)
    assert len(starts) == 3  # only the three take-outs


@pytest.mark.parametrize("points", [lambda: hall(3), lambda: random_hall(1)])
def test_a_flat_band_that_stays_flat_is_left_as_it_was(points):
    """Moving the frequency or Q of a band with no gain would only be a write to the console."""
    proposal = optimize_design(analyze_points(points()), FLAT, X32EqGrid(), FS)
    for band in proposal.bands:
        if band.band.gain_db == 0 and band.was.gain_db == 0:
            assert band.band == band.was
            assert band.action.value == "keep"
