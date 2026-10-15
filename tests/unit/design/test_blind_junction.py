"""The system EQ while the sub / tops junction is not measured (ADR 0013)."""

from dataclasses import replace

import numpy as np
import pytest

from churchtune.design import blind_junction as bj
from churchtune.design import system_eq_opt
from churchtune.design.blind_junction import BlindJunction
from churchtune.design.packages import DesignContext, present_crossover_hz
from churchtune.design.system_eq import DesignInput
from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.domain.eq_model import eq_response_db
from churchtune.mixers.x32.params import X32EqGrid
from churchtune.mixers.x32.topology import infer_topology
from tests.fixtures.topologies import TOPOLOGIES, load

FS = 48000.0
GRID = X32EqGrid()
BLIND = BlindJunction.around(100.0)
FLAT = SystemEq(True, tuple(Band(n, EqType.PEQ, 1000.0, 0.0, 1.0) for n in range(1, 7)))
NONE: frozenset[int] = frozenset()
AUDIO_GRID = np.geomspace(20.0, 20000.0, 1200)


def proposal(*bands: Band, base: SystemEq = FLAT) -> list[Band]:
    """``base`` with the given bands in their places."""
    out = list(base.bands)
    for band in bands:
        out[band.band - 1] = band
    return out


def within(bands: list[Band], base: SystemEq = FLAT, frozen: frozenset[int] = NONE) -> list[Band]:
    return bj.within_zone(bands, base, BLIND, frozen=frozen, quantizer=GRID, fs_hz=FS)


def zone_total_db(bands: list[Band]) -> float:
    zone = np.geomspace(BLIND.low_hz, BLIND.high_hz, 200)
    return float(eq_response_db(SystemEq(True, tuple(bands)), zone, FS).min())


def test_the_zone_is_0_6_to_2_fc():
    assert (BLIND.low_hz, BLIND.high_hz) == (pytest.approx(60.0), pytest.approx(200.0))
    assert BLIND.holds(125.0)
    assert not BLIND.holds(250.0)


def test_a_new_cut_in_the_zone_is_made_broad_and_gentle():
    limited = within(proposal(Band(1, EqType.PEQ, 125.0, -9.0, 4.0)))
    assert limited[0].gain_db == -3.0
    assert limited[0].q <= 1.0


def test_an_existing_correction_in_the_zone_is_left_as_it_was():
    """Blind, a correction someone set at the junction is not ours to move (DSP review)."""
    base = SystemEq(True, tuple(proposal(Band(1, EqType.PEQ, 125.0, -6.0, 2.0))))
    nudged = proposal(Band(1, EqType.PEQ, 125.0, -5.5, 2.0), base=base)
    assert within(nudged, base)[0] == base.bands[0]


def test_a_cut_next_to_the_zone_cuts_no_deeper_than_the_limit_at_its_edge():
    limited = within(proposal(Band(1, EqType.PEQ, 260.0, -9.0, 0.7)))
    assert BLIND.nearest_db(limited[0], FS) >= BLIND.max_cut_db
    assert limited[0].gain_db < 0  # softened, not removed
    far = within(proposal(Band(2, EqType.PEQ, 1000.0, -9.0, 4.0)))
    assert far[1].gain_db == -9.0  # nothing near the zone: untouched


def test_a_boost_in_the_zone_goes_back_and_one_outside_stays():
    assert within(proposal(Band(5, EqType.PEQ, 150.0, 3.0, 1.0)))[4] == FLAT.bands[4]
    assert within(proposal(Band(5, EqType.PEQ, 330.0, 3.0, 1.4)))[4].gain_db == 3.0


def test_two_broad_cuts_do_not_add_up_to_a_deep_one():
    two = proposal(Band(1, EqType.PEQ, 90.0, -3.0, 0.5), Band(2, EqType.PEQ, 180.0, -3.0, 0.5))
    assert zone_total_db(within(two)) >= BLIND.max_cut_db - 0.05


