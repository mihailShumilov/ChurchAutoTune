"""Full read-only snapshot of an X32/M32: the node list, the read and parsing into domain models.

Parsing is pure and tolerant: a missing or malformed line leaves the default value and its path is
listed in ``ConsoleSnapshot.unparsed``, so one odd firmware reply never stops the wizard. The raw
lines are kept, so a snapshot file can be parsed again by a newer version.
"""

import math
import re
from collections.abc import Callable, Mapping
from typing import Protocol

from churchtune.domain.eq import Band, EqType
from churchtune.domain.snapshot import (
    ClockInfo,
    ConsoleIdentity,
    ConsoleSnapshot,
    Headamp,
    InputChannel,
    InputKind,
    InputRef,
    MixStrip,
    OutputDelay,
    OutputKind,
    PhysicalOutput,
    Send,
    SignalRef,
    SourceKind,
    UsbRouting,
)
from churchtune.mixers.x32 import routing
from churchtune.mixers.x32.dump import (
    AUX_OUTPUTS,
    BUS_EQ_BANDS,
    BUSES,
    CHANNEL_EQ_BANDS,
    CHANNELS,
    HEADAMPS,
    MAIN_OUTPUTS,
    MATRICES,
    MATRIX_SENDS,
    dump_nodes,
)
from churchtune.mixers.x32.params import parse_freq

ProgressCb = Callable[[int, int], None]
# Per-node wait during a snapshot. A console answers in a few ms; a short wait keeps a lossy
# network from stretching the read (one retry follows in the client).
SNAPSHOT_NODE_TIMEOUT_S = 0.15
CARD_BLOCKS = 4
INPUT_BLOCKS = 4
_NAME_RE = re.compile(r'^\S+\s+"([^"]*)"\s*(.*)$')
_FS_RE = re.compile(r"^(\d+)K$")
_CARD_CHANNELS_RE = re.compile(r"^(\d+)/(\d+)$")
_LINKS = (("config/chlink", "channel", CHANNELS), ("config/buslink", "bus", BUSES))
_LINKS_MTX = ("config/mtxlink", "matrix", MATRICES)
_FS_KILO = 1000


def snapshot_nodes() -> list[str]:
    """Every node a snapshot reads: the full dump plus matrix polarity and output delays."""
    extra = [f"mtx/{n:02d}/preamp" for n in range(1, MATRICES + 1)]
    extra += [f"outputs/main/{n:02d}/delay" for n in range(1, MAIN_OUTPUTS + 1)]
    return list(dict.fromkeys([*dump_nodes(), *extra]))


class NodeReader(Protocol):
    """The read-only client, as far as a snapshot needs it."""

    def node(self, path: str, timeout_s: float = ...) -> str | None:
        """Return the reply line for ``path``, or None when the console does not answer."""
        ...


def read_raw(reader: NodeReader, progress: ProgressCb | None = None) -> dict[str, str]:
    """Read every snapshot node; unanswered nodes are left out.

    Args:
        reader: Read-only client.
        progress: Called with ``(done, total)`` after each node.
    """
    nodes = snapshot_nodes()
    raw: dict[str, str] = {}
    for done, path in enumerate(nodes, start=1):
        line = reader.node(path, timeout_s=SNAPSHOT_NODE_TIMEOUT_S)
        if line is not None:
            raw[path] = line
        if progress is not None:
            progress(done, len(nodes))
    return raw


def raw_from_dump(text: str) -> dict[str, str]:
    """Node lines of a dump file by path; ``/ch/01/config ...`` is stored under ``ch/01/config``."""
    return {
        line.split(" ", 1)[0].lstrip("/"): line.rstrip()
        for line in text.splitlines()
        if line.startswith("/")
    }


class _Lines:
    """Access to raw lines that records what was missing or malformed."""

    def __init__(self, raw: Mapping[str, str]) -> None:
        self.raw = raw
        self.unparsed: list[str] = []

    def fields(self, path: str, minimum: int = 1) -> list[str] | None:
        """Values of a line (the path dropped), or None when missing or too short."""
        line = self.raw.get(path)
        parts = line.split()[1:] if line else []
        if len(parts) < minimum:
            self.unparsed.append(path)
            return None
        return parts

    def named(self, path: str) -> tuple[str, list[str]] | None:
        """Quoted name and the remaining values of a config line."""
        line = self.raw.get(path)
        match = _NAME_RE.match(line) if line else None
        if match is None:
            self.unparsed.append(path)
            return None
        return match.group(1).strip(), match.group(2).split()

    def bad(self, path: str) -> None:
        """Record a line that was present but not understood."""
        self.unparsed.append(path)


def _db(text: str) -> float:
    """A level in dB as printed by the console; ``-oo`` is minus infinity."""
    return -math.inf if text == "-oo" else float(text)


