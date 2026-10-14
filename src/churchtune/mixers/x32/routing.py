"""X32/M32 routing ids: output sources, input routing blocks, user routing and headamp numbers.

Source: P.-G. Maillot, "Unofficial X32/M32 OSC Remote Protocol", cross-checked against the real
dumps in ``tests/fixtures/x32/dumps``. What the dumps confirm is noted next to each table; the rest
is marked ``UNVERIFIED`` and listed in ``docs/hardware-checklist.md`` (rows 10, 15, 16).
"""

import re

from churchtune.domain.snapshot import (
    ConsoleSnapshot,
    Headamp,
    InputKind,
    InputRef,
    SignalRef,
    SourceKind,
)

BUSES, MATRICES, CHANNELS, AUX_INS, FX_RETURNS = 16, 6, 32, 8, 8
BLOCK_SIZE = 8

# ------------------------------------------------------------------ output sources
# Source id of ``/outputs/main/NN`` and ``/outputs/aux/NN``.
# Confirmed by the dumps: 4..19 are mix buses 1..16 (bus 1 "Monitor" feeds output 1) and 20..25 are
# matrices 1..6 (matrix 3 "Sub buffer" feeds output 6).
# UNVERIFIED: 0..3 and 26..76 (docs/hardware-checklist.md #10, #15).
_OUT_MAIN_L, _OUT_MAIN_R, _OUT_MONO = 1, 2, 3
_OUT_FIRST_BUS = 4
_OUT_FIRST_MATRIX = _OUT_FIRST_BUS + BUSES  # 20
_OUT_FIRST_DIRECT_CH = _OUT_FIRST_MATRIX + MATRICES  # 26
_OUT_FIRST_DIRECT_AUX = _OUT_FIRST_DIRECT_CH + CHANNELS  # 58
_OUT_FIRST_DIRECT_FX = _OUT_FIRST_DIRECT_AUX + AUX_INS  # 66
_OUT_MONITOR_L = _OUT_FIRST_DIRECT_FX + FX_RETURNS  # 74
_OUT_MONITOR_R = _OUT_MONITOR_L + 1
_OUT_TALKBACK = _OUT_MONITOR_R + 1


def _in_range(value: int, first: int, count: int) -> int | None:
    """1-based position of ``value`` in ``first .. first+count-1``, or None."""
    return value - first + 1 if first <= value < first + count else None


def output_source(source_id: int) -> SignalRef:
    """Mix signal behind an output source id; ``unknown`` for ids outside the table."""
    fixed = {
        0: SignalRef(SourceKind.OFF),
        _OUT_MAIN_L: SignalRef(SourceKind.MAIN_L),
        _OUT_MAIN_R: SignalRef(SourceKind.MAIN_R),
        _OUT_MONO: SignalRef(SourceKind.MONO),
        _OUT_MONITOR_L: SignalRef(SourceKind.MONITOR, 1),
        _OUT_MONITOR_R: SignalRef(SourceKind.MONITOR, 2),
        _OUT_TALKBACK: SignalRef(SourceKind.TALKBACK),
    }
    if source_id in fixed:
        return fixed[source_id]
    ranges = (
        (SourceKind.BUS, _OUT_FIRST_BUS, BUSES),
        (SourceKind.MATRIX, _OUT_FIRST_MATRIX, MATRICES),
        (SourceKind.DIRECT_CHANNEL, _OUT_FIRST_DIRECT_CH, CHANNELS),
        (SourceKind.DIRECT_AUX, _OUT_FIRST_DIRECT_AUX, AUX_INS),
        (SourceKind.DIRECT_FX, _OUT_FIRST_DIRECT_FX, FX_RETURNS),
    )
    for kind, first, count in ranges:
        number = _in_range(source_id, first, count)
        if number is not None:
            return SignalRef(kind, number)
    return SignalRef(SourceKind.UNKNOWN, source_id)


# ------------------------------------------------------------------ user routing ids
# Ids of ``/config/userrout/in``. Confirmed by the dumps: 1..32 local inputs (13, 15), 33..80
# AES50-A 1..48 (33..64), 129..160 card 1..32 (129..132 carry the computer's playback).
# UNVERIFIED: 81..128 AES50-B and 161.. aux inputs (docs/hardware-checklist.md #15).
_USER_RANGES: tuple[tuple[InputKind, int, int], ...] = (
    (InputKind.LOCAL, 1, 32),
    (InputKind.AES50_A, 33, 48),
    (InputKind.AES50_B, 81, 48),
    (InputKind.CARD, 129, 32),
    (InputKind.AUX_IN, 161, 6),
)


def user_input_source(user_id: int) -> InputRef:
    """Physical source behind a user-routing id; ``off`` for 0, ``unknown`` outside the table."""
    if user_id == 0:
        return InputRef(InputKind.OFF)
    for kind, first, count in _USER_RANGES:
        number = _in_range(user_id, first, count)
        if number is not None:
            return InputRef(kind, number)
    return InputRef(InputKind.UNKNOWN)


