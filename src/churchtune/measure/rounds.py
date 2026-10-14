"""Rounds of a multi-microphone measurement (SPEC S10).

With K microphones and N points, every round places the K microphones at the next K points of the
plan and measures them with one sweep: ``ceil(N / K)`` rounds. The last round may use fewer
microphones. Between rounds the wizard says "move microphones 1-4 to points 5-8".
"""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Placement:
    """One microphone at one point.

    Attributes:
        usb_input: 1-based USB input of the microphone.
        point: Point id from the plan.
    """

    usb_input: int
    point: str


@dataclass(frozen=True, slots=True)
class Round:
    """One sweep with several microphones.

    Attributes:
        number: 1-based round number.
        placements: Where each microphone stands in this round.
    """

    number: int
    placements: tuple[Placement, ...]

    @property
    def inputs(self) -> tuple[int, ...]:
        """USB inputs recorded in this round."""
        return tuple(p.usb_input for p in self.placements)


def plan_rounds(points: Sequence[str], mics: Sequence[int]) -> tuple[Round, ...]:
    """Split the points of a plan into rounds for the available microphones.

    Raises:
        ValueError: No point, no microphone, or a point or microphone given twice.
    """
    if not points or not mics:
        raise ValueError("a measurement needs at least one point and one microphone")
    if len(set(points)) != len(points) or len(set(mics)) != len(mics):
        raise ValueError("points and microphones must be unique")
    size = len(mics)
    rounds = []
    for number, start in enumerate(range(0, len(points), size), start=1):
        chunk = points[start : start + size]
        rounds.append(
            Round(number, tuple(Placement(m, p) for m, p in zip(mics, chunk, strict=False)))
        )
    return tuple(rounds)