def test_a_frozen_band_is_never_touched():
    deep = proposal(Band(1, EqType.PEQ, 125.0, -9.0, 4.0))
    assert within(deep, frozen=frozenset({0}))[0] == deep[0]


@pytest.mark.parametrize(
    "bands",
    [
        (Band(1, EqType.PEQ, 125.0, -9.0, 6.0),),
        (Band(1, EqType.PEQ, 70.0, -8.0, 0.6), Band(2, EqType.PEQ, 190.0, -7.0, 0.6)),
        (Band(1, EqType.PEQ, 240.0, -9.0, 0.5), Band(3, EqType.PEQ, 45.0, -9.0, 0.8)),
    ],
)
def test_the_limited_proposal_is_on_the_console_grid_and_within_the_zone(bands):
    given = proposal(*bands)
    limited = within(given)
    for band, before in zip(limited, given, strict=True):
        if band != before:  # what the rules set lies on the console grid
            assert GRID.snap_gain_db(band.gain_db) == band.gain_db
            if BLIND.holds(band.freq_hz):
                assert GRID.snap_q(band.q) == band.q
    assert zone_total_db(limited) >= BLIND.max_cut_db - 0.05


class CoarseGrid:
    """A console with large Q steps: the search for a Q below the limit must still end."""

    def snap_freq_hz(self, freq_hz: float) -> float:
        return freq_hz

    def snap_gain_db(self, gain_db: float) -> float:
        return round(gain_db)

    def snap_q(self, q: float) -> float:
        return float(2 ** round(np.log2(q)))  # one octave a step


def test_the_q_search_ends_on_a_coarse_grid():
    assert bj._q_at_most(1.0, CoarseGrid()) == 1.0
    assert bj._q_at_most(0.9, CoarseGrid()) == 0.5


def test_the_zone_residual_is_continuous_at_the_zone_edge():
    def residual(freq_hz: float) -> np.ndarray:
        x = np.array([np.log(freq_hz), -9.0, np.log(0.5)])
        return bj.zone_residuals(x, [0], BLIND, FS)

    below, above = residual(BLIND.low_hz * (1 - 1e-4)), residual(BLIND.low_hz * (1 + 1e-4))
    assert np.max(np.abs(below - above)) < 0.05


def design_input(seats: list[np.ndarray]) -> DesignInput:
    return DesignInput(
        grid_hz=AUDIO_GRID,
        dev_db=np.mean(seats, axis=0),
        point_devs_db=tuple(seats),
        f_correct_from_hz=40.0,
        old_response_db=np.zeros_like(AUDIO_GRID),
    )


def bump(freq_hz: float, gain_db: float) -> np.ndarray:
    return gain_db * np.exp(-((np.log2(AUDIO_GRID / freq_hz) / 0.5) ** 2))


def guarded(bands: list[Band], data: DesignInput, base: SystemEq = FLAT) -> list[Band]:
    mask = (AUDIO_GRID >= 40.0) & (AUDIO_GRID <= 10000.0)
    return bj.seat_guarded(
        bands, base, data, mask=mask, blind=BLIND, frozen=NONE, quantizer=GRID, fs_hz=FS
    )


def worst_seat_db(bands: list[Band], data: DesignInput, base: SystemEq = FLAT) -> float:
    mask = (AUDIO_GRID >= 40.0) & (AUDIO_GRID <= 10000.0)
    corrected = AUDIO_GRID[mask]
    count = int(np.ceil(3 * np.log2(corrected[-1] / corrected[0])))
    centres = corrected[0] * 2 ** ((np.arange(count) + 0.5) / 3)
    change = eq_response_db(SystemEq(True, tuple(bands)), AUDIO_GRID, FS) - eq_response_db(
        base, AUDIO_GRID, FS
    )
    return max(
        float(bj.third_octave_worsening(seat, seat + change, AUDIO_GRID, centres).max())
        for seat in data.point_devs_db
    )


