"""Parsing of ``/node`` reply lines into domain models, and reading them through a client.

``/node`` strings are human-readable and formatted, not raw: frequency ``232.3`` / ``1k97``, gain
``+0.00`` / ``-15.0``, Q ``3.9``. They are parsed with the display parsers from ``params``.
"""

import math
import re
from collections.abc import Sequence
from typing import Protocol

from churchtune.domain.console import ConsoleState, MatrixState, OutputDelay
from churchtune.domain.eq import BANDS_PER_EQ, Band, EqType, SystemEq
from churchtune.errors import ConsoleUnreachable
from churchtune.mixers.x32.params import parse_freq

MAIN_EQ_NODE = "main/st/eq"
MATRIX_COUNT = 6
MAIN_OUTPUT_COUNT = 16
# Source id of a main output: 20..25 are matrices 1..6 (seen in the real dump: matrix 3 -> 22).
OUTPUT_SOURCE_FIRST_MATRIX = 20

_MAIN_BAND_RE = re.compile(r"^/?main/st/eq/([1-6])\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)")
_ANY_BAND_RE = re.compile(
    r"^/?((?:main/st|mtx/0[1-6]|bus/(?:0[1-9]|1[0-6]))/eq)/([1-6])\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)"
)
_MIN_ON_LINE_PARTS = 2


def _band(line: str, fields: Sequence[str]) -> Band:
    """Build a band from the five fields of a band line: number, type, frequency, gain, Q."""
    number, type_name, freq, gain, q = fields
    try:
        eq_type = EqType(type_name)
    except ValueError:
        raise ValueError(f"unknown EQ type {type_name!r} in {line!r}") from None
    return Band(int(number), eq_type, parse_freq(freq), float(gain), float(q))


def parse_eq_node_line(line: str) -> Band:
    """Parse a Main LR band line: ``/main/st/eq/2 PEQ 232.3 -1.75 3.9``.

    Raises:
        ValueError: The line is not a Main LR EQ band line or names an unknown type.
    """
    match = _MAIN_BAND_RE.match(line.strip())
    if not match:
        raise ValueError(f"not a main EQ band line: {line!r}")
    return _band(line, match.groups())


def parse_band_line(line: str) -> tuple[str, Band]:
    """Parse a band line of the main, a matrix or a bus EQ.

    ``/mtx/03/eq/6 HShv 751.7 -15.0 2.0`` gives ``("mtx/03/eq", Band(6, HShv, ...))``.

    Raises:
        ValueError: The line is not an EQ band line or names an unknown type.
    """
    match = _ANY_BAND_RE.match(line.strip())
    if not match:
        raise ValueError(f"not an EQ band line: {line!r}")
    prefix, *fields = match.groups()
    return prefix, _band(line, fields)


def parse_eq_on_line(line: str) -> bool:
    """Parse the Main LR EQ switch line: ``/main/st/eq ON`` gives True.

    Raises:
        ValueError: The line is not the Main LR EQ on/off line.
    """
    parts = line.strip().split()
    if len(parts) < _MIN_ON_LINE_PARTS or parts[0].lstrip("/") != MAIN_EQ_NODE:
        raise ValueError(f"not a main EQ on/off line: {line!r}")
    return parts[1].upper() == "ON"


def parse_eq_from_dump(text: str) -> SystemEq | None:
    """Extract the Main LR EQ from a full console dump; None when it is incomplete."""
    on: bool | None = None
    bands: dict[int, Band] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(f"/{MAIN_EQ_NODE}/"):
            band = parse_eq_node_line(line)
            bands[band.band] = band
        elif line.split(" ")[0] == f"/{MAIN_EQ_NODE}":
            on = parse_eq_on_line(line)
    if on is None or len(bands) != BANDS_PER_EQ:
        return None
    return SystemEq(on, tuple(bands[i] for i in range(1, BANDS_PER_EQ + 1)))


class NodeReader(Protocol):
    """Anything that can read one ``/node`` line (the read-only client)."""

    def node(self, path: str) -> str | None:
        """Return the reply line for ``path``, or None when the console does not answer."""
        ...


