"""Locations of test fixtures."""

from pathlib import Path

FIXTURES = Path(__file__).resolve().parent
X32_DUMPS = FIXTURES / "x32" / "dumps"
# Reference hall before any change; Main LR EQ still as set by ear.
DUMP_2026_09_29 = X32_DUMPS / "x32_dump_2026-09-29.txt"
# Same hall on the evening of the measurement session, before the first write.
DUMP_2026_10_02 = X32_DUMPS / "x32_dump_2026-10-02.txt"
# Real measurement data of the reference hall, session of 2026-10-02 (see fixtures/README.md).
GOLDEN_HALL = FIXTURES / "golden" / "hall-2026-10-02"


class DumpReader:
    """Answers ``node(path)`` from a console dump file, like a console frozen at that moment."""

    def __init__(self, dump: Path) -> None:
        self.lines = {
            line.split(" ", 1)[0].lstrip("/"): line
            for line in dump.read_text(encoding="utf-8").splitlines()
            if line.startswith("/")
        }

    def node(self, path: str, timeout_s: float = 0.0) -> str | None:
        return self.lines.get(path.lstrip("/"))
