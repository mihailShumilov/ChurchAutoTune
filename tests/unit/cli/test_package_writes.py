"""The plan shown and a plan made again from a fresh read must be the same on the console's grid."""

import pytest

from churchtune.cli.package_writes import plan_differences, require_same_plan
from churchtune.domain.changes import Change, WriteScope
from churchtune.errors import UnsafeWrite
from churchtune.mixers.x32 import params as P  # noqa: N812 - short alias as elsewhere
from churchtune.mixers.x32.plan_writes import PlannedChange

GAIN = P.quantize_gain(P.gain_to_norm(-6.0))


def change(path="/main/st/eq/3/g", old=0.5, new=GAIN, scope=WriteScope.SYSTEM_EQ):
    return PlannedChange(scope, Change(path, old, new, "0", "-6"))


def test_the_same_plan_on_the_grid_is_accepted():
    nearly = change(new=GAIN + 1e-6)
    assert plan_differences([change()], [nearly]) == ()
    require_same_plan([change()], [nearly])


def test_another_value_is_a_difference():
    other = change(new=P.quantize_gain(P.gain_to_norm(-3.0)))
    assert plan_differences([change()], [other]) == ("/main/st/eq/3/g",)


def test_another_set_of_writes_is_a_difference():
    extra = change(path="/main/st/eq/4/g")
    assert plan_differences([change()], [change(), extra]) == (
        "/main/st/eq/3/g",
        "/main/st/eq/4/g",
    )
    assert plan_differences([change()], [change(scope=WriteScope.LEVELS)]) != ()


def test_a_difference_is_refused_with_the_writes_named():
    with pytest.raises(UnsafeWrite, match="changed while the plan was shown") as refused:
        require_same_plan([change()], [])
    assert refused.value.context["mismatches"] == "/main/st/eq/3/g"
