"""The order of writes is never louder than before or after (SPEC §9.3).

Never weaken, skip or xfail these tests. The property test builds random plans on Main LR and two
matrices and checks every intermediate state by simulation.
"""

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.domain.snapshot import SignalRef, SourceKind
from churchtune.errors import UnsafeWrite
from churchtune.safety import order
from churchtune.safety.order import Step, StripState, check_order, order_steps

FS = 48000.0
MAIN = SignalRef(SourceKind.MAIN_L)
TOPS = SignalRef(SourceKind.MATRIX, 1)
SUB = SignalRef(SourceKind.MATRIX, 3)
CHAINS = {"tops": (MAIN, TOPS), "sub": (MAIN, SUB)}
FREQS = (60.0, 120.0, 250.0, 480.0, 1000.0, 2000.0)


def flat_eq(on: bool = True) -> SystemEq:
    return SystemEq(on, tuple(Band(n, EqType.PEQ, FREQS[n - 1], 0.0, 1.4) for n in range(1, 7)))


def state(eq=None, fader=0.0, on=True) -> StripState:
    return StripState(eq or flat_eq(), fader, on)


def plan(before, after):
    steps = order_steps(before, after, CHAINS, FS)
    check_order(before, steps, after, CHAINS, FS)
    return steps


def test_a_cut_that_moves_never_passes_through_zero():
    cut = flat_eq().with_band(Band(2, EqType.PEQ, 480.0, -9.0, 1.5))
    moved = flat_eq().with_band(Band(2, EqType.PEQ, 520.0, -9.0, 2.0))
    before = {MAIN: state(cut), TOPS: state(), SUB: state()}
    after = {MAIN: state(moved), TOPS: state(), SUB: state()}
    steps = plan(before, after)
    assert all(s.band is None or s.band.gain_db == -9.0 for s in steps)


def test_a_naive_order_that_lifts_a_cut_first_is_refused():
    cut = flat_eq().with_band(Band(2, EqType.PEQ, 480.0, -9.0, 1.5))
    moved = flat_eq().with_band(Band(2, EqType.PEQ, 2000.0, -9.0, 1.5))
    other_cut = moved.with_band(Band(3, EqType.PEQ, 480.0, -9.0, 1.5))
    before = {MAIN: state(cut), TOPS: state(), SUB: state()}
    after = {MAIN: state(other_cut), TOPS: state(), SUB: state()}
    naive = [  # band 2 moves away before band 3 takes over 480 Hz: louder at 480 in between
        Step(MAIN, "band", band=Band(2, EqType.PEQ, 2000.0, -9.0, 1.5), param="freq_hz"),
        Step(MAIN, "band", band=Band(3, EqType.PEQ, 480.0, -9.0, 1.5), param="gain_db"),
    ]
    with pytest.raises(UnsafeWrite, match="louder"):
        check_order(before, naive, after, CHAINS, FS)
    plan(before, after)


def test_boosts_and_faders_up_come_last_mutes_and_faders_down_first():
    boost = flat_eq().with_band(Band(4, EqType.PEQ, 1000.0, 3.0, 1.0))
    before = {MAIN: state(), TOPS: state(fader=-10.0), SUB: state(fader=-5.0)}
    after = {MAIN: state(boost), TOPS: state(fader=-6.0), SUB: state(fader=-8.0, on=False)}
    steps = plan(before, after)
    kinds = [(s.what, s.strip) for s in steps]
    assert kinds.index(("on", SUB)) < kinds.index(("band", MAIN))
    assert kinds.index(("fader", SUB)) < kinds.index(("band", MAIN))
    assert kinds[-1] == ("fader", TOPS)


def test_unmuting_last_and_eq_switches_late():
    before = {MAIN: state(), TOPS: state(on=False), SUB: state(flat_eq(on=False))}
    after = {MAIN: state(), TOPS: state(), SUB: state()}
    steps = plan(before, after)
    assert steps[-1].what == "on"


def test_a_plan_must_describe_the_same_strips():
    with pytest.raises(UnsafeWrite, match="same strips"):
        order_steps({MAIN: state()}, {TOPS: state()}, CHAINS, FS)


def test_steps_that_do_not_reach_the_plan_are_refused():
    before = {MAIN: state(), TOPS: state(), SUB: state()}
    after = {MAIN: state(), TOPS: state(fader=-3.0), SUB: state()}
    with pytest.raises(UnsafeWrite, match="planned state"):
        check_order(before, [], after, CHAINS, FS)


def test_a_fader_down_is_never_too_loud_even_from_minus_infinity():
    before = {MAIN: state(), TOPS: state(fader=-math.inf), SUB: state()}
    after = {MAIN: state(), TOPS: state(fader=-20.0), SUB: state()}
    assert [s.what for s in plan(before, after)] == ["fader"]


