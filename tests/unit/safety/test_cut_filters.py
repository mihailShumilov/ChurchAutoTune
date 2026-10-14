"""Where a plan may touch a cut filter (SPEC §9.2). Never weaken, skip or xfail these tests."""

import pytest

from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.errors import UnsafeWrite
from churchtune.safety.cut_filters import PathRole, check_cut_changes

FREQS = (60.0, 120.0, 250.0, 480.0, 1000.0, 2000.0)


def flat() -> SystemEq:
    return SystemEq(True, tuple(Band(n, EqType.PEQ, FREQS[n - 1], 0.0, 1.4) for n in range(1, 7)))


def same(a: Band, b: Band) -> bool:
    return a == b


HPF = flat().with_band(Band(1, EqType.LR24, 100.0, 0.0, 1.4))
LPF = flat().with_band(Band(6, EqType.LR24, 100.0, 0.0, 1.4))


def test_the_crossover_sets_the_tops_high_pass_and_the_sub_low_pass():
    check_cut_changes("matrix 1", PathRole.TOPS, flat(), HPF, same, may_remove=False)
    check_cut_changes("matrix 3", PathRole.SUB, flat(), LPF, same, may_remove=False)
    moved = HPF.with_band(Band(1, EqType.LR24, 120.0, 0.0, 1.4))
    check_cut_changes("matrix 1", PathRole.TOPS, HPF, moved, same, may_remove=False)


@pytest.mark.parametrize(
    ("role", "now", "wanted", "match"),
    [
        (PathRole.MAIN, flat(), HPF, "may not change"),  # never on the common path
        (PathRole.OTHER, flat(), HPF, "may not change"),
        (PathRole.TOPS, flat(), LPF, "may not change"),  # band 6 is the sub's
        (PathRole.SUB, flat(), HPF, "may not change"),
        (PathRole.TOPS, HPF, flat(), "removed"),
        (PathRole.TOPS, flat(), flat().with_band(Band(1, EqType.LR24, 1490.0, 0.0, 1.4)),
         "outside"),
        (PathRole.SUB, flat(), flat().with_band(Band(6, EqType.LR24, 20.0, 0.0, 1.4)),
         "outside"),
    ],
)  # fmt: skip
def test_everything_else_is_refused(role, now, wanted, match):
    with pytest.raises(UnsafeWrite, match=match):
        check_cut_changes("strip", role, now, wanted, same, may_remove=False)


def test_only_an_undo_may_remove_a_cut_and_only_in_its_place():
    check_cut_changes("matrix 1", PathRole.TOPS, HPF, flat(), same, may_remove=True)
    with pytest.raises(UnsafeWrite, match="may not change"):
        check_cut_changes("main_l", PathRole.MAIN, HPF, flat(), same, may_remove=True)
