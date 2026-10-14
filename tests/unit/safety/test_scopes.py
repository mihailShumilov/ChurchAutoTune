"""The write scopes of SPEC §9.2 (M5): temp_mutes, system_eq, levels, delay, polarity.

Never weaken, skip or xfail these tests. Each scope accepts its paths within its limits and
refuses neighbours, other matrices, hostile values (NaN, inf, bool, wrong types) and anything
the scope does not name.
"""

import math

import pytest

from churchtune.domain.changes import WriteScope
from churchtune.errors import UnsafeWrite
from churchtune.mixers.x32 import params as P  # noqa: N812 - short alias kept from the original
from churchtune.mixers.x32.laws import X32ValueLaws
from churchtune.safety.policy import WritePolicy
from tests.fixtures.venues import MATRICES, SUB_MATRIX, TOP_MATRICES

TEMP, EQ, LEVELS, DELAY, POLARITY = (
    WriteScope.TEMP_MUTES,
    WriteScope.SYSTEM_EQ,
    WriteScope.LEVELS,
    WriteScope.DELAY,
    WriteScope.POLARITY,
)
LR24 = P.EQ_TYPES.index("LR24")
PEQ = P.EQ_TYPES.index("PEQ")
LCUT = P.EQ_TYPES.index("LCut")
HOSTILE = [math.nan, math.inf, -math.inf, True, False, "0", None, 1.5, -0.1]


def policy(**overrides) -> WritePolicy:
    base = {
        "laws": X32ValueLaws(),
        "matrices": MATRICES,
        "sub_matrices": frozenset({SUB_MATRIX}),
        "top_matrices": frozenset(TOP_MATRICES),
        "mute_originals": (("/mtx/01/mix/on", 1), ("/mtx/03/mix/on", 1), ("/mtx/05/mix/on", 0)),
        "level_baselines": (("/mtx/03/mix/fader", -20.0),),
    }
    base.update(overrides)
    return WritePolicy(**base)


POLICY = policy()


def gain(db):
    return P.quantize_gain(P.gain_to_norm(db))


def fader(db):
    return P.quantize_fader(P.fader_to_norm(db))


# ------------------------------------------------------------ temp_mutes
@pytest.mark.parametrize(
    ("path", "value"),
    [("/mtx/01/mix/on", 0), ("/mtx/01/mix/on", 1), ("/mtx/03/mix/on", 0), ("/mtx/05/mix/on", 0)],
)
def test_temp_mutes_accepts_a_mute_or_the_exact_original(path, value):
    POLICY.validate(path, value, TEMP)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("/mtx/05/mix/on", 1),  # its original was 0: switching it on is not a restore
        ("/mtx/02/mix/on", 0),  # no original recorded: not part of this mute window
        ("/mtx/04/mix/on", 0),  # not a venue matrix
        ("/ch/10/mix/on", 0),  # channel mutes are by hand (never verified on hardware)
        ("/bus/01/mix/on", 0),  # bus mutes are by hand (never verified on hardware)
        ("/main/st/mix/on", 0),  # Main mute is by hand (never verified on hardware)
        ("/mtx/01/mix/fader", 0.0),
        ("/mtx/01/mix/on/x", 0),
        ("/mtx/1/mix/on", 0),
    ],
)
def test_temp_mutes_refuses_anything_else(path, value):
    with pytest.raises(UnsafeWrite):
        POLICY.validate(path, value, TEMP)


@pytest.mark.parametrize("value", HOSTILE)
def test_temp_mutes_refuses_hostile_values(value):
    with pytest.raises(UnsafeWrite):
        POLICY.validate("/mtx/01/mix/on", value, TEMP)


# ------------------------------------------------------------ system_eq
@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("/main/st/eq/2/g", gain(-6.0)),
        ("/main/st/eq/2/g", gain(3.0)),
        ("/main/st/eq/2/type", PEQ),
        ("/main/st/eq/on", 1),
        ("/mtx/01/eq/3/g", gain(3.0)),
        ("/mtx/03/eq/3/g", gain(6.0)),  # the sub may get up to +6 dB
        ("/mtx/03/eq/4/f", 0.5),
        ("/mtx/02/eq/on", 1),
    ],
)
def test_system_eq_accepts_eq_within_limits(path, value):
    POLICY.validate(path, value, EQ)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("/main/st/eq/2/g", gain(3.5)),  # above +3 dB on Main
        ("/mtx/01/eq/3/g", gain(3.5)),  # above +3 dB on the tops
        ("/mtx/03/eq/3/g", gain(6.5)),  # above +6 dB on the sub
        ("/main/st/eq/on", 0),  # an EQ is never switched off
        ("/mtx/03/eq/on", 0),
        ("/main/st/eq/1/type", LR24),  # never a cut filter on the common path
        ("/main/st/eq/1/type", LCUT),
        ("/mtx/01/eq/1/type", LR24),  # cut filters only in the crossover package
        ("/mtx/04/eq/3/g", gain(0.0)),  # not a venue matrix
        ("/mtx/05/eq/3/g", gain(0.0)),  # balcony: not a sub or top path
        ("/main/st/eq/7/g", 0.5),
        ("/main/st/mix/fader", 0.5),
        ("/ch/01/eq/1/g", 0.5),
        ("/bus/01/eq/1/g", 0.5),  # bus EQ never verified (checklist row 8)
    ],
)
def test_system_eq_refuses_outside_its_limits(path, value):
    with pytest.raises(UnsafeWrite):
        POLICY.validate(path, value, EQ)