def test_a_cut_that_thins_a_seat_without_the_bump_keeps_it_within_the_guard():
    seats = [bump(400.0, 6.0) for _ in range(4)] + [np.zeros_like(AUDIO_GRID)]
    data = design_input(seats)
    out = guarded(proposal(Band(1, EqType.PEQ, 400.0, -6.0, 1.0)), data)
    assert -6.0 < out[0].gain_db < 0  # softened, still there for the others
    assert worst_seat_db(out, data) <= BLIND.seat_guard_db


def test_a_boost_elsewhere_does_not_stop_the_guard_mending_a_cut():
    """The worst third is the boost's doing; the cut's damage elsewhere is still mended."""
    seats = [bump(400.0, 6.0) for _ in range(4)] + [bump(3000.0, 3.0)]
    data = design_input(seats)
    bands = proposal(Band(1, EqType.PEQ, 400.0, -6.0, 1.0), Band(5, EqType.PEQ, 3000.0, 3.0, 1.0))
    out = guarded(bands, data)
    assert out[4] == bands[4]  # boosts are not softened
    near_cut = (AUDIO_GRID > 250.0) & (AUDIO_GRID < 650.0)
    change = eq_response_db(SystemEq(True, tuple(out)), AUDIO_GRID, FS)
    seat = seats[4]
    worse = np.abs(seat[near_cut] + change[near_cut]).mean() - np.abs(seat[near_cut]).mean()
    assert worse <= BLIND.seat_guard_db


def test_a_moved_correction_comes_back_whole():
    base = SystemEq(True, tuple(proposal(Band(1, EqType.PEQ, 500.0, -4.0, 1.2))))
    seats = [bump(500.0, 1.0) for _ in range(5)]  # measured with the 500 Hz cut on
    moved = proposal(Band(1, EqType.PEQ, 140.0, -3.0, 1.0), base=base)
    assert guarded(moved, design_input(seats), base)[0] == base.bands[0]


def test_cut_bands_are_matched_to_the_slots_of_their_nearest_old_bands():
    base = SystemEq(
        True,
        tuple(
            proposal(Band(1, EqType.PEQ, 500.0, -4.0, 1.2), Band(2, EqType.PEQ, 50.0, -3.0, 1.4))
        ),
    )
    problem = system_eq_opt._Problem(
        slots=(1, 2, 3),
        kinds=dict.fromkeys((1, 2, 3), system_eq_opt._Kind.CUT),
        x0=(),
        lower=(),
        upper=(),
    )
    swapped = proposal(
        Band(1, EqType.PEQ, 140.0, -3.0, 1.0), Band(2, EqType.PEQ, 520.0, -3.0, 2.0), base=base
    )
    matched = system_eq_opt._matched_to_slots(swapped, base, problem)
    assert (matched[0].freq_hz, matched[0].band) == (520.0, 1)
    assert (matched[1].freq_hz, matched[1].band) == (140.0, 2)


def hall_context(snapshot) -> DesignContext:
    """A design context for reading the console only (no measurement is needed)."""
    return DesignContext(
        analysis=None,  # type: ignore[arg-type]  # present_crossover_hz reads the console only
        snapshot=snapshot,
        topology=infer_topology(snapshot),
        quantizer=GRID,
        fs_hz=FS,
    )


def test_the_zone_follows_the_crossover_on_the_console():
    real = load(TOPOLOGIES / "hall-2026-10-02.toml").snapshot()
    # The dump before 2026-10-02: the tops' LCut at 112.5 Hz; the sub has only a deep shelf,
    # which is not a crossover.
    assert present_crossover_hz(hall_context(real)) == pytest.approx(112.5)
    tops_off = {n: replace(m, eq_on=False) for n, m in real.matrices.items()}
    assert present_crossover_hz(hall_context(replace(real, matrices=tops_off))) is None
