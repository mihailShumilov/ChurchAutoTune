"""The speaker paths of a snapshot in the shape the design rules use (``ConsoleState``, ``Roles``).

The design code of M0 works on matrices: Main LR EQ, the matrices with their EQ, mute, fader and
polarity, and output delays. The wizard reads a full ``ConsoleSnapshot`` and guesses a
``TopologyGuess``; these helpers translate one into the other. Paths that are not matrices
(tops straight on Main LR, a sub on a bus or on Mono) have no ``Roles``: the design then works
in suggestion mode for what it cannot place.
"""

from churchtune.domain.console import ConsoleState, MatrixState, OutputDelay, Roles
from churchtune.domain.eq import SystemEq
from churchtune.domain.snapshot import ConsoleSnapshot, MixStrip, OutputKind, SignalRef, SourceKind
from churchtune.domain.topology import TopologyGuess


def strip_eq(strip: MixStrip) -> SystemEq:
    """The EQ block of a strip."""
    return SystemEq(strip.eq_on, strip.eq)


def console_state(snapshot: ConsoleSnapshot) -> ConsoleState:
    """Main LR EQ, every matrix and the delays of the main outputs."""
    matrices = {
        number: MatrixState(
            number=number,
            name=strip.name,
            eq=strip_eq(strip),
            on=strip.on,
            fader_db=strip.fader_db,
            invert=strip.invert,
            outputs=tuple(
                o.number for o in snapshot.outputs_fed_by(strip.ref) if o.kind is OutputKind.MAIN
            ),
        )
        for number, strip in snapshot.matrices.items()
    }
    delays = {
        o.number: OutputDelay(o.number, o.delay.on, o.delay.delay_ms)
        for o in snapshot.outputs
        if o.kind is OutputKind.MAIN and o.delay is not None
    }
    return ConsoleState(strip_eq(snapshot.main), matrices, delays)


def _matrices(refs: tuple[SignalRef, ...]) -> tuple[int, ...] | None:
    if not refs or any(r.kind is not SourceKind.MATRIX for r in refs):
        return None
    return tuple(sorted({r.number for r in refs}))


def matrix_roles(guess: TopologyGuess) -> Roles | None:
    """Sub and top matrices, or None when they cannot carry a crossover.

    None when the tops or the sub are not on matrices, or when one matrix would be both: a
    crossover filter there would cut the sub or the tops.
    """
    tops = _matrices(guess.tops)
    sub = _matrices(guess.subs)
    if tops is None or sub is None:
        return None
    others = tuple(sorted({r.number for r in guess.others if r.kind is SourceKind.MATRIX}))
    if set(sub) & {*tops, *others}:
        return None  # one matrix feeds the sub and other speakers: a shared path, no crossover
    return Roles(sub=sub, mains=tops, others=others)
