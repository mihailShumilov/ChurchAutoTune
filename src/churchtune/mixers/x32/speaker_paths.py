"""What feeds a speaker path, whether it is silent, and which side of a stereo link it is on."""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from churchtune.domain.snapshot import ConsoleSnapshot, LinkKind, MixStrip, SignalRef
from churchtune.domain.snapshot import SourceKind as K
from churchtune.mixers.x32.names import Side, side_hint

MAIN_SOURCES = frozenset({K.MAIN_L, K.MAIN_R})
LINK_KINDS = {K.MATRIX: LinkKind.MATRIX, K.BUS: LinkKind.BUS}
PAIR = 2


@dataclass(frozen=True, slots=True)
class SpeakerPaths:
    """Speaker paths of a guess, by role group."""

    tops: tuple[SignalRef, ...]
    subs: tuple[SignalRef, ...]
    others: tuple[SignalRef, ...]
    zones: tuple[SignalRef, ...]


def is_silent(strip: MixStrip) -> bool:
    """True when a strip passes no signal: muted or fader at minus infinity."""
    return not strip.on or strip.fader_db == -math.inf


def feeders(snapshot: ConsoleSnapshot, matrix: int) -> tuple[SignalRef, ...]:
    """Strips with an open send into ``matrix``: Main LR, Main Mono and unmuted buses."""
    strips: list[MixStrip] = [snapshot.main]
    if snapshot.mono is not None:
        strips.append(snapshot.mono)
    strips += list(snapshot.buses.values())
    found: list[SignalRef] = []
    for strip in strips:
        sends_open = any(s.target == matrix and s.open for s in strip.matrix_sends)
        # Main LR always counts (it carries the programme); a muted Mono or bus feeds nothing.
        if sends_open and (strip.ref.kind is K.MAIN_L or not is_silent(strip)):
            found.append(strip.ref)
    return tuple(found)


def linked_side(snapshot: ConsoleSnapshot, ref: SignalRef) -> Side | None:
    """Side of a path inside a stereo-linked pair (odd = left), or None when unlinked."""
    pair_start = ref.number if ref.number % 2 else ref.number - 1
    kind = LINK_KINDS.get(ref.kind)
    if kind is None or (kind, pair_start) not in snapshot.stereo_links:
        return None
    return Side.LEFT if ref.number % 2 else Side.RIGHT


def is_sub_pair(snapshot: ConsoleSnapshot, subs: Sequence[SignalRef]) -> bool:
    """Two sub paths that form one stereo sub: neighbours in a stereo link, or named L and R."""
    if len(subs) != PAIR:
        return False
    first, second = subs
    neighbours = first.kind is second.kind and abs(first.number - second.number) == 1
    if neighbours and linked_side(snapshot, first) is not None:
        return True
    strips = (snapshot.strip(first), snapshot.strip(second))
    return {side_hint(s.name) for s in strips if s is not None} == {Side.LEFT, Side.RIGHT}
