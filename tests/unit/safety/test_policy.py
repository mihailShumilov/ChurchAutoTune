"""Write allowlists and value limits. Never weaken, skip or xfail these tests.

The ``ported`` blocks keep the cases of the original tool's ``test_safety.py`` and
``test_sub.py``; the rest adds neighbours of allowed paths and hostile values.
"""

import math
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from churchtune.domain.changes import Change, WriteScope
from churchtune.errors import UnsafeWrite
from churchtune.mixers.x32 import params as P  # noqa: N812 - short alias kept from the original
from churchtune.mixers.x32.laws import X32ValueLaws
from churchtune.safety.policy import (
    MAIN_PATH_RE,
    SUB_DELAY_PATH_RE,
    SUB_EQ_PATH_RE,
    SUB_MATRIX_PATH_RE,
    WriteLimits,
    WritePolicy,
)
from tests.fixtures.venues import MATRICES, reference_policy

POLICY = reference_policy()
# Same policy with the hardware-unverified writes (output delay, polarity) switched on.
UNVERIFIED_OK = reference_policy(allow_unverified_writes=True)
MAIN, SUB = WriteScope.MAIN, WriteScope.SUB
LR24 = P.EQ_TYPES.index("LR24")
BU18 = P.EQ_TYPES.index("BU18")
PEQ = P.EQ_TYPES.index("PEQ")


def gain(db):
    return P.quantize_gain(P.gain_to_norm(db))


# ------------------------------------------------------------ ported: main scope
@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("/ch/10/mix/on", 1),
        ("/main/st/mix/fader", 0.5),
        ("/main/st/eq/7/g", 0.5),
        ("/main/st/insert/on", 1),
        ("/main/st/eq/2/type", BU18),  # cut filter refused
        ("/main/st/eq/2/g", 1.5),
        ("/fx/8/insert", 1),
        ("/main/st/eq/2/f", 1),  # int where a normalized float is required
    ],
)
def test_writer_validation_refuses(path, value):
    with pytest.raises(UnsafeWrite):
        POLICY.validate(path, value, MAIN)


# ------------------------------------------------------------ ported: sub scope
@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("/mtx/04/eq/1/g", 0.5),  # unused matrix 4 is not touched
        ("/ch/10/mix/on", 1),
        ("/main/st/mix/fader", 0.5),
        ("/headamp/041/phantom", 1),
        ("/mtx/03/eq/3/type", LR24),  # LR24 only on band 1 / 6
        ("/mtx/03/eq/2/g", gain(7.0)),  # boost > +6
        ("/mtx/03/mix/fader", P.fader_to_norm(3.0)),  # fader above 0 dB
        ("/outputs/main/06/delay/time", P.delay_to_norm(80.0)),  # delay > 50 ms
        ("/outputs/main/06/delay/on", 2),
        ("/mtx/03/preamp/invert", 1.0),
    ],
)
def test_sub_scope_refuses(path, value):
    for policy in (POLICY, UNVERIFIED_OK):
        with pytest.raises(UnsafeWrite):
            policy.validate(path, value, SUB)


def test_sub_scope_allows_needed_params():
    for path, value in [
        ("/mtx/03/eq/6/type", LR24),
        ("/mtx/01/eq/1/type", LR24),
        ("/mtx/03/eq/6/f", 0.235),
        ("/mtx/03/mix/on", 0),
        ("/mtx/05/mix/on", 1),
        ("/mtx/03/preamp/invert", 1),
        ("/outputs/main/07/delay/on", 1),
        ("/outputs/main/07/delay/time", P.delay_to_norm(4.2)),
        ("/mtx/03/mix/fader", P.fader_to_norm(-40.0)),
    ]:
        UNVERIFIED_OK.validate(path, value, SUB)
    with pytest.raises(UnsafeWrite):
        UNVERIFIED_OK.validate("/mtx/03/eq/6/type", LR24, MAIN)  # main scope still EQ-only


# ------------------------------------------------------------ hardware-unverified writes
UNVERIFIED_WRITES = [
    ("/mtx/03/preamp/invert", 1),
    ("/mtx/03/preamp/invert", 0),
    ("/outputs/main/07/delay/on", 1),
    ("/outputs/main/07/delay/on", 0),
    ("/outputs/main/07/delay/time", P.delay_to_norm(4.2)),
]


