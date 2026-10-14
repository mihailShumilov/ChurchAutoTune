"""Ordered steps as X32 writes, each in its scope and validated by the policy."""

import pytest

from churchtune.domain.changes import WriteScope
from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.domain.snapshot import SignalRef, SourceKind
from churchtune.errors import UnsafeWrite
from churchtune.mixers.x32.laws import X32ValueLaws
from churchtune.mixers.x32.plan_writes import PlannedChange, plan_writes, runs_by_scope
from churchtune.safety.order import Step, StripState, order_steps
from churchtune.safety.policy import WritePolicy
from tests.fixtures.venues import MATRICES

FS = 48000.0
LAWS = X32ValueLaws()
MAIN = SignalRef(SourceKind.MAIN_L)
TOPS = SignalRef(SourceKind.MATRIX, 1)
SUB = SignalRef(SourceKind.MATRIX, 3)
CHAINS = {"tops": (MAIN, TOPS), "sub": (MAIN, SUB)}
FREQS = (60.0, 120.0, 250.0, 480.0, 1000.0, 2000.0)


def flat() -> SystemEq:
    return SystemEq(True, tuple(Band(n, EqType.PEQ, FREQS[n - 1], 0.0, 1.4) for n in range(1, 7)))


def test_steps_become_writes_in_order_with_their_scopes():
    cut = flat().with_band(Band(4, EqType.PEQ, 480.0, -6.0, 1.5))
    crossover = flat().with_band(Band(6, EqType.LR24, 100.0, 0.0, 1.4))
    before = {MAIN: StripState(flat()), TOPS: StripState(flat()), SUB: StripState(flat(), -20.0)}
    after = {MAIN: StripState(cut), TOPS: StripState(flat()), SUB: StripState(crossover, -26.0)}
    steps = order_steps(before, after, CHAINS, FS)
    planned = plan_writes(steps, before, LAWS)
    paths = [p.change.path for p in planned]
    assert paths[0] == "/mtx/03/mix/fader"  # faders down first
    assert "/main/st/eq/4/g" in paths
    assert "/mtx/03/eq/6/type" in paths
    assert {p.scope for p in planned} == {WriteScope.SYSTEM_EQ, WriteScope.LEVELS}
    policy = WritePolicy(
        LAWS,
        MATRICES,
        sub_matrices=frozenset({3}),
        top_matrices=frozenset({1, 2}),
        level_baselines=(("/mtx/03/mix/fader", -20.0),),
        allow_cut_filters=True,
    )
    for item in planned:
        policy.validate(item.change.path, item.change.new_value, item.scope)


def test_a_write_already_on_the_grid_step_is_left_out():
    almost = flat().with_band(Band(4, EqType.PEQ, 480.0, 0.01, 1.4))
    before = {MAIN: StripState(flat()), TOPS: StripState(flat()), SUB: StripState(flat())}
    steps = [Step(MAIN, "band", band=almost.band(4), param="gain_db")]
    assert plan_writes(steps, before, LAWS) == ()


def test_switches_and_mutes():
    before = {MAIN: StripState(flat()), TOPS: StripState(flat()), SUB: StripState(flat())}
    steps = [Step(SUB, "on", value=False), Step(TOPS, "eq_on", value=False)]
    planned = plan_writes(steps, before, LAWS)
    assert [(p.scope, p.change.path, p.change.new_value) for p in planned] == [
        (WriteScope.TEMP_MUTES, "/mtx/03/mix/on", 0),
        (WriteScope.SYSTEM_EQ, "/mtx/01/eq/on", 0),
    ]


def test_buses_and_mono_are_never_written():
    bus = SignalRef(SourceKind.BUS, 2)
    before = {bus: StripState(flat())}
    with pytest.raises(UnsafeWrite, match="not a strip"):
        plan_writes([Step(bus, "fader", value=-10.0)], before, LAWS)


def test_a_band_step_must_name_its_band():
    before = {MAIN: StripState(flat())}
    with pytest.raises(UnsafeWrite, match="band step"):
        plan_writes([Step(MAIN, "band")], before, LAWS)


def test_runs_group_consecutive_scopes():
    change = plan_writes([Step(SUB, "on", value=False)], {SUB: StripState(flat())}, LAWS)[0].change
    eq, lv = WriteScope.SYSTEM_EQ, WriteScope.LEVELS
    planned = [PlannedChange(lv, change), PlannedChange(eq, change), PlannedChange(eq, change),
               PlannedChange(lv, change)]  # fmt: skip
    assert [(scope, len(changes)) for scope, changes in runs_by_scope(planned)] == [
        (lv, 1),
        (eq, 2),
        (lv, 1),
    ]
