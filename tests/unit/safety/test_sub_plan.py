"""Writes of the sub scope from crossover and alignment targets.

Never weaken, skip or xfail these tests.
"""

import math
from dataclasses import replace

import pytest

from churchtune.design.crossover import plan_crossover
from churchtune.domain.changes import WriteScope
from churchtune.domain.console import ConsoleState, MatrixState, OutputDelay, Roles
from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.errors import UnsafeWrite
from churchtune.mixers.x32 import params as P  # noqa: N812 - short alias kept from the original
from churchtune.safety.sub_plan import alignment_changes, crossover_changes
from tests.fixtures.venues import reference_policy

FS_HZ = 48000.0
POLICY = reference_policy()
UNVERIFIED_OK = reference_policy(allow_unverified_writes=True)
ROLES = Roles(sub=(3,), mains=(1, 2), others=(5, 6))
GRID = P.X32EqGrid()


def bands(**overrides):
    result = [Band(i, EqType.PEQ, 1000.0, 0.0, 2.0) for i in range(1, 7)]
    for key, band in overrides.items():
        result[int(key[1:]) - 1] = band
    return tuple(result)


def console(sub_bands=None, top_bands=None, sub_fader_db=-37.5, sub_invert=False, delay_on=False):
    def matrix(n, eq_bands, outputs, fader_db=0.0, invert=False):
        eq = SystemEq(True, eq_bands or bands())
        return MatrixState(n, f"m{n}", eq, True, fader_db, invert, outputs)

    matrices = {
        1: matrix(1, top_bands, (8,)),
        2: matrix(2, top_bands, (7,)),
        3: matrix(3, sub_bands, (6,), sub_fader_db, sub_invert),
        5: matrix(5, None, (4,)),
        6: matrix(6, None, (3,)),
    }
    delays = {o: OutputDelay(o, delay_on, 0.3) for o in range(1, 17)}
    return ConsoleState(SystemEq(True, bands()), matrices, delays)


# ------------------------------------------------------------ crossover
def test_crossover_changes_are_ordered_gain_down_then_filters_then_gain_up():
    sub = bands(
        b2=Band(2, EqType.PEQ, 120.0, 3.0, 2.0),  # boost removed: gain down
        b4=Band(4, EqType.PEQ, 1390.0, -15.0, 2.0),  # pseudo low-pass removed: gain up
        b6=Band(6, EqType.HSHV, 751.7, -15.0, 2.0),
    )
    tops = bands(
        b1=Band(1, EqType.LCUT, 112.5, 0.0, 2.0), b2=Band(2, EqType.VEQ, 90.0, -14.75, 2.0)
    )
    state = console(sub_bands=sub, top_bands=tops)
    plan = plan_crossover(state, ROLES, GRID, FS_HZ)

    changes = crossover_changes(state, plan.targets, POLICY)

    paths = [c.path for c in changes]
    assert paths[0] == "/mtx/03/eq/2/g"  # the only gain reduction comes first
    filters = [i for i, p in enumerate(paths) if p.endswith(("/type", "/f", "/q"))]
    gain_ups = [paths.index(p) for p in ("/mtx/03/eq/4/g", "/mtx/01/eq/2/g", "/mtx/02/eq/2/g")]
    assert min(gain_ups) > max(filters)
    assert set(paths) == {
        "/mtx/03/eq/2/g", "/mtx/03/eq/4/g", "/mtx/03/eq/6/type", "/mtx/03/eq/6/f",
        "/mtx/01/eq/1/type", "/mtx/01/eq/1/f", "/mtx/01/eq/2/g",
        "/mtx/02/eq/1/type", "/mtx/02/eq/1/f", "/mtx/02/eq/2/g",
    }  # fmt: skip
    for change in changes:
        POLICY.validate(change.path, change.new_value, WriteScope.SUB)