@pytest.mark.parametrize(("path", "value"), UNVERIFIED_WRITES)
def test_unverified_writes_are_refused_by_default(path, value):
    with pytest.raises(UnsafeWrite, match="never verified on hardware") as excinfo:
        POLICY.validate(path, value, SUB)

    assert excinfo.value.context == {"path": path, "unverified": True}


@pytest.mark.parametrize(("path", "value"), UNVERIFIED_WRITES)
def test_unverified_writes_need_an_explicit_opt_in(path, value):
    UNVERIFIED_OK.validate(path, value, SUB)


def test_verified_sub_writes_do_not_need_the_opt_in():
    for path, value in [
        ("/mtx/03/eq/6/type", LR24),
        ("/mtx/03/eq/6/f", 0.235),
        ("/mtx/03/mix/on", 0),
        ("/mtx/03/mix/fader", P.fader_to_norm(-29.3)),
    ]:
        POLICY.validate(path, value, SUB)


def test_opt_in_is_off_by_default():
    assert WritePolicy(X32ValueLaws(), MATRICES).allow_unverified_writes is False


# ------------------------------------------------------------ main scope, added
@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("/main/st/eq/on", 1),
        ("/main/st/eq/on", 0),
        ("/main/st/eq/1/type", PEQ),
        ("/main/st/eq/6/type", P.EQ_TYPES.index("HShv")),
        ("/main/st/eq/2/f", 0.46),
        ("/main/st/eq/2/q", 0.0),
        ("/main/st/eq/2/q", 1.0),
        ("/main/st/eq/2/g", gain(-15.0)),
        ("/main/st/eq/2/g", gain(3.0)),
    ],
)
def test_main_scope_allows_eq_parameters(path, value):
    POLICY.validate(path, value, MAIN)


@pytest.mark.parametrize(
    "path",
    [
        "/main/st/eq", "/main/st/eq/", "/main/st/eq/0/g", "/main/st/eq/7/g", "/main/st/eq/2",
        "/main/st/eq/2/", "/main/st/eq/2/x", "/main/st/eq/2/gg", "/main/st/eq/2/g/",
        "/main/st/eq/22/g",
        "/main/st/eq/2/g\n", "\n/main/st/eq/2/g", " /main/st/eq/2/g", "/main/st/eq/2/g ",
        "main/st/eq/2/g", "//main/st/eq/2/g", "/main/m/eq/2/g", "/main/st/eq/on/", "/main/st/eq/ON",
        "/main/st/mix/on", "/main/st/mix/fader", "/main/st/dyn/on", "/main/st/insert/on",
        "/mtx/03/eq/2/g", "/bus/01/eq/2/g", "/ch/01/eq/2/g", "/x/main/st/eq/2/g", "",
    ],
)  # fmt: skip
def test_main_scope_refuses_neighbouring_paths(path):
    with pytest.raises(UnsafeWrite):
        POLICY.validate(path, 0.5, MAIN)


@pytest.mark.parametrize(
    "value",
    [math.nan, math.inf, -math.inf, -0.0001, 1.0001, 2, 0, 1, True, False, "0.5", None, b"\0"],
)
def test_main_scope_refuses_bad_normalized_values(value):
    with pytest.raises(UnsafeWrite):
        POLICY.validate("/main/st/eq/2/f", value, MAIN)


@pytest.mark.parametrize("value", [-1, 14, 99, 2.0, True, False, "2", None, math.nan])
def test_main_scope_refuses_bad_type_values(value):
    with pytest.raises(UnsafeWrite):
        POLICY.validate("/main/st/eq/2/type", value, MAIN)


@pytest.mark.parametrize("name", sorted(set(P.EQ_TYPES) - {"PEQ", "VEQ", "LShv", "HShv"}))
def test_main_scope_refuses_every_cut_filter_type(name):
    with pytest.raises(UnsafeWrite):
        POLICY.validate("/main/st/eq/1/type", P.EQ_TYPES.index(name), MAIN)


