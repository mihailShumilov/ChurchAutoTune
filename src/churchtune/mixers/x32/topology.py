"""Which outputs drive which speakers.

``infer_topology`` guesses a role with a confidence and reasons for every physical output that
carries a speaker path (SPEC S4). Evidence, strongest first: the output's source, the strip name
(multilingual dictionary), cut filters and "crutch" shelves on the path, stereo links, and the
fader. The guess is always shown to the operator for confirmation.

``detect_roles`` is the single-hall rule of M0, kept for the transitional expert CLI.
"""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from churchtune.domain.console import ConsoleState, Roles
from churchtune.domain.eq import Band, EqType
from churchtune.domain.snapshot import ConsoleSnapshot, MixStrip, PhysicalOutput, SignalRef
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
from churchtune.mixers.x32.names import Side, is_non_speaker, role_hint, side_hint

SPEAKER_SOURCES = frozenset({K.MAIN_L, K.MAIN_R, K.MONO, K.BUS, K.MATRIX})
# Corner frequencies that look like a crossover rather than a sound-shaping filter.
HPF_RANGE_HZ = (40.0, 250.0)
LPF_RANGE_HZ = (50.0, 250.0)
# A high shelf this deep below 1 kHz is a "crutch" low-pass (seen on the reference sub matrix).
CRUTCH_SHELF_MAX_HZ = 1000.0
CRUTCH_SHELF_MAX_DB = -10.0
LOW_CONFIDENCE = 0.6
_HPF_TYPES = frozenset({EqType.LCUT, *(t for t in EqType if t.value[:2] in ("BU", "BS", "LR"))})
_LPF_TYPES = frozenset({EqType.HCUT, *(t for t in EqType if t.value[:2] in ("BU", "BS", "LR"))})
FIRST_BAND, LAST_BAND = 1, 6


def _between(value: float, span: tuple[float, float]) -> bool:
    return span[0] <= value <= span[1]


def has_hpf(eq: Sequence[Band]) -> Band | None:
    """The band that acts as a crossover high-pass (low cut on band 1, or any LCut), or None."""
    for band in eq:
        is_hpf = band.type is EqType.LCUT or (band.band == FIRST_BAND and band.type in _HPF_TYPES)
        if is_hpf and _between(band.freq_hz, HPF_RANGE_HZ):
            return band
    return None


def has_lpf(eq: Sequence[Band]) -> Band | None:
    """The band that acts as a low-pass: a high cut, a cut filter on band 6 or a deep high shelf."""
    for band in eq:
        is_cut = band.type is EqType.HCUT or (band.band == LAST_BAND and band.type in _LPF_TYPES)
        if is_cut and _between(band.freq_hz, LPF_RANGE_HZ):
            return band
        crutch = (
            band.type is EqType.HSHV
            and band.freq_hz <= CRUTCH_SHELF_MAX_HZ
            and band.gain_db <= CRUTCH_SHELF_MAX_DB
        )
        if crutch:
            return band
    return None


@dataclass(frozen=True, slots=True)
class _Evidence:
    role: SpeakerRole
    confidence: float
    reasons: tuple[Reason, ...]


def _filters(strip: MixStrip) -> tuple[SpeakerRole | None, Reason | None]:
    if not strip.eq_on:
        return None, None
    lpf = has_lpf(strip.eq)
    if lpf is not None:
        return SpeakerRole.SUB, Reason("lpf", {"type": lpf.type.value, "freq_hz": lpf.freq_hz})
    hpf = has_hpf(strip.eq)
    if hpf is not None:
        return SpeakerRole.TOP_MONO, Reason("hpf", {"type": hpf.type.value, "freq_hz": hpf.freq_hz})
    return None, None


def _silent(strip: MixStrip) -> bool:
    return not strip.on or strip.fader_db == -math.inf


