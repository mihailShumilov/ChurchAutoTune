"""The channel audit (SPEC §8.5, S11 item 4): advice on the input channels, never a change.

After the system is tuned, cuts that compensated the room on many channels may no longer be
needed, and some channel settings are risky. The audit reads the snapshot and reports findings;
the person decides, one channel at a time, by ear. Nothing here is ever written to the console.

Narrow cuts are reported apart: they are most often feedback protection from a ring-out, and
taking them out is the quickest way to feedback.
"""

import math
import re
from collections.abc import Callable, Collection
from dataclasses import dataclass
from enum import StrEnum

from churchtune.domain.eq import Band, EqType
from churchtune.domain.snapshot import ConsoleSnapshot, Headamp, InputChannel, InputKind, InputRef

# Names that mark a voice: speech and singing (en, ru, uk, es, pt), compared in lower case.
VOICE_WORDS = (
    "vox", "voc", "voice", "vocal", "speech", "pastor", "preach", "pulpit", "lectern",
    "bgv", "choir", "radio", "wireless", "headset",  # wireless microphones in a church: voices
    "вокал", "голос", "пастор", "пропов", "речь", "кафедр", "ведущ", "радио", "гарнитур",
    "бэк", "хор", "спів", "радіо",
    "voz", "predic", "púlpito", "coro",
)  # fmt: skip
# Words that mark a voice even next to an instrument name.
STRONG_VOICE_WORDS = ("vox", "voc", "вокал", "голос", "спів")
# Whole words that also mark a voice, too short to match inside other words.
VOICE_TOKENS = frozenset({"bv", "mc", "lead vox", "lead voc"})
# Instruments, which a "radio" or "lead" name can also carry: never treated as a voice.
INSTRUMENT_WORDS = (
    "gtr", "guitar", "bass", "keys", "piano", "sax", "violin", "drum", "kick", "snare",
    "гит", "бас", "клав", "скрип", "пиан", "барабан",
)  # fmt: skip
# Names that mark a dynamic microphone (phantom power is useless there; harmless for most, but
# a hint that the strip may be set up for a different microphone).
DYNAMIC_WORDS = ("sm58", "sm57", "beta58", "beta57", "e835", "e845", "e935", "md421", "re20", "sm7")
# Speech on headsets and lavaliers in a reverberant church is often cut this high on purpose.
VOICE_HPF_MAX_HZ = 250.0
DEEP_CUT_DB = -9.0  # cuts deeper than this are reported
BIG_BOOST_DB = 6.0
NOTCH_Q = 4.0  # cuts at least this narrow are probably feedback protection
SHARED_CUT_CHANNELS = 3  # this many channels with the same cut look like room compensation
SHARED_CUT_OCT = 1 / 3
ROOM_MATCH_OCT = 1 / 2  # a shared cut this close to a system-EQ cut is called room compensation
SHARED_CUT_DB = -3.0
MAX_SHARED_FINDINGS = 3
EQ_TYPES = frozenset({EqType.PEQ, EqType.VEQ, EqType.LSHV, EqType.HSHV})


class AuditCode(StrEnum):
    """What the audit found."""

    VOICE_NO_HPF = "voice-no-hpf"
    VOICE_HPF_HIGH = "voice-hpf-high"
    POSSIBLE_FEEDBACK_NOTCH = "possible-feedback-notch"
    SHARED_CUT_ROOM = "shared-cut-room"
    SHARED_CUT = "shared-cut"
    DEEP_CUT = "deep-cut"
    BIG_BOOST = "big-boost"
    PHANTOM_ON_DYNAMIC = "phantom-on-dynamic"


@dataclass(frozen=True, slots=True)
class Finding:
    """One piece of advice.

    Attributes:
        code: What it is.
        channels: Channel numbers it is about.
        freq_hz: Frequency involved, when there is one.
        gain_db: Gain involved, when there is one.
    """

    code: AuditCode
    channels: tuple[int, ...]
    freq_hz: float | None = None
    gain_db: float | None = None


def is_voice(channel: InputChannel) -> bool:
    """True when the channel's name marks speech or singing (and no instrument)."""
    name = channel.name.lower()
    if any(word in name for word in STRONG_VOICE_WORDS):
        return True  # "Bass Vox", "бас вокал": a singer, whatever else the name says
    if any(word in name for word in INSTRUMENT_WORDS):
        return False
    words = set(re.findall(r"[\w]+", name))
    return any(word in name for word in VOICE_WORDS) or bool(
        words & VOICE_TOKENS or any(token in name for token in VOICE_TOKENS if " " in token)
    )


def _active_bands(channel: InputChannel) -> list[Band]:
    if not channel.eq_on:
        return []
    return [b for b in channel.eq if b.type in EQ_TYPES and b.gain_db != 0.0]


def _is_notch(band: Band) -> bool:
    return band.gain_db < 0 and band.type in (EqType.PEQ, EqType.VEQ) and band.q >= NOTCH_Q