@pytest.mark.parametrize("value", [2, -1, 0.0, 1.0, 0.5, "1", None, math.nan])
def test_main_scope_refuses_bad_switch_values(value):
    with pytest.raises(UnsafeWrite):
        POLICY.validate("/main/st/eq/on", value, MAIN)


@pytest.mark.parametrize("db", [3.25, 6.0, 15.0])
def test_main_scope_refuses_boost_above_limit(db):
    with pytest.raises(UnsafeWrite, match="boost"):
        POLICY.validate("/main/st/eq/2/g", gain(db), MAIN)


# ------------------------------------------------------------ sub scope, added
@pytest.mark.parametrize("matrix", sorted(MATRICES))
def test_sub_scope_allows_eq_of_every_venue_matrix(matrix):
    POLICY.validate(f"/mtx/0{matrix}/eq/2/g", gain(-6.0), SUB)
    POLICY.validate(f"/mtx/0{matrix}/eq/2/g", gain(6.0), SUB)


def test_sub_scope_matrices_come_from_the_venue():
    only_sub = WritePolicy(X32ValueLaws(), frozenset({3}), allow_unverified_writes=True)

    only_sub.validate("/mtx/03/eq/2/g", 0.5, SUB)
    only_sub.validate("/mtx/03/mix/on", 0, SUB)
    for path, value in [
        ("/mtx/01/eq/2/g", 0.5),
        ("/mtx/01/mix/on", 0),
        ("/mtx/01/mix/fader", 0.2),
        ("/mtx/01/preamp/invert", 1),
    ]:
        with pytest.raises(UnsafeWrite, match="not in the sub scope"):
            only_sub.validate(path, value, SUB)


@pytest.mark.parametrize("kind", ["mix/on", "mix/fader", "preamp/invert", "eq/1/g"])
def test_unused_matrix_4_is_never_written(kind):
    for value in (0, 0.2):
        with pytest.raises(UnsafeWrite):
            UNVERIFIED_OK.validate(f"/mtx/04/{kind}", value, SUB)


@pytest.mark.parametrize(
    "path",
    [
        "/mtx/00/eq/1/g", "/mtx/07/eq/1/g", "/mtx/3/eq/1/g", "/mtx/003/eq/1/g", "/mtx/03/eq/0/g",
        "/mtx/03/eq/7/g", "/mtx/03/eq/1", "/mtx/03/eq/on", "/mtx/03/eq/1/g\n", "/mtx/03/eq/1/g/",
        "mtx/03/eq/1/g", "/mtx/03/mix", "/mtx/03/mix/", "/mtx/03/mix/on\n", "/mtx/03/mix/pan",
        "/mtx/03/mix/01/on", "/mtx/07/mix/on", "/mtx/03/preamp/trim", "/mtx/03/preamp",
        "/mtx/03/config/name", "/mtx/03/dyn/on", "/mtx/03/insert/on",
        "/outputs/main/00/delay/on", "/outputs/main/17/delay/on", "/outputs/main/6/delay/on",
        "/outputs/main/06/delay", "/outputs/main/06/delay/time\n", "/outputs/main/06/src",
        "/outputs/main/06/pos", "/outputs/main/06/invert", "/outputs/aux/01/delay/on",
        "/outputs/p16/01/delay/on", "/main/st/eq/2/g", "/main/st/mix/on", "/main/st/mix/fader",
        "/main/m/mix/fader", "/bus/01/mix/on", "/bus/01/mix/fader", "/ch/01/mix/on",
        "/config/routing/CARD", "/headamp/041/gain", "/-action/goscene", "/save", "/-snap/load",
        "/fx/1/par/01", "/-prefs/card/UREC", "",
    ],
)  # fmt: skip
def test_sub_scope_refuses_neighbouring_paths(path):
    for value in (0, 1, 0.5):
        with pytest.raises(UnsafeWrite):
            UNVERIFIED_OK.validate(path, value, SUB)


@pytest.mark.parametrize(
    "path",
    ["/mtx/03/eq/2/g", "/mtx/03/eq/2/f", "/mtx/03/mix/fader", "/outputs/main/06/delay/time"],
)
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, -0.01, 1.01, 1, 0, True, "0.5"])
def test_sub_scope_refuses_bad_normalized_values(path, value):
    with pytest.raises(UnsafeWrite):
        UNVERIFIED_OK.validate(path, value, SUB)