def test_crossover_changes_carry_wire_values_and_console_display_strings():
    state = console(sub_bands=bands(b6=Band(6, EqType.HSHV, 751.7, -15.0, 2.0)))
    plan = plan_crossover(state, Roles(sub=(3,), mains=()), GRID, FS_HZ)

    changes = {c.path: c for c in crossover_changes(state, plan.targets, POLICY)}

    lpf_type = changes["/mtx/03/eq/6/type"]
    assert (lpf_type.old_value, lpf_type.new_value) == (4, 13)
    assert (lpf_type.old_display, lpf_type.new_display) == ("HShv", "LR24")
    lpf_freq = changes["/mtx/03/eq/6/f"]
    assert lpf_freq.old_value == pytest.approx(0.525)
    assert lpf_freq.new_value == pytest.approx(0.235)
    assert (lpf_freq.old_display, lpf_freq.new_display) == ("751.7 Hz", "101.4 Hz")


def test_crossover_changes_are_empty_when_the_console_already_matches():
    state = console()
    targets = {3: state.matrices[3].eq.bands}

    assert crossover_changes(state, targets, POLICY) == []


def test_crossover_changes_refuse_targets_outside_the_policy():
    state = console()
    lr24_in_the_middle = {3: bands(b3=Band(3, EqType.LR24, 100.0, 0.0, 2.0))}
    too_much_boost = {3: bands(b2=Band(2, EqType.PEQ, 60.0, 9.0, 2.0))}
    unused_matrix = {
        4: bands(b2=Band(2, EqType.PEQ, 60.0, -3.0, 2.0)),
    }
    state_with_4 = replace(
        state, matrices={**state.matrices, 4: replace(state.matrices[3], number=4)}
    )

    with pytest.raises(UnsafeWrite, match="not allowed on band 3"):
        crossover_changes(state, lr24_in_the_middle, POLICY)
    with pytest.raises(UnsafeWrite, match="boost"):
        crossover_changes(state, too_much_boost, POLICY)
    with pytest.raises(UnsafeWrite, match="not in the sub scope"):
        crossover_changes(state_with_4, unused_matrix, POLICY)


# ------------------------------------------------------------ alignment
def test_level_only_alignment_writes_just_the_sub_fader():
    changes = alignment_changes(
        console(), ROLES, POLICY, delay_ms=0.0, polarity=1, fader_change_db=6.0
    )

    assert [c.path for c in changes] == ["/mtx/03/mix/fader"]
    fader = changes[0]
    assert fader.old_value == pytest.approx(P.quantize_fader(P.fader_to_norm(-37.5)))
    assert P.norm_to_fader(fader.new_value) == pytest.approx(-31.5, abs=0.05)
    assert (fader.old_display, fader.new_display) == ("-37.5", "-31.5")


def test_sub_fader_never_goes_above_the_limit():
    changes = alignment_changes(
        console(sub_fader_db=-2.0), ROLES, POLICY, delay_ms=0.0, polarity=1, fader_change_db=6.0
    )

    assert P.norm_to_fader(changes[0].new_value) == pytest.approx(0.0, abs=0.05)


def test_fader_that_is_down_stays_down():
    changes = alignment_changes(
        console(sub_fader_db=-math.inf),
        ROLES,
        POLICY,
        delay_ms=0.0,
        polarity=1,
        fader_change_db=6.0,
    )

    assert (changes[0].old_value, changes[0].new_value) == (0.0, 0.0)
    assert changes[0].old_display == "-oo"


@pytest.mark.parametrize("fader_change_db", [0.0, 0.4, -0.49])
def test_small_fader_changes_are_not_written(fader_change_db):
    assert (
        alignment_changes(
            console(), ROLES, POLICY, delay_ms=0.0, polarity=1, fader_change_db=fader_change_db
        )
        == []
    )


