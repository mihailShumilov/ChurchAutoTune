"""Speaker topology: which physical output drives which speaker, as guessed from a snapshot.

The guess is never applied silently: the wizard shows it for confirmation (SPEC S4). Reasons are
codes with parameters, rendered into words by the UI. Pure data.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from churchtune.domain.snapshot import OutputKind, SignalRef


class SpeakerRole(StrEnum):
    """Role of a physical output in the sound system."""

    TOP_L = "top_l"
    TOP_R = "top_r"
    TOP_MONO = "top_mono"
    SUB = "sub"
    FILL = "fill"
    DELAY = "delay"
    MONITOR = "monitor"
    UNUSED = "unused"


TOP_ROLES: frozenset[SpeakerRole] = frozenset(
    {SpeakerRole.TOP_L, SpeakerRole.TOP_R, SpeakerRole.TOP_MONO}
)


class Variant(StrEnum):
    """Supported system layouts (SPEC S4)."""

    # Tops on matrices or buses, sub on its own matrix or bus.
    A = "a_tops_and_sub_on_own_paths"
    # Tops straight from Main LR, sub on a matrix, a bus or Main Mono.
    B = "b_tops_on_main_sub_separate"
    # Tops without a sub.
    C = "c_tops_only"
    # The sub shares Main LR with the tops: no crossover possible.
    SUB_ON_MAIN = "sub_shares_main"
    # No tops found at all.
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Reason:
    """Why a role was guessed: a code and its parameters.

    Attributes:
        code: Stable code, such as ``name`` or ``hpf``.
        params: Values for the message.
    """

    code: str
    params: Mapping[str, str | float | int] | None = None


@dataclass(frozen=True, slots=True)
class OutputGuess:
    """The guessed role of one physical output.

    Attributes:
        kind: Output group.
        number: Output number inside the group.
        path: Mix signal that feeds the output.
        name: Name of the strip behind the path (empty when none).
        role: Guessed role.
        confidence: 0..1.
        reasons: Evidence, strongest first.
    """

    kind: OutputKind
    number: int
    path: SignalRef
    name: str
    role: SpeakerRole
    confidence: float
    reasons: tuple[Reason, ...]


@dataclass(frozen=True, slots=True)
class Issue:
    """Something the wizard must explain about the topology.

    Attributes:
        code: Stable code, such as ``sub_on_mono_suggest_only``.
        params: Values for the message.
    """

    code: str
    params: Mapping[str, str | float | int] | None = None


@dataclass(frozen=True, slots=True)
class TopologyGuess:
    """The whole guess.

    Attributes:
        outputs: Every physical output that carries a speaker path, in output order.
        variant: The system layout.
        tops: Paths of the top speakers.
        sub: Path of the subwoofer, or None.
        others: Other speaker paths (fills, delays/balcony).
        issues: Things to explain to the operator.
    """

    outputs: tuple[OutputGuess, ...]
    variant: Variant
    tops: tuple[SignalRef, ...]
    sub: SignalRef | None
    others: tuple[SignalRef, ...]
    issues: tuple[Issue, ...] = ()

    def role_of(self, kind: OutputKind, number: int) -> SpeakerRole | None:
        """Role of one output, or None when the output carries no speaker path."""
        for guess in self.outputs:
            if guess.kind is kind and guess.number == number:
                return guess.role
        return None

    @property
    def confidence(self) -> float:
        """Lowest confidence among the outputs that drive speakers (1.0 when there are none)."""
        speakers = [g.confidence for g in self.outputs if g.role is not SpeakerRole.UNUSED]
        return min(speakers, default=1.0)
