"""Planning Main LR EQ writes. Never weaken, skip or xfail these tests.

The ``ported`` tests keep the cases of the original tool's ``test_safety.py``; there the current
EQ came from the emulator, here it is parsed from the same console dump.
"""

from dataclasses import replace

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from churchtune.domain.changes import WriteScope
from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.domain.eq_model import eq_response_db
from churchtune.errors import UnsafeWrite
from churchtune.mixers.x32 import params as P  # noqa: N812 - short alias kept from the original
from churchtune.mixers.x32.nodes import parse_eq_from_dump
from churchtune.safety.plan import plan_changes, same_step
from tests.fixtures.paths import DUMP_2026_09_29
from tests.fixtures.venues import reference_policy

POLICY = reference_policy()
LAWS = POLICY.laws
FS_HZ = 48000.0
FREQS_HZ = np.geomspace(20.0, 20000.0, 200)


def current_eq() -> SystemEq:
    eq = parse_eq_from_dump(DUMP_2026_09_29.read_text(encoding="utf-8"))
    assert eq is not None
    return eq


def with_band(eq: SystemEq, number: int, **fields) -> SystemEq:
    return eq.with_band(replace(eq.band(number), **fields))


# ------------------------------------------------------------ ported from the original tool
def test_plan_refuses_filter_band_and_big_boost():
    cur = current_eq()

    with pytest.raises(UnsafeWrite):
        plan_changes(cur, with_band(cur, 1, freq_hz=40.0), POLICY)  # band 1 = BU18 HPF
    with pytest.raises(UnsafeWrite):
        plan_changes(cur, with_band(cur, 2, gain_db=4.0), POLICY)


def test_single_gain_change_plans_a_single_write():
    cur = current_eq()

    changes = plan_changes(cur, with_band(cur, 2, gain_db=-3.0), POLICY)

    assert [c.path for c in changes] == ["/main/st/eq/2/g"]


def test_boost_cap_applies_even_without_gain_change():
    cur = with_band(current_eq(), 2, gain_db=6.0)  # the console already has +6 dB on band 2

    with pytest.raises(UnsafeWrite):
        plan_changes(cur, with_band(cur, 2, freq_hz=500.0), POLICY)  # only f changes


def test_turning_main_eq_off_is_refused():
    cur = current_eq()

    with pytest.raises(UnsafeWrite):
        plan_changes(cur, cur.with_on(False), POLICY)


def test_gain_reduction_written_first():
    cur = current_eq()
    target = with_band(cur, 3, freq_hz=300.0, gain_db=-4.0)  # deeper cut -> gain first
    target = with_band(target, 4, freq_hz=2500.0, gain_db=-1.0)  # shallower cut -> gain last

    paths = [c.path for c in plan_changes(cur, target, POLICY)]

    assert paths.index("/main/st/eq/3/g") < paths.index("/main/st/eq/3/f")
    assert paths.index("/main/st/eq/4/g") > paths.index("/main/st/eq/4/f")


# ------------------------------------------------------------ added
def test_identical_states_plan_nothing():
    cur = current_eq()

    assert plan_changes(cur, cur, POLICY) == []


def test_differences_below_one_grid_step_plan_nothing():
    cur = current_eq()
    nudged = with_band(cur, 2, freq_hz=cur.band(2).freq_hz * 1.001, gain_db=-0.05)

    assert plan_changes(cur, nudged, POLICY) == []


def test_switching_the_eq_on_is_planned_last():
    cur = current_eq().with_on(False)
    target = with_band(cur.with_on(True), 2, gain_db=-3.0)

    changes = plan_changes(cur, target, POLICY)

    assert [c.path for c in changes] == ["/main/st/eq/2/g", "/main/st/eq/on"]
    assert (changes[-1].old_value, changes[-1].new_value) == (0, 1)
    assert (changes[-1].old_display, changes[-1].new_display) == ("OFF", "ON")


