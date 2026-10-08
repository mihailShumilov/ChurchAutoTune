"""Locations of test fixtures."""

from pathlib import Path

FIXTURES = Path(__file__).resolve().parent
X32_DUMPS = FIXTURES / "x32" / "dumps"
# Reference hall before any change; Main LR EQ still as set by ear.
DUMP_2026_09_29 = X32_DUMPS / "x32_dump_2026-09-29.txt"
# Same hall on the evening of the measurement session, before the first write.
DUMP_2026_10_02 = X32_DUMPS / "x32_dump_2026-10-02.txt"