def test_crossover_filters_only_on_band_1_of_tops_and_band_6_of_the_sub():
    crossover = policy(allow_cut_filters=True)
    crossover.validate("/mtx/01/eq/1/type", LR24, EQ)
    crossover.validate("/mtx/03/eq/6/type", LR24, EQ)
    for path in ("/mtx/01/eq/6/type", "/mtx/03/eq/1/type", "/main/st/eq/1/type",
                 "/mtx/01/eq/3/type"):  # fmt: skip
        with pytest.raises(UnsafeWrite):
            crossover.validate(path, LR24, EQ)


@pytest.mark.parametrize("value", HOSTILE)
def test_system_eq_refuses_hostile_values(value):
    with pytest.raises(UnsafeWrite):
        POLICY.validate("/main/st/eq/2/g", value, EQ)


# ------------------------------------------------------------ levels
def test_levels_accepts_the_sub_fader_within_ten_db_and_never_above_zero():
    POLICY.validate("/mtx/03/mix/fader", fader(-28.0), LEVELS)
    POLICY.validate("/mtx/03/mix/fader", fader(-12.0), LEVELS)


@pytest.mark.parametrize(
    ("path", "level_db"),
    [
        ("/mtx/03/mix/fader", -9.0),  # +11 dB from the fresh read
        ("/mtx/03/mix/fader", -31.0),  # -11 dB
        ("/mtx/01/mix/fader", -20.0),  # not the sub path
        ("/main/st/mix/fader", -20.0),  # Main fader never
        ("/bus/01/mix/fader", -20.0),
    ],
)
def test_levels_refuses_big_steps_other_faders_and_main(path, level_db):
    with pytest.raises(UnsafeWrite):
        POLICY.validate(path, fader(level_db), LEVELS)


def test_levels_never_above_zero_db_and_needs_a_baseline():
    hot = policy(level_baselines=(("/mtx/03/mix/fader", -5.0),))
    with pytest.raises(UnsafeWrite):
        hot.validate("/mtx/03/mix/fader", fader(3.0), LEVELS)
    unknown = policy(level_baselines=())
    with pytest.raises(UnsafeWrite):
        unknown.validate("/mtx/03/mix/fader", fader(-20.0), LEVELS)


@pytest.mark.parametrize("value", HOSTILE)
def test_levels_refuses_hostile_values(value):
    with pytest.raises(UnsafeWrite):
        POLICY.validate("/mtx/03/mix/fader", value, LEVELS)


# ------------------------------------------------------------ delay and polarity
def test_delay_and_polarity_need_the_unverified_opt_in():
    with pytest.raises(UnsafeWrite):
        POLICY.validate("/outputs/main/08/delay/time", P.delay_to_norm(5.0), DELAY)
    with pytest.raises(UnsafeWrite):
        POLICY.validate("/mtx/03/preamp/invert", 1, POLARITY)
    opted = policy(allow_unverified_writes=True)
    opted.validate("/outputs/main/08/delay/time", P.delay_to_norm(5.0), DELAY)
    opted.validate("/outputs/main/08/delay/on", 1, DELAY)
    opted.validate("/mtx/03/preamp/invert", 1, POLARITY)


@pytest.mark.parametrize(
    ("path", "value", "scope"),
    [
        ("/outputs/main/08/delay/time", P.delay_to_norm(60.0), DELAY),  # above 50 ms
        ("/outputs/main/17/delay/time", 0.1, DELAY),
        ("/outputs/aux/01/delay/time", 0.1, DELAY),
        ("/mtx/01/preamp/invert", 1, POLARITY),  # polarity of the sub path only
        ("/mtx/03/preamp/invert", 2, POLARITY),
        ("/mtx/03/mix/on", 0, POLARITY),
    ],
)
def test_delay_and_polarity_limits(path, value, scope):
    opted = policy(allow_unverified_writes=True)
    with pytest.raises(UnsafeWrite):
        opted.validate(path, value, scope)


@pytest.mark.parametrize("scope", [TEMP, EQ, LEVELS, DELAY, POLARITY])
def test_no_new_scope_reaches_forbidden_paths(scope):
    opted = policy(allow_unverified_writes=True, allow_cut_filters=True)
    for path in ("/config/routing/OUT/1-4", "/headamp/041/phantom", "/-action/goscene",
                 "/main/st/mix/fader", "/main/m/mix/fader", "/fx/1/type", "/-prefs/name"):  # fmt: skip
        with pytest.raises(UnsafeWrite):
            opted.validate(path, 0, scope)


def test_policies_reject_overlapping_or_unknown_sub_and_top_matrices():
    with pytest.raises(ValueError, match="both"):
        policy(top_matrices=frozenset({1, SUB_MATRIX}))
    with pytest.raises(ValueError, match="not venue"):
        policy(sub_matrices=frozenset({4}))
