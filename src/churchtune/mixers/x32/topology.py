"""Which outputs drive which speakers.

``infer_topology`` guesses a role with a confidence and reasons for every physical output that
carries a speaker path (SPEC S4). Evidence, strongest first: the output's source, the strip name
(multilingual dictionary), cut filters and "crutch" shelves on the path, stereo links, what feeds
the path, and the fader. The guess is always shown to the operator for confirmation; a confidence
below ``LOW_CONFIDENCE`` makes the wizard ask.

``detect_roles`` is the single-hall rule of M0, kept for the transitional expert CLI.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from churchtune.domain.console import ConsoleState, Roles
from churchtune.domain.eq import Band
from churchtune.domain.snapshot import (
    ConsoleSnapshot,
    MixStrip,
    OutputKind,
    PhysicalOutput,
    SignalRef,
)
from churchtune.domain.snapshot import SourceKind as K
from churchtune.domain.topology import (
    TOP_ROLES,
    Issue,
    OutputGuess,
    Reason,
    SpeakerRole,
    TopologyGuess,
    Variant,
)
from churchtune.mixers.x32.filters import (
    has_crutch_low_shelf,
    has_hpf,
    has_lpf,
    has_subsonic,
)
from churchtune.mixers.x32.names import Side, name_hint, side_hint
from churchtune.mixers.x32.speaker_paths import (
    MAIN_SOURCES,
    PAIR,
    SpeakerPaths,
    feeders,
    is_silent,
    linked_side,
)
from churchtune.mixers.x32.topology_issues import topology_issues

SPEAKER_SOURCES = frozenset({K.MAIN_L, K.MAIN_R, K.MONO, K.BUS, K.MATRIX})

# Confidence of each kind of evidence.
CONF_NAME_AND_FILTER = 0.9
CONF_MAIN_SOURCE = 0.85
CONF_NAME = 0.8
CONF_NAME_FILTER_DIFFER = 0.7
CONF_NON_SPEAKER = 0.7
CONF_FILTER = 0.65
CONF_AMBIGUOUS_NAME = 0.6
CONF_MAIN_ON_AUX_OUTPUT = 0.6
CONF_UNUSED_SILENT = 0.6
CONF_CRUTCH_LOW_SHELF = 0.55
CONF_SUBSONIC_ONLY = 0.5
CONF_MONITOR_DEFAULT = 0.5
CONF_MAIN_NEXT_TO_MATRIX_TOPS = 0.5
CONF_NAME_FILTER_CONFLICT = 0.5
CONF_SIDE_BY_NUMBER = 0.5
CONF_SILENT_WITH_EVIDENCE = 0.45
CONF_AMBIGUOUS_BUS = 0.45
CONF_DEFAULT_FED = 0.4
CONF_DEFAULT_FED_AUX = 0.3
CONF_DEFAULT_UNFED = 0.3


@dataclass(frozen=True, slots=True)
class _Evidence:
    role: SpeakerRole
    confidence: float
    reasons: tuple[Reason, ...]

    def with_reason(self, reason: Reason, confidence: float | None = None) -> "_Evidence":
        level = self.confidence if confidence is None else confidence
        return _Evidence(self.role, level, (*self.reasons, reason))


def _band_reason(code: str, band: Band) -> Reason:
    return Reason(code, {"type": band.type.value, "freq_hz": band.freq_hz})


def _filters(strip: MixStrip) -> tuple[SpeakerRole | None, float, list[Reason]]:
    """Role suggested by the filters of a path, its confidence and the reasons."""
    if not strip.eq_on:
        return None, 0.0, []
    reasons: list[Reason] = []
    subsonic = has_subsonic(strip.eq)
    if subsonic is not None:
        reasons.append(_band_reason("subsonic", subsonic))
    lpf = has_lpf(strip.eq)
    if lpf is not None:
        return SpeakerRole.SUB, CONF_FILTER, [_band_reason("lpf", lpf), *reasons]
    hpf = has_hpf(strip.eq)
    if hpf is not None:
        return SpeakerRole.TOP_MONO, CONF_FILTER, [_band_reason("hpf", hpf), *reasons]
    shelf = has_crutch_low_shelf(strip.eq)
    if shelf is not None:
        return SpeakerRole.TOP_MONO, CONF_CRUTCH_LOW_SHELF, [_band_reason("low_shelf", shelf)]
    if subsonic is not None:
        # Only a subsonic cut: typical of a sub whose low-pass lives in its own DSP. Weak.
        return SpeakerRole.SUB, CONF_SUBSONIC_ONLY, reasons
    return None, 0.0, reasons


def _combine(
    named: SpeakerRole, filtered: SpeakerRole | None, ambiguous: bool
) -> tuple[SpeakerRole, float, list[Reason]]:
    """Role and confidence from a name, refined by the filters."""
    if filtered is None:
        return named, CONF_AMBIGUOUS_NAME if ambiguous else CONF_NAME, []
    if named is filtered or (named in TOP_ROLES and filtered in TOP_ROLES):
        return named, CONF_NAME_AND_FILTER, []
    if filtered is SpeakerRole.SUB and named in TOP_ROLES:
        return named, CONF_NAME_FILTER_CONFLICT, [Reason("name_filter_conflict")]
    return named, CONF_NAME_FILTER_DIFFER, []


def _strip_evidence(
    strip: MixStrip, default: SpeakerRole, default_conf: float, default_code: str = "default"
) -> _Evidence:
    """Role of a bus, matrix or mono path from its name, filters and fader."""
    hint = name_hint(strip.name)
    if hint.role is None and hint.non_speaker:
        reason = Reason("name_not_speaker", {"name": strip.name})
        return _Evidence(SpeakerRole.UNUSED, CONF_NON_SPEAKER, (reason,))
    filtered, filter_conf, reasons = _filters(strip)
    named = hint.role
    if named is SpeakerRole.SUB and hint.ambiguous and strip.ref.kind is K.BUS and filtered is None:
        # "Bass" on a bus without a low-pass: most likely the bass player's monitor.
        named = SpeakerRole.MONITOR
        reasons.append(Reason("name_ambiguous", {"name": strip.name}))
        role, confidence = named, CONF_AMBIGUOUS_BUS
    elif named is not None:
        role, confidence, extra = _combine(named, filtered, hint.ambiguous)
        reasons = [Reason("name", {"name": strip.name}), *reasons, *extra]
    elif filtered is not None:
        role, confidence = filtered, filter_conf
    else:
        role, confidence = default, default_conf
        reasons.append(Reason(default_code, {"role": default.value}))
    if is_silent(strip):
        reasons.append(Reason("muted_or_down"))
        if named is None and filtered is None:
            return _Evidence(SpeakerRole.UNUSED, CONF_UNUSED_SILENT, tuple(reasons))
        confidence = min(confidence, CONF_SILENT_WITH_EVIDENCE)
    return _Evidence(role, confidence, tuple(reasons))


def _main_evidence(output: PhysicalOutput, strip: MixStrip) -> _Evidence:
    ref = output.source
    role = SpeakerRole.TOP_L if ref.kind is K.MAIN_L else SpeakerRole.TOP_R
    reasons = (Reason("source_main", {"side": ref.kind.value}),)
    if is_silent(strip):
        return _Evidence(role, CONF_SILENT_WITH_EVIDENCE, (*reasons, Reason("muted_or_down")))
    on_aux = output.kind is OutputKind.AUX
    return _Evidence(role, CONF_MAIN_ON_AUX_OUTPUT if on_aux else CONF_MAIN_SOURCE, reasons)


def _matrix_evidence(snapshot: ConsoleSnapshot, output: PhysicalOutput) -> _Evidence:
    strip = snapshot.matrices[output.source.number]
    fed = feeders(snapshot, output.source.number)
    if not fed:
        return _strip_evidence(strip, SpeakerRole.FILL, CONF_DEFAULT_UNFED, "default_unfed_matrix")
    on_aux = output.kind is OutputKind.AUX
    default_conf = CONF_DEFAULT_FED_AUX if on_aux else CONF_DEFAULT_FED
    evidence = _strip_evidence(strip, SpeakerRole.TOP_MONO, default_conf)
    return evidence.with_reason(Reason("fed_by", {"paths": ", ".join(f.label() for f in fed)}))


def _evidence(snapshot: ConsoleSnapshot, output: PhysicalOutput) -> _Evidence | None:
    ref = output.source
    strip = snapshot.strip(ref)
    if strip is None:
        return None
    if ref.kind in MAIN_SOURCES:
        return _main_evidence(output, strip)
    if ref.kind is K.MONO:
        return _strip_evidence(strip, SpeakerRole.TOP_MONO, CONF_DEFAULT_FED)
    if ref.kind is K.BUS:
        # A bus on a physical output with no other clue is almost always a stage monitor.
        return _strip_evidence(strip, SpeakerRole.MONITOR, CONF_MONITOR_DEFAULT)
    return _matrix_evidence(snapshot, output)


Sides = dict[SignalRef, tuple[SpeakerRole, Reason | None]]


def _sides(snapshot: ConsoleSnapshot, refs: list[SignalRef]) -> tuple[Sides, list[Issue]]:
    """Assign L / R / mono to top paths that are not Main L / Main R."""
    role_of = {Side.LEFT: SpeakerRole.TOP_L, Side.RIGHT: SpeakerRole.TOP_R}
    sides: Sides = {}
    issues: list[Issue] = []
    for ref in refs:
        strip = snapshot.strip(ref)
        named = side_hint(strip.name) if strip else None
        linked = linked_side(snapshot, ref)
        if named is not None and linked is not None and named is not linked:
            issues.append(Issue("side_conflict", {"path": ref.label()}))
        side = named or linked
        if side is not None:
            sides[ref] = (role_of[side], None)
    rest = sorted((r for r in refs if r not in sides), key=lambda r: r.number)
    if len(rest) == PAIR:  # an unlinked, unnamed pair of top paths
        reason = Reason("side_by_number")
        sides[rest[0]], sides[rest[1]] = (SpeakerRole.TOP_L, reason), (SpeakerRole.TOP_R, reason)
    else:
        for ref in rest:
            sides[ref] = (SpeakerRole.TOP_MONO, None)
    taken = [role for role, _ in sides.values() if role is not SpeakerRole.TOP_MONO]
    if len(taken) != len(set(taken)):
        issues.append(Issue("side_conflict", {"path": "several"}))
    return sides, issues


def _guess(output: PhysicalOutput, evidence: _Evidence, name: str) -> OutputGuess:
    return OutputGuess(
        output.kind,
        output.number,
        output.source,
        name,
        evidence.role,
        round(evidence.confidence, 2),
        evidence.reasons,
    )


def _variant(tops: Sequence[SignalRef], subs: Sequence[SignalRef]) -> Variant:
    if not tops:
        return Variant.UNKNOWN
    if not subs:
        return Variant.C
    if any(t.kind in MAIN_SOURCES for t in tops) or any(s.kind is K.MONO for s in subs):
        return Variant.B
    return Variant.A


def _resolve_main_and_matrix_tops(evidence: dict[SignalRef, _Evidence]) -> list[Issue]:
    """Main L/R on outputs next to matrix tops: the matrices are the likely tops; ask."""
    matrix_tops = [r for r, ev in evidence.items() if ev.role in TOP_ROLES and r.kind is K.MATRIX]
    mains = [r for r in evidence if r.kind in MAIN_SOURCES]
    if not matrix_tops or not mains:
        return []
    for ref in mains:
        confidence = min(evidence[ref].confidence, CONF_MAIN_NEXT_TO_MATRIX_TOPS)
        evidence[ref] = evidence[ref].with_reason(Reason("matrix_tops_too"), confidence)
    params = {
        "mains": ", ".join(r.label() for r in mains),
        "matrices": ", ".join(r.label() for r in matrix_tops),
    }
    return [Issue("main_and_matrix_tops", params)]


def _apply_sides(snapshot: ConsoleSnapshot, evidence: dict[SignalRef, _Evidence]) -> list[Issue]:
    top_paths = [
        r for r, ev in evidence.items() if ev.role in TOP_ROLES and r.kind not in MAIN_SOURCES
    ]
    sides, issues = _sides(snapshot, top_paths)
    for ref, (role, reason) in sides.items():
        ev = _Evidence(role, evidence[ref].confidence, evidence[ref].reasons)
        if reason is not None:
            ev = ev.with_reason(reason, min(ev.confidence, CONF_SIDE_BY_NUMBER))
        evidence[ref] = ev
    return issues


def _paths(guesses: list[OutputGuess]) -> SpeakerPaths:
    def by_role(*roles: SpeakerRole) -> tuple[SignalRef, ...]:
        refs = {g.path for g in guesses if g.role in roles}
        return tuple(sorted(refs, key=lambda r: (r.kind.value, r.number)))

    tops = by_role(*TOP_ROLES)
    if any(t.kind is K.MATRIX for t in tops):
        tops = tuple(t for t in tops if t.kind not in MAIN_SOURCES)
    return SpeakerPaths(
        tops,
        by_role(SpeakerRole.SUB),
        by_role(SpeakerRole.DELAY, SpeakerRole.FILL),
        by_role(SpeakerRole.ZONE),
    )


def infer_topology(snapshot: ConsoleSnapshot) -> TopologyGuess:
    """Guess the role of every physical output that carries a speaker path.

    Args:
        snapshot: A parsed console snapshot.

    Returns:
        The guess, with the system variant and the issues the wizard must explain.
    """
    evidence: dict[SignalRef, _Evidence] = {}
    for output in snapshot.outputs:
        if output.source.kind in SPEAKER_SOURCES and output.source not in evidence:
            found = _evidence(snapshot, output)
            if found is not None:
                evidence[output.source] = found
    early = _resolve_main_and_matrix_tops(evidence)
    early += _apply_sides(snapshot, evidence)
    guesses: list[OutputGuess] = []
    for output in snapshot.outputs:
        if output.source in evidence:
            strip = snapshot.strip(output.source)
            guesses.append(_guess(output, evidence[output.source], strip.name if strip else ""))
    paths = _paths(guesses)
    return TopologyGuess(
        tuple(guesses),
        _variant(paths.tops, paths.subs),
        paths.tops,
        paths.subs,
        paths.others,
        paths.zones,
        (*early, *topology_issues(snapshot, guesses, paths)),
    )


# ------------------------------------------------------------------ M0 rule (expert CLI)
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