@pytest.mark.parametrize(
    "path", ["/mtx/03/mix/on", "/mtx/03/preamp/invert", "/outputs/main/06/delay/on"]
)
@pytest.mark.parametrize("value", [2, -1, 0.0, 1.0, "1", None, math.nan])
def test_sub_scope_refuses_bad_switch_values(path, value):
    with pytest.raises(UnsafeWrite):
        UNVERIFIED_OK.validate(path, value, SUB)


@pytest.mark.parametrize("band", [2, 3, 4, 5])
@pytest.mark.parametrize("name", ["LCut", "HCut", "BU6", "BU12", "BS12", "LR12", "BU18", "LR24"])
def test_sub_scope_refuses_cut_filters_on_middle_bands(band, name):
    with pytest.raises(UnsafeWrite):
        POLICY.validate(f"/mtx/03/eq/{band}/type", P.EQ_TYPES.index(name), SUB)


def test_sub_scope_band_1_takes_high_pass_types_only_and_band_6_low_pass_types_only():
    lcut, hcut = P.EQ_TYPES.index("LCut"), P.EQ_TYPES.index("HCut")

    POLICY.validate("/mtx/01/eq/1/type", lcut, SUB)
    POLICY.validate("/mtx/03/eq/6/type", hcut, SUB)
    with pytest.raises(UnsafeWrite):
        POLICY.validate("/mtx/01/eq/1/type", hcut, SUB)
    with pytest.raises(UnsafeWrite):
        POLICY.validate("/mtx/03/eq/6/type", lcut, SUB)


@pytest.mark.parametrize("value", [-1, 14, 13.0, True, "13", None])
def test_sub_scope_refuses_bad_type_values(value):
    with pytest.raises(UnsafeWrite):
        POLICY.validate("/mtx/03/eq/6/type", value, SUB)


def test_sub_scope_limits_are_inclusive_at_the_limit_and_refuse_one_step_above():
    one_gain_step = 1 / (P.GAIN_STEPS - 1)
    one_fader_step = 1 / (P.FADER_STEPS - 1)
    one_delay_step = 1 / (P.DELAY_STEPS - 1)
    at_limit = [
        ("/mtx/03/eq/2/g", gain(6.0), one_gain_step),
        ("/mtx/03/mix/fader", P.quantize_fader(P.fader_to_norm(0.0)), one_fader_step),
        (
            "/outputs/main/06/delay/time",
            P.quantize_delay(P.delay_to_norm(50.0)),
            one_delay_step,
        ),
    ]
    for path, x_norm, step in at_limit:
        UNVERIFIED_OK.validate(path, x_norm, SUB)
        with pytest.raises(UnsafeWrite):
            UNVERIFIED_OK.validate(path, x_norm + step, SUB)


def test_limits_are_configurable_and_default_to_the_proven_values():
    assert WriteLimits() == WriteLimits(3.0, 6.0, 0.0, 50.0)
    strict = WritePolicy(X32ValueLaws(), MATRICES, WriteLimits(sub_max_boost_db=0.0))

    with pytest.raises(UnsafeWrite):
        strict.validate("/mtx/03/eq/2/g", gain(0.25), SUB)


@pytest.mark.parametrize(
    "limits",
    [
        {"main_max_boost_db": 3.25},
        {"main_max_boost_db": 15.0},
        {"sub_max_boost_db": 6.25},
        {"sub_max_fader_db": 0.1},
        {"sub_max_fader_db": 10.0},
        {"sub_max_delay_ms": 50.1},
        {"sub_max_delay_ms": 500.0},
    ],
)
def test_limits_cannot_be_raised_above_the_ceilings(limits):
    with pytest.raises(ValueError, match="not above"):
        WriteLimits(**limits)


@pytest.mark.parametrize(
    "field", ["main_max_boost_db", "sub_max_boost_db", "sub_max_fader_db", "sub_max_delay_ms"]
)
@pytest.mark.parametrize("value", [math.nan, math.inf])
def test_limits_cannot_be_switched_off_with_nan_or_infinity(field, value):
    with pytest.raises(ValueError, match="finite"):
        WriteLimits(**{field: value})


