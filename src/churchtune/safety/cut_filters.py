"""Where a plan may touch a cut filter (SPEC §9.2: low cut, high cut, crossover slopes).

Only the crossover package, and the undo of one, change a band that is or becomes a cut filter.
Even then: never on Main LR (a common path: its subsonic filter protects everything), only band 1
of a top path (its high-pass) and band 6 of the sub path (its low-pass), with the corner between
40 and 250 Hz. Setting or moving a cut is the crossover's job; removing one is allowed only to an
undo, which puts back what the console had before a crossover.
"""

from collections.abc import Callable
from enum import StrEnum

from churchtune.domain.eq import Band, SystemEq
from churchtune.errors import UnsafeWrite

TOP_HPF_BAND, SUB_LPF_BAND = 1, 6
CROSSOVER_RANGE_HZ = (40.0, 250.0)


class PathRole(StrEnum):
    """What a strip feeds, from a fresh read of the console."""

    MAIN = "main"
    TOPS = "tops"
    SUB = "sub"
    OTHER = "other"


SameBand = Callable[[Band, Band], bool]


def check_cut_changes(  # noqa: PLR0913 - every input of the rule is explicit
    label: str,
    role: PathRole,
    now: SystemEq,
    wanted: SystemEq,
    same_band: SameBand,
    *,
    may_remove: bool,
) -> None:
    """Refuse a change of a cut-filter band outside the crossover's place and range.

    Args:
        label: The strip, for messages.
        role: What the strip feeds.
        now: Its EQ now (fresh read).
        wanted: Its EQ after the plan.
        same_band: Compares two bands on the console's grid.
        may_remove: True for an undo: a cut may become an ordinary band again.

    Raises:
        UnsafeWrite: A cut band would change where or how no crossover may change it.
    """
    allowed_band = {PathRole.TOPS: TOP_HPF_BAND, PathRole.SUB: SUB_LPF_BAND}.get(role)
    for old, new in zip(now.bands, wanted.bands, strict=True):
        if (old.adjustable and new.adjustable) or same_band(old, new):
            continue
        where = f"band {new.band} of {label}"
        if allowed_band is None or new.band != allowed_band:
            raise UnsafeWrite(f"a cut filter may not change in {where}", cut_filter=label)
        if not old.adjustable and new.adjustable and not may_remove:
            raise UnsafeWrite(f"the cut filter in {where} would be removed", cut_filter=label)
        low, high = CROSSOVER_RANGE_HZ
        if not new.adjustable and not low <= new.freq_hz <= high:
            raise UnsafeWrite(
                f"a cut filter at {new.freq_hz:.0f} Hz in {where} is outside "
                f"{low:.0f}-{high:.0f} Hz",
                cut_filter=label,
            )
