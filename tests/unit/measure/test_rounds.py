import pytest

from churchtune.measure.rounds import plan_rounds

POINTS = [f"p{i}" for i in range(1, 9)]


def test_one_mic_takes_one_round_per_point():
    rounds = plan_rounds(POINTS, [10])
    assert len(rounds) == 8
    assert rounds[2].placements[0].point == "p3"


def test_four_mics_take_two_rounds():
    rounds = plan_rounds(POINTS, [10, 11, 12, 13])
    assert [r.number for r in rounds] == [1, 2]
    assert rounds[1].inputs == (10, 11, 12, 13)
    assert [p.point for p in rounds[1].placements] == ["p5", "p6", "p7", "p8"]


def test_the_last_round_may_be_short():
    rounds = plan_rounds(POINTS[:5], [10, 11, 12])
    assert rounds[-1].inputs == (10, 11)


@pytest.mark.parametrize(
    ("points", "mics"), [([], [10]), (["p1"], []), (["p1", "p1"], [10]), (["p1"], [10, 10])]
)
def test_bad_plans_are_refused(points, mics):
    with pytest.raises(ValueError, match=r"point|microphone|unique"):
        plan_rounds(points, mics)
