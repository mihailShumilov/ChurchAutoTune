"""The system EQ while the sub / tops junction is not measured (ADR 0013)."""

import numpy as np
import pytest

from churchtune.design import system_eq_opt
from churchtune.design.packages import DesignContext, present_crossover_hz
from churchtune.design.system_eq import DesignInput
from churchtune.design.system_eq_opt import BlindJunction
from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.domain.eq_model import eq_response_db
from churchtune.mixers.x32.params import X32EqGrid
from churchtune.mixers.x32.topology import infer_topology
from tests.fixtures.topologies import TOPOLOGIES, load
from tests.unit.design.test_packages import flat_snapshot

FS = 48000.0
GRID = X32EqGrid()
BLIND = BlindJunction.around(100.0)
FLAT = SystemEq(True, tuple(Band(n, EqType.PEQ, 1000.0, 0.0, 1.0) for n in range(1, 7)))
CUTS = (1, 2, 3, 4)
PROBLEM = system_eq_opt._Problem(
    slots=(1, 2, 3, 4, 5),
    kinds={**dict.fromkeys(CUTS, system_eq_opt._Kind.CUT), 5: system_eq_opt._Kind.BOOST},
    x0=(),
    lower=(),
    upper=(),
)


def proposal(*bands: Band) -> list[Band]:
    """FLAT with the given bands in their places."""
    out = list(FLAT.bands)
    for band in bands:
        out[band.band - 1] = band
    return out


def within(bands: list[Band]) -> list[Band]:
    return system_eq_opt._within_zone(bands, FLAT, PROBLEM, BLIND, quantizer=GRID, fs_hz=FS)


def test_the_zone_is_0_6_to_2_fc():
    assert (BLIND.low_hz, BLIND.high_hz) == (pytest.approx(60.0), pytest.approx(200.0))
    assert BLIND.holds(125.0)
    assert not BLIND.holds(250.0)


def test_a_cut_in_the_zone_is_made_broad_and_gentle():
    limited = within(proposal(Band(1, EqType.PEQ, 125.0, -9.0, 4.0)))
    assert limited[0].gain_db == -3.0
    assert limited[0].q <= 1.0
    assert GRID.snap_q(limited[0].q) == limited[0].q  # still on the console grid


def test_a_cut_next_to_the_zone_cuts_no_deeper_than_the_limit_at_its_edge():
    limited = within(proposal(Band(1, EqType.PEQ, 260.0, -9.0, 0.7)))
    edge = BLIND.edge_db(limited[0], FS)
    assert edge >= BLIND.max_cut_db
    assert limited[0].gain_db < 0  # softened, not removed
    far = within(proposal(Band(2, EqType.PEQ, 1000.0, -9.0, 4.0)))
    assert far[1].gain_db == -9.0  # nothing near the zone: untouched


def test_a_boost_in_the_zone_goes_back_and_one_outside_stays():
    limited = within(proposal(Band(5, EqType.PEQ, 150.0, 3.0, 1.0)))
    assert limited[4] == FLAT.bands[4]
    kept = within(proposal(Band(5, EqType.PEQ, 330.0, 3.0, 1.4)))
    assert kept[4].gain_db == 3.0


def test_two_broad_cuts_do_not_add_up_to_a_deep_one():
    limited = within(
        proposal(Band(1, EqType.PEQ, 90.0, -3.0, 0.5), Band(2, EqType.PEQ, 180.0, -3.0, 0.5))
    )
    zone = np.geomspace(BLIND.low_hz, BLIND.high_hz, 200)
    total = eq_response_db(SystemEq(True, tuple(limited)), zone, FS)
    assert total.min() >= BLIND.max_cut_db - 0.05


def design_input(seats: list[np.ndarray]) -> DesignInput:
    grid = np.geomspace(20.0, 20000.0, 1200)
    return DesignInput(
        grid_hz=grid,
        dev_db=np.mean(seats, axis=0),
        point_devs_db=tuple(seats),
        f_correct_from_hz=40.0,
        old_response_db=np.zeros_like(grid),
    )


def bump(grid: np.ndarray, freq_hz: float, gain_db: float) -> np.ndarray:
    return gain_db * np.exp(-((np.log2(grid / freq_hz) / 0.5) ** 2))


def guarded(bands: list[Band], data: DesignInput) -> list[Band]:
    mask = (data.grid_hz >= 40.0) & (data.grid_hz <= 10000.0)
    return system_eq_opt._seat_guarded(bands, FLAT, data, mask=mask, fs_hz=FS, blind=BLIND)


def test_a_cut_that_thins_a_seat_without_the_bump_is_softened():
    grid = np.geomspace(20.0, 20000.0, 1200)
    seats = [bump(grid, 400.0, 6.0) for _ in range(4)] + [np.zeros_like(grid)]
    out = guarded(proposal(Band(1, EqType.PEQ, 400.0, -6.0, 1.0)), design_input(seats))
    assert -6.0 < out[0].gain_db < 0  # softened, still there for the others


def test_a_boost_is_not_softened_by_the_guard():
    grid = np.geomspace(20.0, 20000.0, 1200)
    seats = [bump(grid, 400.0, -3.0) for _ in range(4)] + [bump(grid, 400.0, 1.0)]
    boost = Band(5, EqType.PEQ, 400.0, 3.0, 1.0)
    assert guarded(proposal(boost), design_input(seats))[4] == boost


def test_a_moved_correction_comes_back_whole():
    old = list(FLAT.bands)
    old[0] = Band(1, EqType.PEQ, 500.0, -4.0, 1.2)
    meas = SystemEq(True, tuple(old))
    grid = np.geomspace(20.0, 20000.0, 1200)
    seats = [bump(grid, 500.0, 1.0) for _ in range(5)]  # measured with the 500 Hz cut on
    moved = list(old)
    moved[0] = Band(1, EqType.PEQ, 140.0, -3.0, 1.0)  # the slot is used elsewhere
    mask = (grid >= 40.0) & (grid <= 10000.0)
    out = system_eq_opt._seat_guarded(
        moved, meas, design_input(seats), mask=mask, fs_hz=FS, blind=BLIND
    )
    assert out[0] == old[0]


def test_cut_bands_are_matched_to_the_slots_of_their_nearest_old_bands():
    old = list(FLAT.bands)
    old[0] = Band(1, EqType.PEQ, 500.0, -4.0, 1.2)
    old[1] = Band(2, EqType.PEQ, 50.0, -3.0, 1.4)
    meas = SystemEq(True, tuple(old))
    swapped = list(old)
    swapped[0] = Band(1, EqType.PEQ, 140.0, -3.0, 1.0)
    swapped[1] = Band(2, EqType.PEQ, 520.0, -3.0, 2.0)
    matched = system_eq_opt._matched_to_slots(swapped, meas, PROBLEM)
    assert matched[0].freq_hz == 520.0
    assert matched[0].band == 1
    assert matched[1].freq_hz == 140.0
    assert matched[1].band == 2


def test_the_zone_follows_the_crossover_on_the_console():
    real = load(TOPOLOGIES / "hall-2026-10-02.toml").snapshot()
    ctx = DesignContext(None, real, infer_topology(real), GRID, FS)  # type: ignore[arg-type]
    # The dump before 2026-10-02: the tops' LCut at 112.5 Hz; the sub has only a deep shelf,
    # which is not a crossover.
    assert present_crossover_hz(ctx) == pytest.approx(112.5)
    flat = flat_snapshot("hall-2026-10-02")
    no_filters = DesignContext(None, flat, infer_topology(flat), GRID, FS)  # type: ignore[arg-type]
    assert present_crossover_hz(no_filters) is None
