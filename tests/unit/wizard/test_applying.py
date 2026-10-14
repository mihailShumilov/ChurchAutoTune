"""A saved proposal becomes ordered steps only from a fresh read that still matches it."""

import math

import pytest

from churchtune.domain.console import ConsoleState, MatrixState
from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.domain.snapshot import SignalRef, SourceKind
from churchtune.errors import UnsafeWrite
from churchtune.mixers.x32.laws import X32ValueLaws
from churchtune.safety.cut_filters import PathRole
from churchtune.safety.order import check_order
from churchtune.storage.models import (
    EqModel,
    LevelModel,
    PackageModel,
    PackagesFile,
    PathEqModel,
    PathRefModel,
)
from churchtune.wizard.applying import (
    MAIN,
    check_eq_change,
    chosen_packages,
    combined_target,
    ordered_steps,
    package_target,
    same_eq,
    speaker_chains,
    strip_states,
    writable_packages,
)

FS = 48000.0
LAWS = X32ValueLaws()
SUB = SignalRef(SourceKind.MATRIX, 3)
FREQS = (60.0, 120.0, 250.0, 480.0, 1000.0, 2000.0)


def flat() -> SystemEq:
    return SystemEq(True, tuple(Band(n, EqType.PEQ, FREQS[n - 1], 0.0, 1.4) for n in range(1, 7)))


def console(main: SystemEq | None = None, sub_fader: float = -20.0) -> ConsoleState:
    matrices = {
        n: MatrixState(n, f"M{n}", flat(), True, sub_fader if n == 3 else 0.0, False, ())
        for n in (1, 2, 3)
    }
    return ConsoleState(main or flat(), matrices, {})


def eq_package(before: SystemEq, after: SystemEq, **extra) -> PackageModel:
    path = PathEqModel(
        path=PathRefModel(kind="main_l"),
        before=EqModel.from_domain(before),
        after=EqModel.from_domain(after),
    )
    fields = {"kind": "system_eq", "suggest_only": False, "risk": "low", "selected": True}
    return PackageModel(**{**fields, "eq": (path,), **extra})


def level_package(before_db: float, after_db: float) -> PackageModel:
    level = LevelModel(path=PathRefModel(kind="matrix", number=3), before_db=before_db,
                       after_db=after_db)  # fmt: skip
    return PackageModel(kind="sub_level", suggest_only=False, risk="low", selected=True,
                        levels=(level,))  # fmt: skip


def packages_file(*packages: PackageModel) -> PackagesFile:
    return PackagesFile.model_construct(packages=packages)


ROLES = {SignalRef(SourceKind.MATRIX, 1): PathRole.TOPS, SUB: PathRole.SUB}
CUT = flat().with_band(Band(4, EqType.PEQ, 480.0, -9.0, 1.5))


def test_strips_and_chains_cover_main_and_every_matrix():
    states = strip_states(console())
    assert set(states) == {MAIN, *(SignalRef(SourceKind.MATRIX, n) for n in (1, 2, 3))}
    assert states[SUB].fader_db == -20.0
    chains = speaker_chains(states)
    assert chains["main"] == (MAIN,)
    assert chains["matrix 3"] == (MAIN, SUB)


def test_a_matching_package_becomes_ordered_steps():
    fresh = strip_states(console())
    target = package_target(eq_package(flat(), CUT), fresh, LAWS, ROLES)
    assert target.mismatches == ()
    steps = ordered_steps(target, fresh, FS)
    assert {step.strip for step in steps} == {MAIN}
    whole_after = {**fresh, **target.after}
    check_order(fresh, steps, whole_after, speaker_chains(fresh), FS)


def test_a_console_changed_by_hand_is_refused():
    fresh = strip_states(console(main=CUT))  # someone already cut 480 Hz
    target = package_target(eq_package(flat(), CUT), fresh, LAWS, ROLES)
    assert target.mismatches == ("main_l",)
    with pytest.raises(UnsafeWrite, match="changed since"):
        ordered_steps(target, fresh, FS)


