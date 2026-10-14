"""Speaker topology: which physical output drives which speaker, as guessed from a snapshot.

The guess is never applied silently: the wizard shows it for confirmation (SPEC S4). Reasons are
codes with parameters, rendered into words by the UI. Pure data.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
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
    # Speakers in another room (foyer, nursery, overflow): not tuned, but people there hear sweeps.
    ZONE = "zone"
    MONITOR = "monitor"
    UNUSED = "unused"


TOP_ROLES: frozenset[SpeakerRole] = frozenset(
    {SpeakerRole.TOP_L, SpeakerRole.TOP_R, SpeakerRole.TOP_MONO}
)


class Variant(StrEnum):
    """Supported system layouts (SPEC S4)."""

    # Tops on matrices or buses, sub on its own matrix or bus.
    A = "a_tops_and_sub_on_own_paths"
    # The tops come straight from Main LR, or the sub hangs on Main Mono: one of the crossover
    # filters has no separate path, so that part is suggest-only.
    B = "b_tops_on_main_sub_separate"
    # Tops without a sub path. The wizard asks whether a sub exists anyway (then SUB_ON_MAIN).
    C = "c_tops_only"
    # The sub shares Main LR with the tops (set from the operator's answer, never guessed): no
    # crossover possible.
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
        subs: Paths of the subwoofers (a stereo pair counts as one sub group).
        others: Other speaker paths in the hall (fills, delays/balcony).
        zones: Speaker paths in other rooms (foyer, nursery).
        issues: Things to explain to the operator.
    """

    outputs: tuple[OutputGuess, ...]
    variant: Variant
    tops: tuple[SignalRef, ...]
    subs: tuple[SignalRef, ...]
    others: tuple[SignalRef, ...]
    zones: tuple[SignalRef, ...] = ()
    issues: tuple[Issue, ...] = ()

    @property
    def sub(self) -> SignalRef | None:
        """The one sub path, or None when there is none or several."""
        return self.subs[0] if len(self.subs) == 1 else None

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


def output_key(kind: OutputKind | str, number: int) -> str:
    """Key of an output in a roles mapping: ``"main 8"``."""
    return f"{OutputKind(kind).value} {number}"


def with_roles(guess: TopologyGuess, roles: Mapping[str, str]) -> TopologyGuess:
    """The guess with the roles the person confirmed or corrected.

    ``roles`` maps an output key (``"main 8"``, ``kind number``) to a ``SpeakerRole`` value; outputs
    not in it keep their guessed role. The speaker paths are grouped again from the roles, with
    confidence 1 for every confirmed output. ``variant`` and ``issues`` stay those of the guess:
    they explain what was read, not what the person corrected.
    """
    outputs = tuple(
        replace(g, role=SpeakerRole(roles[key]), confidence=1.0)
        if (key := output_key(g.kind, g.number)) in roles
        else g
        for g in guess.outputs
    )

    def by_role(*wanted: SpeakerRole) -> tuple[SignalRef, ...]:
        refs = {g.path for g in outputs if g.role in wanted}
        return tuple(sorted(refs, key=lambda r: (r.kind.value, r.number)))

    return replace(
        guess,
        outputs=outputs,
        tops=by_role(*TOP_ROLES),
        subs=by_role(SpeakerRole.SUB),
        others=by_role(SpeakerRole.DELAY, SpeakerRole.FILL),
        zones=by_role(SpeakerRole.ZONE),
    )
