"""S13 picks the points to measure again and draws the before/after picture."""

import numpy as np

from churchtune.measure.plan import plan_points
from churchtune.wizard.steps.verify import chart, verify_points
from tests.unit.analysis.test_verdict import analysis, bump

PLAN = plan_points(14.0, 22.0)


def baseline(worst: str) -> object:
    curves = {p: bump(1000, 1.0) for p in PLAN.ids}
    curves[worst] = bump(500, 9.0)
    return analysis(curves)


def test_the_worst_point_and_the_middle_are_measured_again_and_the_rest_spread_out():
    worst = PLAN.ids[-1]
    points = verify_points(baseline(worst), PLAN, mic_count=2)
    assert len(points) == 4
    assert worst in points
    assert list(points) == [p for p in PLAN.ids if p in points]  # walking order
    spots = [(PLAN.point(p).x_m, PLAN.point(p).y_m) for p in points]
    gaps = [np.hypot(a[0] - b[0], a[1] - b[1]) for i, a in enumerate(spots) for b in spots[i + 1 :]]
    assert min(gaps) > 2.0


def test_as_many_points_as_microphones_for_one_round():
    assert len(verify_points(baseline(PLAN.ids[0]), PLAN, mic_count=6)) == 6


def test_the_chart_has_two_rows_one_column_per_third_octave():
    before = baseline(PLAN.ids[0])
    rows = chart(before, before).splitlines()
    assert rows[0].startswith("1 ")
    assert rows[1].startswith("2 ")
    assert rows[0] == "1 " + rows[1][2:]  # the same response draws the same row