def test_a_band_left_boosted_while_the_eq_was_off_is_cut_before_the_eq_is_switched_on():
    cur = with_band(current_eq(), 3, gain_db=12.0).with_on(False)
    target = with_band(cur.with_on(True), 3, gain_db=-3.0)

    paths = [c.path for c in plan_changes(cur, target, POLICY)]

    assert paths.index("/main/st/eq/3/g") < paths.index("/main/st/eq/on")
    assert paths[-1] == "/main/st/eq/on"


def test_switching_the_eq_on_is_refused_while_an_untouched_band_holds_a_boost_above_the_limit():
    cur = with_band(current_eq(), 3, gain_db=12.0).with_on(False)

    with pytest.raises(UnsafeWrite, match="refusing to switch the EQ on"):
        plan_changes(cur, cur.with_on(True), POLICY)
    with pytest.raises(UnsafeWrite, match="refusing to switch the EQ on"):
        plan_changes(cur, with_band(cur.with_on(True), 2, gain_db=-3.0), POLICY)


def test_switching_on_an_eq_whose_bands_all_pass_the_limit_is_a_single_write():
    cur = current_eq().with_on(False)

    changes = plan_changes(cur, cur.with_on(True), POLICY)

    assert [(c.path, c.new_value) for c in changes] == [("/main/st/eq/on", 1)]


def test_changes_carry_quantized_wire_values_and_console_display_strings():
    cur = current_eq()
    target = with_band(cur, 2, freq_hz=480.0, gain_db=-9.0, q=1.5)

    changes = {c.path: c for c in plan_changes(cur, target, POLICY)}

    freq = changes["/main/st/eq/2/f"]
    assert freq.new_value == pytest.approx(0.46)
    assert (freq.old_display, freq.new_display) == ("232.3 Hz", "479.8 Hz")
    assert changes["/main/st/eq/2/g"].new_display == "-9.00 dB"
    assert changes["/main/st/eq/2/q"].new_display == "Q 1.5"
    for change in changes.values():
        kind = change.path.rsplit("/", 1)[1]
        assert LAWS.step_index(kind, change.new_value) / (
            {"f": 200, "g": 120, "q": 71}[kind]
        ) == pytest.approx(change.new_value)


def test_type_change_between_adjustable_types_is_planned_as_an_int():
    cur = current_eq()

    changes = plan_changes(cur, with_band(cur, 2, type=EqType.VEQ), POLICY)

    assert [(c.path, c.new_value, c.new_display) for c in changes] == [
        ("/main/st/eq/2/type", P.EQ_TYPES.index(EqType.VEQ), "VEQ")
    ]


@pytest.mark.parametrize("cut", [EqType.LCUT, EqType.HCUT, EqType.BU12, EqType.LR24])
def test_target_cut_filter_type_is_refused(cut):
    cur = current_eq()

    with pytest.raises(UnsafeWrite, match="cut filter"):
        plan_changes(cur, with_band(cur, 2, type=cut), POLICY)


@pytest.mark.parametrize("field", ["freq_hz", "gain_db", "q"])
def test_any_change_to_a_cut_filter_band_is_refused(field):
    cur = current_eq()
    assert cur.band(1).type is EqType.BU18
    changed = {"freq_hz": 80.0, "gain_db": 0.0, "q": 2.0}[field]

    with pytest.raises(UnsafeWrite, match="cut filter"):
        plan_changes(cur, with_band(cur, 1, **{field: changed}), POLICY)


def test_boost_at_the_limit_is_planned_and_one_step_above_is_refused():
    cur = current_eq()

    assert plan_changes(cur, with_band(cur, 2, gain_db=3.0), POLICY)
    with pytest.raises(UnsafeWrite, match="boost"):
        plan_changes(cur, with_band(cur, 2, gain_db=3.25), POLICY)


@pytest.mark.parametrize(
    ("kind", "a", "b", "same"),
    [
        ("type", 2, 2, True),
        ("type", 2, 3, False),
        ("on", 1, 1, True),
        # Switches and types are integers: floats and bools never count as the same value.
        ("on", 1, 1.0, False),
        ("on", 1, 1.7, False),
        ("on", 0, 0.9, False),
        ("on", 1, True, False),
        ("on", 1, float("inf"), False),
        ("invert", 0, float("-inf"), False),
        ("type", 2, 2.0, False),
        ("type", 2, float("nan"), False),
        ("invert", 0, 1, False),
        ("g", 0.2, 0.2 + 0.4 / 120, True),
        ("g", 0.2, 0.2 + 1 / 120, False),
        ("f", 0.46, 0.4601, True),
        ("fader", 0.2590420332355816, 0.25904203951358795, True),
        ("time", 0.0, 1 / 4997, False),
    ],
)
def test_same_step(kind, a, b, same):
    assert same_step(LAWS, kind, a, b) is same


