"""Remove personal and site-specific data from console dumps and undo files used as fixtures.

What is replaced:
  * names of people on channel and bus scribble strips, by OSC path (``RENAMES``);
  * the console name in the ``/-prefs`` line;
  * the console id (``X32P-xx-xx-xx``, derived from its MAC address);
  * private IPv4 addresses, by the documentation address ``192.0.2.80`` (RFC 5737).

What is kept: every numeric value, matrix and main names (role detection relies on them),
instrument and bus-purpose names, line order and line count.

Usage: ``uv run python scripts/anonymize_fixtures.py SOURCE DEST``
"""

from __future__ import annotations

import ipaddress
import re
import sys
from pathlib import Path

DOC_IP = "192.0.2.80"
CONSOLE_ID = "X32P-00-00-00"
CONSOLE_NAME = "ChurchTune"

# Scribble-strip names that were people's names in the reference hall, keyed by path so that the
# original names never appear in this repository.
RENAMES: dict[str, str] = {
    "/ch/01/config": "Vocal 1",
    "/ch/02/config": "Vocal 2",
    "/ch/03/config": "Vocal 3",
    "/ch/04/config": "Vocal 4",
    "/ch/13/config": "Vocal radio",
    "/bus/03/config": "Mon voc 1",
    "/bus/04/config": "Mon voc 2",
    "/bus/06/config": "Mon gtr",
}

_IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
_CONSOLE_ID = re.compile(r"\bX32[A-Z]*-[0-9A-Fa-f]{2}-[0-9A-Fa-f]{2}-[0-9A-Fa-f]{2}\b")
_FIRST_QUOTED = re.compile(r'"[^"]*"')


def _replace_ip(match: re.Match[str]) -> str:
    try:
        address = ipaddress.ip_address(match.group(1))
    except ValueError:
        return match.group(0)
    return DOC_IP if address.is_private and not address.is_loopback else match.group(0)


def _anonymize_line(line: str) -> str:
    path = line.split(" ", 1)[0]
    if path in RENAMES:
        line = _FIRST_QUOTED.sub(f'"{RENAMES[path]}"', line, count=1)
    elif path == "/-prefs":
        line = _FIRST_QUOTED.sub(f'"{CONSOLE_NAME}"', line, count=1)
    line = _CONSOLE_ID.sub(CONSOLE_ID, line)
    return _IPV4.sub(_replace_ip, line)


def anonymize_text(text: str) -> str:
    """Return ``text`` with personal and site-specific data replaced, line by line."""
    return "".join(_anonymize_line(line) for line in text.splitlines(keepends=True))


def main(argv: list[str]) -> int:
    """Anonymize the file ``argv[1]`` into ``argv[2]``."""
    expected_args = 3
    if len(argv) != expected_args:
        sys.stderr.write("usage: anonymize_fixtures.py SOURCE DEST\n")
        return 2
    source, dest = Path(argv[1]), Path(argv[2])
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(anonymize_text(source.read_text(encoding="utf-8")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
