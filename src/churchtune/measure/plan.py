"""The measurement point plan (SPEC §8.3, S10).

Points lie in the listening area: ``x`` across the hall from the left wall as the audience sees
it (facing the stage), ``y`` from the stage edge towards the back. The rules:

* at least eight points, in the middle of the depth: the front and back 20 % are left out (they
  would be control points, not part of the average);
* at least 1 m from the side walls, more when the hall is wide enough;
* never exactly on the centre line (the symmetry axis) and never on the same spot twice;
* irregular spacing: a Halton sequence (bases 2 and 3) instead of a grid;
* four of them double as junction points (sub/tops): one near the middle of the seats, three
  spread across width and depth.

The order of the points keeps walking short: row by row, alternating direction.
"""

import math
from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

DEFAULT_WIDTH_M = 12.0
DEFAULT_DEPTH_M = 18.0
MIN_POINTS = 8
EDGE_SHARE = 0.2  # front and back share of the depth that is left out
MIN_WALL_M = 1.0
PREFERRED_WALL_M = 2.5
WALL_SHARE = 0.15  # the wall margin grows with the width up to PREFERRED_WALL_M
CENTRE_KEEP_OUT_M = 1.0  # a point in the centre aisle would stand on the symmetry axis
MIN_SPACING_M = 1.0
JUNCTION_POINTS = 4
MAP_COLUMNS = 40
MAP_ROWS = 12
STAGE_MARK = "█"
OTHER_MARK = "·"
X_SHIFT = 0.381966  # 2 - golden ratio: breaks the mirror symmetry of base-2 Halton
Row = Literal["front", "middle", "back"]
Side = Literal["left", "centre", "right"]
ROWS: tuple[Row, Row, Row] = ("front", "middle", "back")
SIDES: tuple[Side, Side, Side] = ("left", "centre", "right")


@dataclass(frozen=True, slots=True)
class PlanPoint:
    """One measurement position.

    Attributes:
        id: Stable id (``p1``...), the order of the walk.
        x_m: Across the hall from the left wall (as the audience sees it), metres.
        y_m: From the stage edge towards the back, metres.
        row: ``front`` / ``middle`` / ``back`` third of the hall's depth.
        side: ``left`` / ``centre`` / ``right`` third of the hall's width (facing the stage).
        junction: Also a junction (sub/tops) point.
    """

    id: str
    x_m: float
    y_m: float
    row: Row
    side: Side
    junction: bool = False


@dataclass(frozen=True, slots=True)
class PointPlan:
    """A plan for one hall.

    Attributes:
        width_m: Width of the listening area.
        depth_m: Depth of the listening area.
        points: In walking order.
        from_template: True when the hall size was not given and a standard size was used.
    """

    width_m: float
    depth_m: float
    points: tuple[PlanPoint, ...]
    from_template: bool = False

    @property
    def ids(self) -> tuple[str, ...]:
        """Point ids in walking order."""
        return tuple(p.id for p in self.points)

    @property
    def junction_ids(self) -> tuple[str, ...]:
        """Ids of the junction points."""
        return tuple(p.id for p in self.points if p.junction)

    def from_stage_m(self, point_id: str) -> float:
        """Distance of a point from the stage edge, metres."""
        return self.point(point_id).y_m

    def from_centre_m(self, point_id: str) -> float:
        """Distance of a point from the centre line (positive to the right), metres, to 0.5 m."""
        return round((self.point(point_id).x_m - self.width_m / 2) * 2) / 2

    def point(self, point_id: str) -> PlanPoint:
        """One point by id.

        Raises:
            KeyError: No such point.
        """
        for p in self.points:
            if p.id == point_id:
                return p
        raise KeyError(point_id)


def halton(index: int, base: int) -> float:
    """The ``index``-th element (1-based) of the Halton sequence in ``base``, in [0, 1)."""
    result, fraction = 0.0, 1.0
    while index > 0:
        fraction /= base
        result += fraction * (index % base)
        index //= base
    return result


def _area(width_m: float, depth_m: float) -> tuple[float, float, float, float]:
    wall = min(PREFERRED_WALL_M, max(MIN_WALL_M, WALL_SHARE * width_m))
    return wall, width_m - wall, EDGE_SHARE * depth_m, (1 - EDGE_SHARE) * depth_m