def test_same_step_rejects_a_kind_without_a_grid():
    with pytest.raises(ValueError, match="no value grid"):
        same_step(LAWS, "fade", 0.1, 0.1)


# ------------------------------------------------------------ properties
adjustable_band = st.builds(
    lambda t, f, g, q: (t, f, g, q),
    st.sampled_from([EqType.PEQ, EqType.VEQ, EqType.LSHV, EqType.HSHV]),
    st.floats(min_value=20.0, max_value=20000.0),
    st.floats(min_value=-15.0, max_value=3.0),
    st.floats(min_value=0.3, max_value=10.0),
)


def _apply(eq: SystemEq, path: str, value) -> SystemEq:
    """Apply one planned write to an EQ state, as the console would."""
    parts = path.split("/")
    if parts[-1] == "on":
        return eq.with_on(bool(value))
    band = eq.band(int(parts[-2]))
    field = {
        "type": {"type": P.EQ_TYPES[int(value)]} if parts[-1] == "type" else {},
        "f": {"freq_hz": P.norm_to_freq(value)} if parts[-1] == "f" else {},
        "g": {"gain_db": P.norm_to_gain(value)} if parts[-1] == "g" else {},
        "q": {"q": P.norm_to_q(value)} if parts[-1] == "q" else {},
    }[parts[-1]]
    return eq.with_band(replace(band, **field))


@given(targets=st.lists(adjustable_band, min_size=5, max_size=5))
def test_planned_writes_reach_the_target_and_touch_only_main_eq(targets):
    cur = current_eq()
    target = cur
    for number, (eq_type, freq_hz, gain_db, q) in zip(range(2, 7), targets, strict=True):
        target = target.with_band(Band(number, eq_type, freq_hz, gain_db, q))

    changes = plan_changes(cur, target, POLICY)

    state = cur
    for change in changes:
        POLICY.validate(change.path, change.new_value, WriteScope.MAIN)
        assert change.path.startswith("/main/st/eq/")
        assert not change.path.startswith("/main/st/eq/1/")
        state = _apply(state, change.path, change.new_value)
    assert plan_changes(state, target, POLICY) == []
    assert len({c.path for c in changes}) == len(changes)


@given(
    freq_hz=st.floats(min_value=20.0, max_value=20000.0),
    gain_db=st.floats(min_value=-15.0, max_value=3.0),
    q=st.floats(min_value=0.3, max_value=10.0),
)
def test_band_gain_stays_at_the_lower_end_while_type_freq_and_q_move(freq_hz, gain_db, q):
    """While type/f/q of a band move, its gain is the lower of the old and the new gain.

    This pins the ordering rule of the original tool only. The full "never louder than
    max(before, after) + 0.5 dB at any frequency" property arrives with ``order_plan`` in M5.
    """
    cur = current_eq()
    target = with_band(cur, 3, freq_hz=freq_hz, gain_db=gain_db, q=q)
    low_gain_db = min(P.snap_gain_db(cur.band(3).gain_db), P.snap_gain_db(gain_db))

    state = cur
    for change in plan_changes(cur, target, POLICY):
        state = _apply(state, change.path, change.new_value)
        if change.path != "/main/st/eq/3/g" and state.band(3).gain_db > low_gain_db + 1e-9:
            # Still at the old (lower or equal) gain: this must be the gain-up-last order.
            assert state.band(3).gain_db == pytest.approx(P.snap_gain_db(cur.band(3).gain_db))
            assert P.snap_gain_db(gain_db) >= state.band(3).gain_db - 1e-9
        assert np.all(np.isfinite(eq_response_db(state, FREQS_HZ, FS_HZ)))
