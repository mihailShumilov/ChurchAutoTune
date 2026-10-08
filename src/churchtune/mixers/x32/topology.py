"""Which matrix feeds which speakers.

M0 ports the single-hall rule: roles come from the matrix names and from which matrices feed a
physical output; the operator can override each role. Full topology inference with confidence
(SPEC S4) arrives in M1.
"""

import re
from collections.abc import Sequence

from churchtune.domain.console import ConsoleState, Roles

SUB_NAME_RE = re.compile(r"sub|саб", re.IGNORECASE)
MAINS_NAME_RE = re.compile(r"main", re.IGNORECASE)


def detect_roles(
    state: ConsoleState,
    *,
    sub: Sequence[int] | None = None,
    mains: Sequence[int] | None = None,
    others: Sequence[int] | None = None,
) -> Roles:
    """Detect sub, top and other speaker matrices; explicit arguments override the detection.

    A matrix takes part only if it feeds at least one physical output. Without an override the
    sub is a matrix named like a subwoofer, the mains are matrices named like mains, and every
    other matrix with an output counts as "other".

    Raises:
        ValueError: No sub or no mains were found and none were given, or a named matrix does
            not exist or feeds no physical output.
    """
    feeding = {n: m for n, m in state.matrices.items() if m.outputs}
    if sub is None:
        sub = [n for n, m in feeding.items() if SUB_NAME_RE.search(m.name)]
    if mains is None:
        mains = [n for n, m in feeding.items() if MAINS_NAME_RE.search(m.name)]
    if others is None:
        others = [n for n in feeding if n not in sub and n not in mains]
    named = [*sub, *mains, *others]
    without_output = sorted({n for n in named if n not in feeding})
    if without_output:
        raise ValueError(
            f"matrices {without_output} feed no physical output and cannot have a role"
        )
    if not sub or not mains:
        raise ValueError(
            f"could not tell which matrices are the sub and the mains "
            f"(sub={list(sub)}, mains={list(mains)}); name them explicitly"
        )
    return Roles(tuple(sub), tuple(mains), tuple(others))