def _third[T: str](value: float, low: float, high: float, names: tuple[T, T, T]) -> T:
    share = (value - low) / max(high - low, 1e-9)
    return names[min(int(share * 3), 2)]


def _junction_pick(points: list[tuple[float, float]], count: int) -> set[int]:
    """Indices of the junction points.

    One near the middle of the seats (where most people sit, so the alignment compromise holds
    there), the others as far from each other as possible.
    """
    if len(points) <= count:
        return set(range(len(points)))
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    chosen = [min(range(len(points)), key=lambda i: math.dist(points[i], (cx, cy)))]
    while len(chosen) < count:
        rest = [i for i in range(len(points)) if i not in chosen]
        chosen.append(max(rest, key=lambda i: min(math.dist(points[i], points[j]) for j in chosen)))
    return set(chosen)


def plan_points(
    width_m: float | None = None, depth_m: float | None = None, count: int = MIN_POINTS
) -> PointPlan:
    """The plan for a hall; a standard 12 × 18 m hall when the size is unknown.

    Raises:
        ValueError: Fewer than eight points asked for, a size that is not positive, or a hall
            too small for the points.
    """
    if count < MIN_POINTS:
        raise ValueError(f"a plan needs at least {MIN_POINTS} points")
    template = width_m is None or depth_m is None
    width = DEFAULT_WIDTH_M if width_m is None else width_m
    depth = DEFAULT_DEPTH_M if depth_m is None else depth_m
    if width <= 2 * MIN_WALL_M or depth <= 0:
        raise ValueError("the hall is too small for this many points")
    x0, x1, y0, y1 = _area(width, depth)
    centre = width / 2
    found: list[tuple[float, float]] = []
    index = 0
    while len(found) < count:
        index += 1
        if index > count * 200:
            raise ValueError("the hall is too small for this many points")
        # Shifted so the points are not mirror pairs about the centre line (symmetric speakers
        # would give the same response twice).
        x = round(x0 + ((halton(index, 2) + X_SHIFT) % 1.0) * (x1 - x0), 1)
        y = round(y0 + halton(index, 3) * (y1 - y0), 1)
        if abs(x - centre) < CENTRE_KEEP_OUT_M:
            continue
        if any(math.dist((x, y), p) < MIN_SPACING_M for p in found):
            continue
        found.append((x, y))
    junction = _junction_pick(found, JUNCTION_POINTS)
    rows = sorted(range(len(found)), key=lambda i: found[i][1])
    walk: list[int] = []
    for n, start in enumerate(range(0, len(rows), 3)):  # rows of three, alternating direction
        row = sorted(rows[start : start + 3], key=lambda i: found[i][0], reverse=n % 2 == 1)
        walk.extend(row)
    points = tuple(
        PlanPoint(
            id=f"p{order}",
            x_m=found[i][0],
            y_m=found[i][1],
            row=_third(found[i][1], 0.0, depth, ROWS),
            side=_third(found[i][0], 0.0, width, SIDES),
            junction=i in junction,
        )
        for order, i in enumerate(walk, start=1)
    )
    return PointPlan(width, depth, points, template)


def ascii_map(plan: PointPlan, highlight: Collection[str] = ()) -> str:
    """A plan of the hall in monospace text, without words.

    The stage is the solid bar on top; each point is its number. With ``highlight``, only those
    points show their number and the others are dots.
    """
    grid = [[" "] * MAP_COLUMNS for _ in range(MAP_ROWS)]
    for point in plan.points:
        col = min(MAP_COLUMNS - 3, int(point.x_m / plan.width_m * (MAP_COLUMNS - 1)))
        row = min(MAP_ROWS - 1, int(point.y_m / plan.depth_m * (MAP_ROWS - 1)))
        mark = point.id[1:] if not highlight or point.id in highlight else OTHER_MARK
        for k, ch in enumerate(mark):
            grid[row][col + k] = ch
    top = "+" + STAGE_MARK * MAP_COLUMNS + "+"
    body = ["|" + "".join(line) + "|" for line in grid]
    bottom = "+" + "-" * MAP_COLUMNS + "+"
    return "\n".join([top, *body, bottom])