def _used(channel: InputChannel) -> bool:
    """A channel that carries something: a physical source, and unmuted or named."""
    return channel.source.kind not in (InputKind.OFF, InputKind.UNKNOWN) and (
        channel.on or bool(channel.name)
    )


def _octaves(a: float, b: float) -> float:
    return math.log2(a / b)


def _shared_cuts(channels: list[InputChannel], system_cuts_hz: Collection[float]) -> list[Finding]:
    cuts = [
        (ch.number, band)
        for ch in channels
        for band in _active_bands(ch)
        if band.gain_db <= SHARED_CUT_DB
        and band.type in (EqType.PEQ, EqType.VEQ)
        and not _is_notch(band)
    ]
    findings: list[Finding] = []
    used: set[tuple[int, int]] = set()
    for number, band in sorted(cuts, key=lambda c: c[1].freq_hz):
        if (number, band.band) in used:
            continue
        group = [
            (n, b)
            for n, b in cuts
            if (n, b.band) not in used and abs(_octaves(b.freq_hz, band.freq_hz)) <= SHARED_CUT_OCT
        ]
        channels_in = sorted({n for n, _ in group})
        if len(channels_in) < SHARED_CUT_CHANNELS:
            continue
        used.update((n, b.band) for n, b in group)
        freq = sum(b.freq_hz for _, b in group) / len(group)
        room = any(abs(_octaves(freq, f)) <= ROOM_MATCH_OCT for f in system_cuts_hz)
        findings.append(
            Finding(
                AuditCode.SHARED_CUT_ROOM if room else AuditCode.SHARED_CUT,
                tuple(channels_in),
                freq_hz=round(freq),
                gain_db=round(sum(b.gain_db for _, b in group) / len(group)),
            )
        )
    return findings


def _grouped(code: AuditCode, items: list[tuple[int, Band]]) -> Finding | None:
    if not items:
        return None
    extreme = max(items, key=lambda i: abs(i[1].gain_db))[1]
    return Finding(
        code,
        tuple(sorted({n for n, _ in items})),
        freq_hz=round(extreme.freq_hz),
        gain_db=extreme.gain_db,
    )


def audit_channels(
    snapshot: ConsoleSnapshot,
    headamp_of: Callable[[InputRef], Headamp | None] | None = None,
    system_cuts_hz: Collection[float] = (),
) -> tuple[Finding, ...]:
    """Advice on the input channels, most important first.

    Args:
        snapshot: The console as read.
        headamp_of: The preamp behind a physical input (from the driver); without it the
            phantom check is skipped.
        system_cuts_hz: Frequencies the proposed system EQ cuts; a cut shared by many channels
            near one of them is called room compensation, otherwise only a shared cut.
    """
    channels = [ch for ch in snapshot.channels.values() if _used(ch)]
    findings: list[Finding] = []
    voices = [ch for ch in channels if is_voice(ch)]
    no_hpf = tuple(ch.number for ch in voices if not ch.hpf_on)
    if no_hpf:
        findings.append(Finding(AuditCode.VOICE_NO_HPF, no_hpf))
    high = [ch for ch in voices if ch.hpf_on and ch.hpf_hz > VOICE_HPF_MAX_HZ]
    if high:
        findings.append(
            Finding(
                AuditCode.VOICE_HPF_HIGH,
                tuple(ch.number for ch in high),
                freq_hz=round(max(ch.hpf_hz for ch in high)),
            )
        )
    notches = [(ch.number, b) for ch in channels for b in _active_bands(ch) if _is_notch(b)]
    findings.extend(f for f in (_grouped(AuditCode.POSSIBLE_FEEDBACK_NOTCH, notches),) if f)
    shared = sorted(_shared_cuts(channels, system_cuts_hz), key=lambda f: -len(f.channels))
    findings.extend(shared[:MAX_SHARED_FINDINGS])
    in_shared = {(n, f.freq_hz) for f in shared[:MAX_SHARED_FINDINGS] for n in f.channels}
    deep: list[tuple[int, Band]] = []
    boosts: list[tuple[int, Band]] = []
    for ch in channels:
        for band in _active_bands(ch):
            if _is_notch(band):
                continue
            near_shared = any(
                n == ch.number and freq and abs(_octaves(band.freq_hz, freq)) <= SHARED_CUT_OCT
                for n, freq in in_shared
            )
            if band.gain_db < DEEP_CUT_DB and not near_shared:
                deep.append((ch.number, band))
            elif band.gain_db > BIG_BOOST_DB:
                boosts.append((ch.number, band))
    findings.extend(
        f for f in (_grouped(AuditCode.DEEP_CUT, deep), _grouped(AuditCode.BIG_BOOST, boosts)) if f
    )
    if headamp_of is not None:
        phantom = [
            ch.number
            for ch in channels
            if any(word in ch.name.lower() for word in DYNAMIC_WORDS)
            and (amp := headamp_of(ch.source)) is not None
            and amp.phantom
        ]
        if phantom:
            findings.append(Finding(AuditCode.PHANTOM_ON_DYNAMIC, tuple(phantom)))
    return tuple(findings)