def _strip_evidence(strip: MixStrip, default: SpeakerRole, default_conf: float) -> _Evidence:
    """Role of a bus, matrix or mono path from its name, filters and fader."""
    named = role_hint(strip.name)
    if named is None and is_non_speaker(strip.name):
        return _Evidence(
            SpeakerRole.UNUSED, 0.7, (Reason("name_not_speaker", {"name": strip.name}),)
        )
    filtered, filter_reason = _filters(strip)
    reasons: list[Reason] = []
    if named is not None:
        reasons.append(Reason("name", {"name": strip.name}))
    if filter_reason is not None:
        reasons.append(filter_reason)
    if named is not None and filtered is not None:
        agree = named is filtered or (named in TOP_ROLES and filtered in TOP_ROLES)
        if agree:
            role, confidence = named, 0.9
        elif filtered is SpeakerRole.SUB and named in TOP_ROLES:
            role, confidence = named, 0.5
            reasons.append(Reason("name_filter_conflict"))
        else:
            role, confidence = named, 0.7
    elif named is not None:
        role, confidence = named, 0.8
    elif filtered is not None:
        role, confidence = filtered, 0.65
    else:
        role, confidence = default, default_conf
        reasons.append(Reason("default", {"role": default.value}))
    if _silent(strip):
        reasons.append(Reason("muted_or_down"))
        if named is None:
            return _Evidence(SpeakerRole.UNUSED, 0.6, tuple(reasons))
        confidence = min(confidence, 0.5)
    return _Evidence(role, confidence, tuple(reasons))


def _fed_by_main(snapshot: ConsoleSnapshot, matrix: int) -> bool:
    return any(s.target == matrix and s.open for s in snapshot.main.matrix_sends)


def _main_evidence(strip: MixStrip, ref: SignalRef) -> _Evidence:
    role = SpeakerRole.TOP_L if ref.kind is K.MAIN_L else SpeakerRole.TOP_R
    reasons = (Reason("source_main", {"side": ref.kind.value}),)
    if _silent(strip):
        return _Evidence(role, 0.5, (*reasons, Reason("muted_or_down")))
    return _Evidence(role, 0.85, reasons)


def _evidence(snapshot: ConsoleSnapshot, ref: SignalRef) -> _Evidence | None:
    strip = snapshot.strip(ref)
    if strip is None:
        return None
    if ref.kind in (K.MAIN_L, K.MAIN_R):
        return _main_evidence(strip, ref)
    if ref.kind is K.MONO:
        return _strip_evidence(strip, SpeakerRole.TOP_MONO, 0.4)
    if ref.kind is K.BUS:
        # A bus on a physical output with no other clue is almost always a stage monitor.
        return _strip_evidence(strip, SpeakerRole.MONITOR, 0.5)
    fed = _fed_by_main(snapshot, ref.number)
    evidence = _strip_evidence(strip, SpeakerRole.TOP_MONO if fed else SpeakerRole.FILL, 0.4)
    if fed:
        return _Evidence(
            evidence.role, evidence.confidence, (*evidence.reasons, Reason("fed_by_main"))
        )
    return evidence


def _sides(snapshot: ConsoleSnapshot, refs: list[SignalRef]) -> dict[SignalRef, SpeakerRole]:
    """Assign L / R / mono to top paths that are not Main L / Main R."""
    sides: dict[SignalRef, SpeakerRole] = {}
    left, right = SpeakerRole.TOP_L, SpeakerRole.TOP_R
    for ref in refs:
        strip = snapshot.strip(ref)
        side = side_hint(strip.name) if strip else None
        if side is not None:
            sides[ref] = left if side is Side.LEFT else right
    rest = [r for r in refs if r not in sides]
    kind_names = {K.MATRIX: "matrix", K.BUS: "bus"}
    for ref in rest:
        pair_start = ref.number if ref.number % 2 else ref.number - 1
        if (kind_names.get(ref.kind, ""), pair_start) in snapshot.stereo_links:
            sides[ref] = left if ref.number % 2 else right
    rest = [r for r in refs if r not in sides]
    if len(rest) == 2:  # noqa: PLR2004 - an unlinked pair of top paths
        first, second = sorted(rest, key=lambda r: r.number)
        sides[first], sides[second] = left, right
    for ref in rest:
        sides.setdefault(ref, SpeakerRole.TOP_MONO)
    return sides


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