def test_positive_delay_delays_the_sub_output_and_switches_the_delay_on():
    changes = alignment_changes(console(), ROLES, UNVERIFIED_OK, delay_ms=9.2, polarity=1)

    assert [c.path for c in changes] == [
        "/outputs/main/06/delay/on",
        "/outputs/main/06/delay/time",
    ]
    assert (changes[0].old_value, changes[0].new_value) == (0, 1)
    assert P.norm_to_delay(changes[1].new_value) == pytest.approx(9.2, abs=0.05)
    assert (changes[1].old_display, changes[1].new_display) == ("0.3 ms", "9.2 ms")


def test_negative_delay_delays_the_tops_and_the_other_speakers_alike():
    changes = alignment_changes(console(), ROLES, UNVERIFIED_OK, delay_ms=-1.8, polarity=1)

    assert [c.path for c in changes if c.path.endswith("/time")] == [
        "/outputs/main/08/delay/time",
        "/outputs/main/07/delay/time",
        "/outputs/main/04/delay/time",
        "/outputs/main/03/delay/time",
    ]
    assert all(
        P.norm_to_delay(c.new_value) == pytest.approx(1.8, abs=0.05)
        for c in changes
        if c.path.endswith("/time")
    )


def test_delay_is_added_to_a_delay_that_is_already_on():
    changes = alignment_changes(
        console(delay_on=True), ROLES, UNVERIFIED_OK, delay_ms=5.0, polarity=1
    )

    assert [c.path for c in changes] == ["/outputs/main/06/delay/time"]
    assert P.norm_to_delay(changes[0].new_value) == pytest.approx(5.3, abs=0.05)


def test_delay_is_capped_at_the_policy_limit():
    changes = alignment_changes(console(), ROLES, UNVERIFIED_OK, delay_ms=80.0, polarity=1)

    assert P.norm_to_delay(changes[-1].new_value) == pytest.approx(50.0, abs=0.05)


@pytest.mark.parametrize("delay_ms", [0.0, 0.05, -0.05, 0.04])
def test_tiny_delays_are_not_written(delay_ms):
    assert alignment_changes(console(), ROLES, UNVERIFIED_OK, delay_ms=delay_ms, polarity=1) == []


def test_switching_the_delay_on_without_a_new_time_is_a_single_write():
    state = console()
    state = replace(state, delays={**state.delays, 6: OutputDelay(6, False, 9.5)})

    changes = alignment_changes(state, ROLES, UNVERIFIED_OK, delay_ms=9.5, polarity=1)

    assert [c.path for c in changes] == ["/outputs/main/06/delay/on"]


@pytest.mark.parametrize(
    ("inverted", "expected"), [(False, (0, 1, "norm", "INV")), (True, (1, 0, "INV", "norm"))]
)
def test_inverted_polarity_toggles_the_sub_matrix(inverted, expected):
    changes = alignment_changes(
        console(sub_invert=inverted), ROLES, UNVERIFIED_OK, delay_ms=0.0, polarity=-1
    )

    assert [c.path for c in changes] == ["/mtx/03/preamp/invert"]
    change = changes[0]
    assert (change.old_value, change.new_value, change.old_display, change.new_display) == expected


def test_normal_polarity_writes_nothing():
    assert alignment_changes(console(), ROLES, UNVERIFIED_OK, delay_ms=0.0, polarity=1) == []


def test_full_alignment_package_is_ordered_delay_polarity_fader():
    changes = alignment_changes(
        console(), ROLES, UNVERIFIED_OK, delay_ms=4.0, polarity=-1, fader_change_db=3.0
    )

    assert [c.path.rsplit("/", 2)[-2:] for c in changes] == [
        ["delay", "on"],
        ["delay", "time"],
        ["preamp", "invert"],
        ["mix", "fader"],
    ]


@pytest.mark.parametrize(("delay_ms", "polarity"), [(9.2, 1), (-1.8, 1), (0.0, -1)])
def test_delay_and_polarity_are_refused_while_those_writes_are_unverified(delay_ms, polarity):
    with pytest.raises(UnsafeWrite, match="never verified on hardware"):
        alignment_changes(console(), ROLES, POLICY, delay_ms=delay_ms, polarity=polarity)
