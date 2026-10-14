"""The point plan follows SPEC §8.3."""

import itertools
import math

import pytest

from churchtune.measure.plan import (
    CENTRE_KEEP_OUT_M,
    EDGE_SHARE,
    MIN_SPACING_M,
    MIN_WALL_M,
    ascii_map,
    halton,
    plan_points,
)


def test_halton_is_the_radical_inverse():
    assert [halton(i, 2) for i in range(1, 5)] == [0.5, 0.25, 0.75, 0.125]
    assert halton(1, 3) == pytest.approx(1 / 3)


@pytest.mark.parametrize(("width", "depth"), [(14.0, 22.0), (8.0, 10.0), (30.0, 40.0)])
def test_points_respect_the_rules(width, depth):
    plan = plan_points(width, depth)
    assert len(plan.points) == 8
    assert not plan.from_template
    for p in plan.points:
        assert MIN_WALL_M <= p.x_m <= width - MIN_WALL_M
        assert EDGE_SHARE * depth - 0.1 <= p.y_m <= (1 - EDGE_SHARE) * depth + 0.1
        assert abs(p.x_m - width / 2) >= CENTRE_KEEP_OUT_M - 0.05
    for a in plan.points:
        for b in plan.points:
            if a is not b:
                assert math.dist((a.x_m, a.y_m), (b.x_m, b.y_m)) >= MIN_SPACING_M - 0.15
    assert len(plan.junction_ids) == 4
    assert plan.ids == tuple(f"p{n}" for n in range(1, 9))


def test_junction_points_are_spread_across_width_and_depth():
    plan = plan_points(14.0, 22.0)
    junction = [plan.point(i) for i in plan.junction_ids]
    assert max(p.x_m for p in junction) - min(p.x_m for p in junction) > 4.0
    assert max(p.y_m for p in junction) - min(p.y_m for p in junction) > 4.0


def test_the_walk_goes_row_by_row():
    plan = plan_points(14.0, 22.0)
    depths = [p.y_m for p in plan.points]
    rows = [depths[i : i + 3] for i in range(0, 8, 3)]
    for earlier, later in itertools.pairwise(rows):
        assert max(earlier) <= min(later)


def test_unknown_size_uses_a_template_and_labels_every_third():
    plan = plan_points()
    assert plan.from_template
    assert {p.row for p in plan.points} <= {"front", "middle", "back"}
    assert {p.side for p in plan.points} <= {"left", "centre", "right"}
    assert len({p.row for p in plan.points}) == 3


def test_more_points_and_refusals():
    assert len(plan_points(20.0, 30.0, count=12).points) == 12
    with pytest.raises(ValueError, match="at least"):
        plan_points(14.0, 22.0, count=6)
    with pytest.raises(ValueError, match="too small"):
        plan_points(3.0, 3.0, count=40)
    with pytest.raises(KeyError):
        plan_points().point("p99")


def test_ascii_map_shows_the_stage_and_every_point():
    plan = plan_points(14.0, 22.0)
    picture = ascii_map(plan)
    lines = picture.splitlines()
    assert set(lines[0][1:-1]) == {"█"}
    assert not any(ch.isalpha() for ch in picture)
    assert len({len(line) for line in lines}) == 1
    body = "".join(lines[1:-1])
    for n in range(1, 9):
        assert str(n) in body


def test_one_junction_point_sits_near_the_middle_of_the_seats():
    plan = plan_points(14.0, 22.0)
    cx = sum(p.x_m for p in plan.points) / 8
    cy = sum(p.y_m for p in plan.points) / 8
    nearest = min(plan.points, key=lambda p: math.dist((p.x_m, p.y_m), (cx, cy)))
    assert nearest.junction


def test_labels_and_distances_are_about_the_whole_hall():
    plan = plan_points(14.0, 22.0)
    for p in plan.points:
        third = min(int(p.y_m / 22.0 * 3), 2)
        assert p.row == ("front", "middle", "back")[third]
        assert plan.from_stage_m(p.id) == p.y_m
        assert plan.from_centre_m(p.id) == pytest.approx(p.x_m - 7.0, abs=0.26)


def test_no_mirror_pairs_about_the_centre_line():
    plan = plan_points(14.0, 22.0)
    offsets = sorted(round(p.x_m - plan.width_m / 2, 1) for p in plan.points)
    mirrored = [o for o in offsets if -o in offsets]
    assert len(mirrored) <= 2


def test_the_map_can_highlight_one_round():
    plan = plan_points(14.0, 22.0)
    picture = ascii_map(plan, {"p3", "p4"})
    body = "".join(picture.splitlines()[1:-1])
    assert "3" in body
    assert "4" in body
    assert "·" in body
    assert "1" not in body


def test_sizes_that_cannot_hold_a_plan_are_refused():
    with pytest.raises(ValueError, match="too small"):
        plan_points(1.5, 10.0)
    with pytest.raises(ValueError, match="too small"):
        plan_points(10.0, 0.0)