def test_limits_can_be_tightened():
    limits = WriteLimits(
        main_max_boost_db=0.0, sub_max_boost_db=3.0, sub_max_fader_db=-10.0, sub_max_delay_ms=20.0
    )
    policy = WritePolicy(X32ValueLaws(), MATRICES, limits, allow_unverified_writes=True)

    for path, value, scope in [
        ("/main/st/eq/2/g", gain(0.25), MAIN),
        ("/mtx/03/eq/2/g", gain(3.25), SUB),
        ("/mtx/03/mix/fader", P.fader_to_norm(-9.0), SUB),
        ("/outputs/main/06/delay/time", P.delay_to_norm(21.0), SUB),
    ]:
        with pytest.raises(UnsafeWrite):
            policy.validate(path, value, scope)


def test_unknown_scope_is_refused():
    with pytest.raises(UnsafeWrite, match="unknown scope"):
        POLICY.validate("/main/st/eq/2/g", 0.5, "everything")  # type: ignore[arg-type]


def test_refusal_carries_the_path():
    with pytest.raises(UnsafeWrite) as excinfo:
        POLICY.validate("/ch/10/mix/on", 1, SUB)

    assert excinfo.value.context == {"path": "/ch/10/mix/on"}
    assert excinfo.value.i18n_key == "err-unsafe-write"


def test_checked_change_returns_the_change_or_refuses():
    good = Change("/mtx/03/mix/fader", 0.2, P.fader_to_norm(-29.3), "-37.5", "-29.3")
    bad = Change("/mtx/03/mix/fader", 0.2, P.fader_to_norm(5.0), "-37.5", "+5.0")

    assert POLICY.checked_change(good, SUB) is good
    with pytest.raises(UnsafeWrite):
        POLICY.checked_change(bad, SUB)


# ------------------------------------------------------------ properties
ALL_ALLOWED = (MAIN_PATH_RE, SUB_EQ_PATH_RE, SUB_MATRIX_PATH_RE, SUB_DELAY_PATH_RE)
osc_path = st.from_regex(r"/[a-z0-9/_-]{0,30}", fullmatch=True)
any_value = st.one_of(
    st.floats(allow_nan=True, allow_infinity=True), st.integers(-5, 20), st.booleans()
)


@given(path=osc_path, value=any_value, scope=st.sampled_from([MAIN, SUB]))
def test_a_path_outside_the_allowlist_is_never_accepted(path, value, scope):
    if any(regex.fullmatch(path) for regex in ALL_ALLOWED):
        return
    with pytest.raises(UnsafeWrite):
        POLICY.validate(path, value, scope)


@given(
    path=st.one_of(*(st.from_regex(regex, fullmatch=True) for regex in ALL_ALLOWED)),
    value=any_value,
    scope=st.sampled_from([MAIN, SUB]),
)
def test_whatever_is_accepted_is_in_range_and_within_limits(path, value, scope):
    try:
        UNVERIFIED_OK.validate(path, value, scope)
    except UnsafeWrite:
        return
    kind = path.rsplit("/", 1)[1]
    assert not isinstance(value, bool)
    if kind in ("on", "invert"):
        assert value in (0, 1)
        assert isinstance(value, int)
    elif kind == "type":
        assert isinstance(value, int)
        assert 0 <= value < len(P.EQ_TYPES)
    else:
        assert isinstance(value, float)
        assert 0.0 <= value <= 1.0
    if kind == "g":
        assert P.norm_to_gain(value) <= (3.0 if scope is MAIN else 6.0) + 1e-6
    if kind == "fader":
        assert P.norm_to_fader(value) <= 0.0 + 1e-6
    if kind == "time":
        assert P.norm_to_delay(value) <= 50.0 + 1e-6
    if scope is MAIN:
        assert MAIN_PATH_RE.fullmatch(path)
    else:
        assert not MAIN_PATH_RE.fullmatch(path)
        matrix = SUB_EQ_PATH_RE.fullmatch(path) or SUB_MATRIX_PATH_RE.fullmatch(path)
        assert matrix is None or int(matrix.group(1)) in MATRICES


def test_allowlist_regexes_have_no_wildcards():
    for regex in ALL_ALLOWED:
        assert not re.search(r"\.[*+]|\\S|\\w", regex.pattern)