# ------------------------------------------------------------------ routing blocks
# Names of 8-channel blocks in ``/config/routing/IN`` and ``/config/routing/CARD``:
# ``AN1-8`` local inputs, ``A9-16`` AES50-A, ``B1-8`` AES50-B, ``CARD1-8`` the card, ``UIN1-8`` user
# routing, ``OUT1-8`` outputs, ``P161-8`` P16 outputs. The dumps confirm ``UIN``, ``A``, ``OUT`` and
# ``P16``. UNVERIFIED: ``AN``, ``B``, ``CARD`` and any other name (docs/hardware-checklist.md #15).
_BLOCK_RE = re.compile(r"^(AN|CARD|UIN|UOUT|P16|OUT|A|B)(\d+)-(\d+)$")
_BLOCK_KINDS: dict[str, InputKind] = {
    "AN": InputKind.LOCAL,
    "A": InputKind.AES50_A,
    "B": InputKind.AES50_B,
    "CARD": InputKind.CARD,
    "OUT": InputKind.OUTPUT,
    "P16": InputKind.P16,
}
USER_BLOCK = "UIN"


def parse_block(name: str) -> tuple[str, int, int] | None:
    """Split a block name into prefix, first and last number: ``A9-16`` gives ``("A", 9, 16)``."""
    match = _BLOCK_RE.fullmatch(name)
    if not match:
        return None
    prefix, first, last = match.groups()
    if int(last) - int(first) + 1 != BLOCK_SIZE:
        return None  # every routing block carries exactly eight channels
    return prefix, int(first), int(last)


def block_sources(name: str, user_ids: tuple[int, ...] = ()) -> tuple[InputRef, ...]:
    """Sources of the eight channels of one routing block.

    Args:
        name: Block name as the console prints it.
        user_ids: ``/config/userrout/in`` ids, needed for ``UIN`` blocks.

    Returns:
        Eight sources; ``unknown`` for a block name this table does not know.
    """
    parsed = parse_block(name)
    if parsed is None:
        return (InputRef(InputKind.UNKNOWN),) * BLOCK_SIZE
    prefix, first, last = parsed
    numbers = range(first, last + 1)
    if prefix == USER_BLOCK:
        return tuple(
            user_input_source(user_ids[n - 1])
            if n <= len(user_ids)
            else InputRef(InputKind.UNKNOWN)
            for n in numbers
        )
    kind = _BLOCK_KINDS.get(prefix, InputKind.UNKNOWN)
    if kind is InputKind.UNKNOWN:
        return (InputRef(InputKind.UNKNOWN),) * BLOCK_SIZE
    return tuple(InputRef(kind, n) for n in numbers)


# ------------------------------------------------------------------ headamps
# Headamp index of a physical input. Confirmed: AES50-A 10 is headamp 041 (the measurement mic,
# +48 V on). UNVERIFIED: local inputs 000..031 and AES50-B 080..127
# (docs/hardware-checklist.md #16).
_HEADAMP_FIRST: dict[InputKind, tuple[int, int]] = {
    InputKind.LOCAL: (0, 32),
    InputKind.AES50_A: (32, 48),
    InputKind.AES50_B: (80, 48),
}


def headamp_index(source: InputRef) -> int | None:
    """Headamp index of a physical input; None for sources without a preamp (card, outputs)."""
    span = _HEADAMP_FIRST.get(source.kind)
    if span is None or not 1 <= source.number <= span[1]:
        return None
    return span[0] + source.number - 1


def headamp_of(snapshot: ConsoleSnapshot, source: InputRef) -> Headamp | None:
    """The preamp behind a physical input in a snapshot, or None."""
    index = headamp_index(source)
    return None if index is None else snapshot.headamps.get(index)


# ------------------------------------------------------------------ channel sources
# ``/ch/NN/config`` source: 0 off, 1..32 console inputs, 33..38 aux inputs (dump: auxin 1 -> 33).
# UNVERIFIED: ids above 38 (USB, FX returns, buses); docs/hardware-checklist.md #15.
_CH_FIRST_AUX = 33
_CH_AUX_COUNT = 6


def channel_input(source_id: int) -> tuple[str, int]:
    """What a channel listens to.

    ``("input", 5)``, ``("aux", 1)``, ``("off", 0)``, or ``("unknown", id)`` for ids this table
    does not know (USB, FX returns, buses).
    """
    if source_id == 0:
        return "off", 0
    if 1 <= source_id <= CHANNELS:
        return "input", source_id
    number = _in_range(source_id, _CH_FIRST_AUX, _CH_AUX_COUNT)
    return ("aux", number) if number is not None else ("unknown", source_id)
