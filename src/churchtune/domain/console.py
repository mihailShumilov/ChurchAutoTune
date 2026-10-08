"""Console state of the speaker paths, as read from the console. Pure data."""

from collections.abc import Mapping
from dataclasses import dataclass

from churchtune.domain.eq import SystemEq


@dataclass(frozen=True, slots=True)
class MatrixState:
    """One matrix feeding speakers.

    Attributes:
        number: Matrix number, 1..6.
        name: Scribble-strip name.
        eq: The matrix EQ block.
        on: False when the matrix is muted.
        fader_db: Fader level in dB; ``-inf`` when the fader is down.
        invert: True when the polarity is inverted.
        outputs: Physical outputs fed by this matrix.
    """

    number: int
    name: str
    eq: SystemEq
    on: bool
    fader_db: float
    invert: bool
    outputs: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class OutputDelay:
    """Delay setting of one physical output.

    Attributes:
        output: Output number, 1..16.
        on: Whether the delay is switched on.
        delay_ms: The stored delay time in ms (applies only when ``on``).
    """

    output: int
    on: bool
    delay_ms: float

    @property
    def effective_ms(self) -> float:
        """Delay that is actually applied: the stored time when on, otherwise zero."""
        return self.delay_ms if self.on else 0.0


@dataclass(frozen=True, slots=True)
class ConsoleState:
    """State of the paths between Main LR and the speakers.

    Attributes:
        main_eq: Main LR EQ.
        matrices: Matrix states by matrix number.
        delays: Output delays by output number.
    """

    main_eq: SystemEq
    matrices: Mapping[int, MatrixState]
    delays: Mapping[int, OutputDelay]
