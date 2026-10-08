"""Parsing of ``/node`` reply lines into domain models.

``/node`` strings are human-readable and formatted, not raw: frequency ``232.3`` / ``1k97``, gain
``+0.00`` / ``-15.0``, Q ``3.9``. They are parsed with the display parsers from ``params``.
"""

import re
from collections.abc import Sequence

from churchtune.domain.eq import BANDS_PER_EQ, Band, EqType, SystemEq
from churchtune.mixers.x32.params import parse_freq

MAIN_EQ_NODE = "main/st/eq"

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