def _variant(tops: list[SignalRef], sub: SignalRef | None) -> Variant:
    if not tops:
        return Variant.UNKNOWN
    if sub is None:
        return Variant.C
    if any(t.kind in (K.MAIN_L, K.MAIN_R) for t in tops) or sub.kind is K.MONO:
        return Variant.B
    return Variant.A


def _issues(
    snapshot: ConsoleSnapshot,
    guesses: list[OutputGuess],
    tops: list[SignalRef],
    subs: list[SignalRef],
) -> list[Issue]:
    issues: list[Issue] = []
    sub = subs[0] if subs else None
    if len(subs) > 1:
        issues.append(Issue("several_subs", {"paths": ", ".join(s.label() for s in subs)}))
    if sub is not None and sub.kind is K.MONO:
        issues.append(Issue("sub_on_mono_suggest_only"))
    if sub is not None and sub.kind is K.BUS:
        issues.append(Issue("sub_on_bus_suggest_only", {"bus": sub.number}))
    if sub is not None and sub.kind is K.MATRIX and not _fed_by_main(snapshot, sub.number):
        issues.append(Issue("sub_not_fed_by_main", {"matrix": sub.number}))
    if sub is not None and any(t.kind in (K.MAIN_L, K.MAIN_R) for t in tops):
        issues.append(Issue("crossover_hpf_suggest_only"))
    seen: set[SignalRef] = set()
    monitors: list[str] = []
    for guess in guesses:
        if guess.path in seen:
            continue
        seen.add(guess.path)
        strip = snapshot.strip(guess.path)
        audible = strip is not None and not _silent(strip)
        if guess.role in (SpeakerRole.DELAY, SpeakerRole.FILL):
            code = "other_speakers_audible" if audible else "other_speakers_found"
            issues.append(Issue(code, {"path": guess.path.label(), "role": guess.role.value}))
        elif guess.role is SpeakerRole.MONITOR and audible:
            monitors.append(guess.path.label())
    if monitors:
        issues.append(Issue("monitors_on_outputs", {"paths": ", ".join(monitors)}))
    low = [g for g in guesses if g.role is not SpeakerRole.UNUSED and g.confidence < LOW_CONFIDENCE]
    if low:
        issues.append(Issue("confirm_roles", {"count": len(low)}))
    return issues


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
            found = _evidence(snapshot, output.source)
            if found is not None:
                evidence[output.source] = found
    top_paths = [
        ref
        for ref, ev in evidence.items()
        if ev.role in TOP_ROLES and ref.kind not in (K.MAIN_L, K.MAIN_R)
    ]
    for ref, role in _sides(snapshot, top_paths).items():
        ev = evidence[ref]
        evidence[ref] = _Evidence(role, ev.confidence, ev.reasons)
    guesses: list[OutputGuess] = []
    for output in snapshot.outputs:
        if output.source in evidence:
            strip = snapshot.strip(output.source)
            guesses.append(_guess(output, evidence[output.source], strip.name if strip else ""))
    tops = sorted(
        {g.path for g in guesses if g.role in TOP_ROLES}, key=lambda r: (r.kind.value, r.number)
    )
    subs = sorted({g.path for g in guesses if g.role is SpeakerRole.SUB}, key=lambda r: r.number)
    others = sorted(
        {g.path for g in guesses if g.role in (SpeakerRole.DELAY, SpeakerRole.FILL)},
        key=lambda r: r.number,
    )
    sub = subs[0] if subs else None
    return TopologyGuess(
        tuple(guesses),
        _variant(tops, sub),
        tuple(tops),
        sub,
        tuple(others),
        tuple(_issues(snapshot, guesses, tops, subs)),
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