def test_a_fader_is_compared_on_the_console_grid():
    fresh = strip_states(console(sub_fader=-19.98))
    assert package_target(level_package(-20.0, -26.0), fresh, LAWS, ROLES).mismatches == ()
    moved = strip_states(console(sub_fader=-15.0))
    assert package_target(level_package(-20.0, -26.0), moved, LAWS, ROLES).mismatches == (
        "matrix 3",
    )


def test_a_fader_down_matches_minus_infinity():
    fresh = strip_states(console(sub_fader=-math.inf))
    assert package_target(level_package(-math.inf, -30.0), fresh, LAWS, ROLES).mismatches == ()


def test_a_strip_the_console_does_not_have_is_refused():
    level = LevelModel(path=PathRefModel(kind="matrix", number=6), before_db=0.0, after_db=-3.0)
    package = PackageModel(kind="sub_level", suggest_only=False, risk="low", selected=True,
                           levels=(level,))  # fmt: skip
    with pytest.raises(UnsafeWrite, match="no matrix 6"):
        package_target(package, strip_states(console()), LAWS, ROLES)


def test_only_chosen_packages_that_are_not_suggestions():
    chosen = eq_package(flat(), CUT)
    skipped = eq_package(flat(), CUT, selected=False)
    suggestion = eq_package(flat(), CUT, suggest_only=True)
    assert chosen_packages(packages_file(skipped, suggestion, chosen)) == [chosen]


def test_a_package_needs_what_it_requires_first():
    needs = eq_package(flat(), CUT, requires=("crossover",))
    with pytest.raises(UnsafeWrite, match="needs crossover"):
        chosen_packages(packages_file(needs))


def test_same_eq_uses_the_grid_and_the_switch():
    assert same_eq(LAWS, flat(), flat().with_band(Band(4, EqType.PEQ, 480.0, 0.01, 1.4)))
    assert not same_eq(LAWS, flat(), flat().with_on(False))
    assert not same_eq(LAWS, flat(), CUT)


def test_packages_stack_on_each_other():
    first_cut = flat().with_band(Band(1, EqType.PEQ, 60.0, -3.0, 1.4))
    first = eq_package(flat(), first_cut, kind="crossover")
    second = eq_package(first_cut, CUT.with_band(first_cut.band(1)))
    fresh = strip_states(console())
    target = combined_target([first, second], fresh, LAWS, ROLES)
    assert target.mismatches == ()
    assert target.kind == "crossover+system_eq"
    assert target.before[MAIN] == fresh[MAIN]
    assert target.after[MAIN].eq.band(4).gain_db == -9.0
    stale = combined_target(
        [second], fresh, LAWS, ROLES
    )  # the EQ alone expects the crossover first
    assert stale.mismatches == ("system_eq: main_l",)


def test_the_crossover_never_touches_a_cut_on_main_lr():
    hpf = flat().with_band(Band(1, EqType.LCUT, 100.0, 0.0, 1.4))
    with pytest.raises(UnsafeWrite, match="may not change"):
        combined_target(
            [eq_package(flat(), hpf, kind="crossover")], strip_states(console()), LAWS, ROLES
        )


def test_a_package_never_switches_an_eq_on_or_off():
    with pytest.raises(UnsafeWrite, match="on or off"):
        check_eq_change("system_eq", "main_l", PathRole.MAIN, flat().with_on(False), flat(), LAWS)


def test_the_crossover_package_may_set_a_cut_filter():
    crossover = flat().with_band(Band(1, EqType.LCUT, 100.0, 0.0, 1.4))
    check_eq_change("crossover", "matrix 1", PathRole.TOPS, flat(), crossover, LAWS)
    with pytest.raises(UnsafeWrite, match="only the crossover"):
        check_eq_change("system_eq", "matrix 1", PathRole.TOPS, flat(), crossover, LAWS)


def test_advice_packages_and_what_needs_them_are_not_written():
    align = PackageModel(kind="alignment", suggest_only=False, risk="unverified", selected=True,
                         delay_ms=1.0)  # fmt: skip
    level = level_package(-20.0, -23.0)
    needs = eq_package(flat(), CUT, requires=("alignment",))
    kept, advice = writable_packages([align, level, needs])
    assert kept == [level]
    assert advice == [align]