def _require_node(reader: NodeReader, path: str) -> str:
    line = reader.node(path)
    if line is None:
        raise ConsoleUnreachable(f"no answer to /node {path}", node=path)
    return line


def read_main_eq(reader: NodeReader) -> SystemEq:
    """Read the Main LR EQ (switch and six bands).

    Raises:
        ConsoleUnreachable: A node was not answered.
    """
    on = parse_eq_on_line(_require_node(reader, MAIN_EQ_NODE))
    bands = tuple(
        parse_eq_node_line(_require_node(reader, f"{MAIN_EQ_NODE}/{number}"))
        for number in range(1, BANDS_PER_EQ + 1)
    )
    return SystemEq(on, bands)


def parse_mix_line(line: str) -> tuple[bool, float]:
    """Parse a mix line: ``/mtx/03/mix ON -37.5`` gives ``(True, -37.5)``; ``-oo`` is ``-inf``.

    Raises:
        ValueError: The line has no switch and level.
    """
    parts = line.split()
    if len(parts) < 3:  # noqa: PLR2004 - path, switch, level
        raise ValueError(f"not a mix line: {line!r}")
    level_db = -math.inf if parts[2] == "-oo" else float(parts[2])
    return parts[1] == "ON", level_db


def parse_delay_line(line: str) -> tuple[bool, float]:
    """Parse an output delay line: ``/outputs/main/06/delay OFF   0.3`` gives ``(False, 0.3)``.

    Raises:
        ValueError: The line has no switch and time.
    """
    parts = line.split()
    if len(parts) < 3:  # noqa: PLR2004 - path, switch, time
        raise ValueError(f"not a delay line: {line!r}")
    return parts[1] == "ON", float(parts[2])


def _read_matrix(reader: NodeReader, number: int, outputs: tuple[int, ...]) -> MatrixState:
    prefix = f"mtx/{number:02d}"
    config_line = reader.node(f"{prefix}/config") or ""
    name = config_line.split('"')[1].strip() if '"' in config_line else ""
    eq_line = reader.node(f"{prefix}/eq")
    eq_on = eq_line.split()[1] == "ON" if eq_line and len(eq_line.split()) > 1 else True
    bands = tuple(
        parse_band_line(_require_node(reader, f"{prefix}/eq/{band}"))[1]
        for band in range(1, BANDS_PER_EQ + 1)
    )
    on, fader_db = parse_mix_line(_require_node(reader, f"{prefix}/mix"))
    preamp_line = reader.node(f"{prefix}/preamp") or ""
    invert = preamp_line.split()[1:2] == ["ON"]
    return MatrixState(number, name, SystemEq(eq_on, bands), on, fader_db, invert, outputs)


def read_console_state(reader: NodeReader) -> ConsoleState:
    """Read the speaker paths: Main LR EQ, the matrices and the delays of the main outputs.

    A matrix lists the main outputs whose source it is.

    Raises:
        ConsoleUnreachable: A node that must exist (band, mix, Main LR EQ) was not answered.
    """
    outputs_of: dict[int, list[int]] = {n: [] for n in range(1, MATRIX_COUNT + 1)}
    delays: dict[int, OutputDelay] = {}
    for output in range(1, MAIN_OUTPUT_COUNT + 1):
        source_line = reader.node(f"outputs/main/{output:02d}")
        if source_line:
            matrix = int(source_line.split()[1]) - OUTPUT_SOURCE_FIRST_MATRIX + 1
            if matrix in outputs_of:
                outputs_of[matrix].append(output)
        delay_line = reader.node(f"outputs/main/{output:02d}/delay")
        if delay_line:
            on, delay_ms = parse_delay_line(delay_line)
            delays[output] = OutputDelay(output, on, delay_ms)
    matrices = {
        number: _read_matrix(reader, number, tuple(outputs_of[number]))
        for number in range(1, MATRIX_COUNT + 1)
    }
    return ConsoleState(read_main_eq(reader), matrices, delays)