def _on(text: str) -> bool:
    return text.upper() == "ON"


def _band(lines: _Lines, path: str, number: int) -> Band | None:
    parts = lines.fields(path, 4)
    if parts is None:
        return None
    try:
        return Band(
            number, EqType(parts[0]), parse_freq(parts[1]), float(parts[2]), float(parts[3])
        )
    except ValueError:
        lines.bad(path)
        return None


def _eq(lines: _Lines, base: str, bands: int) -> tuple[bool, tuple[Band, ...]]:
    switch = lines.fields(f"{base}/eq")
    parsed = (_band(lines, f"{base}/eq/{b}", b) for b in range(1, bands + 1))
    return bool(switch and _on(switch[0])), tuple(b for b in parsed if b is not None)


def _send(lines: _Lines, path: str, target: int) -> Send | None:
    parts = lines.fields(path, 2)
    if parts is None:
        return None
    try:
        return Send(target, _on(parts[0]), _db(parts[1]))
    except ValueError:
        lines.bad(path)
        return None


def _sends(lines: _Lines, base: str, count: int) -> tuple[Send, ...]:
    parsed = (_send(lines, f"{base}/mix/{t:02d}", t) for t in range(1, count + 1))
    return tuple(s for s in parsed if s is not None)


def _mix(lines: _Lines, path: str) -> tuple[bool, float, list[str]]:
    """Switch, fader and the remaining values of a mix line; muted at -inf when unreadable."""
    parts = lines.fields(path, 2)
    if parts is None:
        return False, -math.inf, []
    try:
        return _on(parts[0]), _db(parts[1]), parts[2:]
    except ValueError:
        lines.bad(path)
        return False, -math.inf, []


def _input_sources(lines: _Lines) -> tuple[InputRef, ...]:
    user = lines.fields("config/userrout/in", routing.CHANNELS)
    user_ids = tuple(int(v) for v in user) if user else ()
    blocks = lines.fields("config/routing/IN", INPUT_BLOCKS)
    if blocks is None:
        return (InputRef(InputKind.UNKNOWN),) * routing.CHANNELS
    sources: list[InputRef] = []
    for block in blocks[:INPUT_BLOCKS]:
        sources.extend(routing.block_sources(block, user_ids))
    return tuple(sources)


def _channel(lines: _Lines, number: int, inputs: tuple[InputRef, ...]) -> InputChannel:
    base = f"ch/{number:02d}"
    named = lines.named(f"{base}/config")
    name, rest = named if named else ("", [])
    source = InputRef(InputKind.UNKNOWN)
    if rest and rest[-1].isdigit():
        listens = routing.channel_input(int(rest[-1]))
        if listens is None:
            source = InputRef(InputKind.OFF)
        elif listens[0] == "input":
            source = inputs[listens[1] - 1]
        else:
            source = InputRef(InputKind.AUX_IN, listens[1])
    on, fader_db, mix_rest = _mix(lines, f"{base}/mix")
    # mix: on fader st pan mono mlevel
    to_main = bool(mix_rest) and _on(mix_rest[0])
    to_mono = len(mix_rest) > 2 and _on(mix_rest[2])  # noqa: PLR2004 - mono switch position
    preamp = lines.fields(f"{base}/preamp", 5)  # trim invert hpon hpslope hpf
    hpf_on, hpf_hz = (_on(preamp[2]), float(preamp[4])) if preamp else (False, 0.0)
    eq_on, eq = _eq(lines, base, CHANNEL_EQ_BANDS)
    return InputChannel(
        number,
        name,
        source,
        on,
        fader_db,
        to_main,
        to_mono,
        _sends(lines, base, BUSES),
        hpf_on,
        hpf_hz,
        eq_on,
        eq,
    )


def _strip(lines: _Lines, base: str, ref: SignalRef, *, sends: bool) -> MixStrip:
    named = lines.named(f"{base}/config")
    name = named[0] if named else ""
    on, fader_db, rest = _mix(lines, f"{base}/mix")
    eq_on, eq = _eq(lines, base, BUS_EQ_BANDS)
    to_main = ref.kind is SourceKind.BUS and bool(rest) and _on(rest[0])
    invert = False
    if ref.kind is SourceKind.MATRIX:
        preamp = lines.fields(f"{base}/preamp")
        invert = bool(preamp) and _on(preamp[0]) if preamp else False
    matrix_sends = _sends(lines, base, MATRIX_SENDS) if sends else ()
    return MixStrip(ref, name, on, fader_db, eq_on, eq, to_main, matrix_sends, invert)


def _delay(lines: _Lines, number: int) -> OutputDelay | None:
    path = f"outputs/main/{number:02d}/delay"
    if path not in lines.raw:
        return None
    parts = lines.fields(path, 2)
    if parts is None:
        return None
    try:
        return OutputDelay(_on(parts[0]), float(parts[1]))
    except ValueError:
        lines.bad(path)
        return None