def test_a_shelf_that_moves_and_changes_type_keeps_its_gain():
    high = flat_eq().with_band(Band(5, EqType.HSHV, 8000.0, 2.0, 1.4))
    low = flat_eq().with_band(Band(5, EqType.LSHV, 60.0, 2.0, 1.4))
    before = {MAIN: state(high), TOPS: state(), SUB: state()}
    after = {MAIN: state(low), TOPS: state(), SUB: state()}
    steps = plan(before, after)
    assert steps[-1].band is not None
    assert steps[-1].band == low.band(5)


def test_a_band_without_a_safe_order_is_refused(monkeypatch):
    cut = flat_eq().with_band(Band(2, EqType.PEQ, 480.0, -9.0, 1.5))
    before = {MAIN: state(cut), TOPS: state(), SUB: state()}
    after = {MAIN: state(), TOPS: state(), SUB: state()}
    monkeypatch.setattr(order._Simulation, "safe", lambda self, steps: False)
    with pytest.raises(UnsafeWrite, match="no safe order for band 2"):
        order_steps(before, after, CHAINS, FS)


def test_late_steps_that_would_still_be_too_loud_are_refused(monkeypatch):
    before = {MAIN: state(), TOPS: state(on=False), SUB: state()}
    after = {MAIN: state(), TOPS: state(), SUB: state()}
    monkeypatch.setattr(order._Simulation, "safe", lambda self, steps: False)
    with pytest.raises(UnsafeWrite, match="unmuting last"):
        order_steps(before, after, CHAINS, FS)


band_strategy = st.builds(
    lambda n, f, g, q: Band(n, EqType.PEQ, f, g, q),
    st.integers(1, 6),
    st.sampled_from([50.0, 80.0, 125.0, 200.0, 315.0, 500.0, 800.0, 1250.0, 2000.0, 4000.0]),
    st.sampled_from([-12.0, -9.0, -6.0, -3.0, 0.0, 1.5, 3.0]),
    st.sampled_from([0.7, 1.0, 1.4, 2.0, 4.0]),
)


def eq_strategy():
    return st.lists(band_strategy, max_size=3).map(lambda bands: _apply(flat_eq(), bands))


def _apply(eq, bands):
    for band in bands:
        eq = eq.with_band(band)
    return eq


strip_strategy = st.builds(
    StripState,
    eq_strategy(),
    st.sampled_from([-30.0, -20.0, -10.0, -5.0, 0.0]),
    st.booleans(),
)


@settings(max_examples=60, deadline=None)
@given(
    st.fixed_dictionaries({MAIN: strip_strategy, TOPS: strip_strategy, SUB: strip_strategy}),
    st.fixed_dictionaries({MAIN: strip_strategy, TOPS: strip_strategy, SUB: strip_strategy}),
)
def test_every_random_plan_is_ordered_never_louder(before, after):
    steps = order_steps(before, after, CHAINS, FS)
    check_order(before, steps, after, CHAINS, FS)


shelf_band_strategy = st.builds(
    lambda kind, n, f, g, q: Band(n, kind, f, g, q),
    st.sampled_from([EqType.PEQ, EqType.VEQ, EqType.LSHV, EqType.HSHV]),
    st.integers(1, 6),
    st.sampled_from([50.0, 125.0, 315.0, 800.0, 2000.0, 8000.0]),
    st.sampled_from([-12.0, -6.0, -2.0, 0.0, 2.0, 3.0]),
    st.sampled_from([0.7, 1.0, 2.0]),
)
shelf_strip_strategy = st.builds(
    StripState,
    st.lists(shelf_band_strategy, max_size=2).map(lambda bands: _apply(flat_eq(), bands)),
    st.sampled_from([-20.0, -10.0, 0.0]),
    st.booleans(),
)


@settings(max_examples=60, deadline=None)
@given(
    st.fixed_dictionaries({MAIN: shelf_strip_strategy, TOPS: shelf_strip_strategy}),
    st.fixed_dictionaries({MAIN: shelf_strip_strategy, TOPS: shelf_strip_strategy}),
)
def test_plans_with_shelves_are_never_louder_or_refused(before, after):
    """A shelf with a high Q overshoots on its other side: some type changes have no safe order.

    Refusing such a plan is safe; any order that is produced must still pass the simulation.
    """
    chains = {"tops": (MAIN, TOPS)}
    try:
        steps = order_steps(before, after, chains, FS)
    except UnsafeWrite as refused:
        reason = str(refused)
    else:
        check_order(before, steps, after, chains, FS)
        return
    assert "no safe order" in reason or "too loud" in reason


def test_steps_that_miss_the_plan_are_refused_by_the_planner(monkeypatch):
    cut = flat_eq().with_band(Band(2, EqType.PEQ, 480.0, -9.0, 1.5))
    before = {MAIN: state(), TOPS: state(), SUB: state()}
    after = {MAIN: state(cut), TOPS: state(), SUB: state()}
    monkeypatch.setattr(order, "_band_orders", lambda strip, old, new: [[]])
    with pytest.raises(UnsafeWrite, match="do not reach the planned state"):
        order_steps(before, after, CHAINS, FS)
