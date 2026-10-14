"""The things the wizard must explain about a topology guess (SPEC S4)."""

from churchtune.domain.snapshot import ConsoleSnapshot, InputKind, SignalRef
from churchtune.domain.snapshot import SourceKind as K
from churchtune.domain.topology import TOP_ROLES, Issue, OutputGuess, SpeakerRole
from churchtune.mixers.x32.filters import is_low_cut
from churchtune.mixers.x32.speaker_paths import (
    MAIN_SOURCES,
    SpeakerPaths,
    feeders,
    is_silent,
    is_sub_pair,
)

LOW_CONFIDENCE = 0.6
MAIN_HPF_STARVES_SUB_HZ = 50.0


def _playback_reaches_bus(snapshot: ConsoleSnapshot, bus: int) -> bool:
    """True when a channel fed by the computer has an open send into ``bus``."""
    return any(
        ch.source.kind is InputKind.CARD and any(s.target == bus and s.open for s in ch.sends)
        for ch in snapshot.channels.values()
    )


def _main_filtertopology_issues(snapshot: ConsoleSnapshot) -> list[Issue]:
    """A high-pass on Main LR reaches every matrix fed from it, the sub included."""
    main = snapshot.main
    if not main.eq_on:
        return []
    for band in main.eq:
        if is_low_cut(band) and band.freq_hz >= MAIN_HPF_STARVES_SUB_HZ:
            return [Issue("main_hpf_feeds_sub", {"freq_hz": band.freq_hz})]
    return []


def _one_subtopology_issues(snapshot: ConsoleSnapshot, sub: SignalRef) -> list[Issue]:
    if sub.kind is K.MONO:
        return [Issue("sub_on_mono_suggest_only")]
    if sub.kind is K.BUS:
        issues = [Issue("sub_on_bus_suggest_only", {"bus": sub.number})]
        if not _playback_reaches_bus(snapshot, sub.number):
            issues.append(Issue("playback_not_sent_to_sub_bus", {"bus": sub.number}))
        return issues
    fed = {f.kind for f in feeders(snapshot, sub.number)}
    if not fed:
        return [Issue("sub_not_fed", {"matrix": sub.number})]
    issues = []
    if {K.MAIN_L, K.MONO} <= fed:
        issues.append(Issue("sub_fed_by_main_and_mono", {"matrix": sub.number}))
    if K.MAIN_L in fed:
        issues += _main_filtertopology_issues(snapshot)
    return issues


def _subtopology_issues(snapshot: ConsoleSnapshot, paths: SpeakerPaths) -> list[Issue]:
    subs = paths.subs
    issues: list[Issue] = []
    if len(subs) > 1:
        code = "sub_pair" if is_sub_pair(snapshot, subs) else "several_subs"
        issues.append(Issue(code, {"paths": ", ".join(s.label() for s in subs)}))
    for sub in subs:
        issues += _one_subtopology_issues(snapshot, sub)
    if not subs and paths.tops:
        issues.append(Issue("no_sub_path_ask"))
    if subs and any(t.kind in MAIN_SOURCES for t in paths.tops):
        issues.append(Issue("crossover_hpf_suggest_only"))
    return _unique(issues)


def _unique(issues: list[Issue]) -> list[Issue]:
    """Issues without repeats, in order (two sub matrices can raise the same issue)."""
    seen: set[tuple[str, tuple[tuple[str, object], ...]]] = set()
    unique: list[Issue] = []
    for issue in issues:
        key = (issue.code, tuple(sorted((issue.params or {}).items())))
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


def _pathtopology_issues(snapshot: ConsoleSnapshot, guesses: list[OutputGuess]) -> list[Issue]:
    issues: list[Issue] = []
    monitors: list[str] = []
    seen: set[SignalRef] = set()
    for guess in guesses:
        if guess.path in seen:
            continue
        seen.add(guess.path)
        strip = snapshot.strip(guess.path)
        audible = strip is not None and not is_silent(strip)
        if guess.role in (SpeakerRole.DELAY, SpeakerRole.FILL):
            code = "other_speakers_audible" if audible else "other_speakers_found"
            issues.append(Issue(code, {"path": guess.path.label(), "role": guess.role.value}))
        elif guess.role is SpeakerRole.ZONE:
            issues.append(Issue("zone_will_hear_sweep", {"path": guess.path.label()}))
        elif guess.role is SpeakerRole.MONITOR and audible:
            monitors.append(guess.path.label())
    if monitors:
        issues.append(Issue("monitors_on_outputs", {"paths": ", ".join(monitors)}))
    return issues


def _alignmenttopology_issues(snapshot: ConsoleSnapshot, guesses: list[OutputGuess]) -> list[Issue]:
    """Delays and polarity already set on speaker paths: the start point of the alignment."""
    speakers = {SpeakerRole.SUB, *TOP_ROLES}
    outputs = {(o.kind, o.number): o for o in snapshot.outputs}
    found: list[str] = []
    for guess in guesses:
        if guess.role not in speakers:
            continue
        delay = outputs[(guess.kind, guess.number)].delay
        if delay is not None and delay.on:
            found.append(f"{guess.kind.value} {guess.number}: {delay.delay_ms:.1f} ms")
        strip = snapshot.strip(guess.path)
        if strip is not None and strip.invert:
            found.append(f"{guess.path.label()}: polarity inverted")
    if not found:
        return []
    return [Issue("existing_alignment", {"items": "; ".join(dict.fromkeys(found))})]


def needs_confirmation(guess: OutputGuess) -> bool:
    """True when the wizard must ask about this output (low confidence on a speaker path)."""
    return guess.role is not SpeakerRole.UNUSED and guess.confidence < LOW_CONFIDENCE


def topology_issues(
    snapshot: ConsoleSnapshot, guesses: list[OutputGuess], paths: SpeakerPaths
) -> list[Issue]:
    """Every issue of a guess: sub path, other speakers, existing alignment, confirmation."""
    issues = _subtopology_issues(snapshot, paths)
    issues += _pathtopology_issues(snapshot, guesses)
    issues += _alignmenttopology_issues(snapshot, guesses)
    if snapshot.mono_mode.upper() == "LCR" and any(t.kind is K.MONO for t in paths.tops):
        issues.append(Issue("lcr_not_supported"))
    low = [g for g in guesses if needs_confirmation(g)]
    if low:
        issues.append(Issue("confirm_roles", {"count": len(low)}))
    return issues