def _outputs(lines: _Lines) -> tuple[PhysicalOutput, ...]:
    found: list[PhysicalOutput] = []
    groups = ((OutputKind.MAIN, MAIN_OUTPUTS), (OutputKind.AUX, AUX_OUTPUTS))
    for kind, count in groups:
        for number in range(1, count + 1):
            path = f"outputs/{kind.value}/{number:02d}"
            parts = lines.fields(path)
            if parts is None or not parts[0].lstrip("-").isdigit():
                if parts is not None:
                    lines.bad(path)
                continue
            source_id = int(parts[0])
            delay = _delay(lines, number) if kind is OutputKind.MAIN else None
            found.append(
                PhysicalOutput(kind, number, routing.output_source(source_id), source_id, delay)
            )
    return tuple(found)


def _headamps(lines: _Lines) -> dict[int, Headamp]:
    found: dict[int, Headamp] = {}
    for index in range(HEADAMPS):
        path = f"headamp/{index:03d}"
        if path not in lines.raw:
            continue
        parts = lines.fields(path, 2)
        if parts is None:
            continue
        try:
            found[index] = Headamp(index, float(parts[0]), _on(parts[1]))
        except ValueError:
            lines.bad(path)
    return found


def _usb(lines: _Lines, inputs: tuple[InputRef, ...]) -> UsbRouting:
    blocks = lines.fields("config/routing/CARD", CARD_BLOCKS)
    to_computer: list[InputRef] = []
    for block in (blocks or [])[:CARD_BLOCKS]:
        to_computer.extend(routing.block_sources(block))
    from_computer = {
        index: source.number
        for index, source in enumerate(inputs, start=1)
        if source.kind is InputKind.CARD
    }
    channels = routing.CHANNELS
    card = lines.raw.get("-prefs/card", "").split()
    for token in card[1:]:
        match = _CARD_CHANNELS_RE.match(token)
        if match:
            channels = int(match.group(1))
            break
    return UsbRouting(tuple(to_computer), from_computer, channels)


def _clock(lines: _Lines) -> ClockInfo | None:
    tokens = lines.raw.get("-prefs", "").split()
    for position, token in enumerate(tokens[:-1]):
        match = _FS_RE.match(token)
        if match:
            return ClockInfo(int(match.group(1)) * _FS_KILO, tokens[position + 1])
    lines.bad("-prefs")
    return None


def _links(lines: _Lines) -> frozenset[tuple[str, int]]:
    links: set[tuple[str, int]] = set()
    for path, kind, count in (*_LINKS, _LINKS_MTX):
        parts = lines.fields(path)
        if parts is None:
            continue
        # The first value of chlink/buslink is a pair, as for mtxlink: pair k links 2k-1 and 2k.
        for pair, value in enumerate(parts[: count // 2], start=1):
            if _on(value):
                links.add((kind, 2 * pair - 1))
    return frozenset(links)


def parse_snapshot(
    identity: ConsoleIdentity, raw: Mapping[str, str], taken_at: str
) -> ConsoleSnapshot:
    """Build a snapshot from raw node lines.

    Args:
        identity: The console's ``/xinfo`` answer.
        raw: Reply lines by node path, as read or as loaded from a dump or a snapshot file.
        taken_at: ISO time of the read.
    """
    lines = _Lines(raw)
    inputs = _input_sources(lines)
    channels = {n: _channel(lines, n, inputs) for n in range(1, CHANNELS + 1)}
    buses = {
        n: _strip(lines, f"bus/{n:02d}", SignalRef(SourceKind.BUS, n), sends=True)
        for n in range(1, BUSES + 1)
    }
    matrices = {
        n: _strip(lines, f"mtx/{n:02d}", SignalRef(SourceKind.MATRIX, n), sends=False)
        for n in range(1, MATRICES + 1)
    }
    main = _strip(lines, "main/st", SignalRef(SourceKind.MAIN_L), sends=True)
    mono = _strip(lines, "main/m", SignalRef(SourceKind.MONO), sends=True)
    mono_fields = lines.fields("config/mono")
    return ConsoleSnapshot(
        identity=identity,
        taken_at=taken_at,
        channels=channels,
        buses=buses,
        matrices=matrices,
        main=main,
        mono=mono,
        outputs=_outputs(lines),
        headamps=_headamps(lines),
        input_sources=inputs,
        usb=_usb(lines, inputs),
        clock=_clock(lines),
        stereo_links=_links(lines),
        mono_mode=mono_fields[0] if mono_fields else "",
        raw=dict(raw),
        unparsed=tuple(dict.fromkeys(lines.unparsed)),
    )
