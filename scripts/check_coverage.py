"""Enforce the per-package coverage gates from SPEC §14.3.

Reads ``coverage.json`` (``pytest --cov --cov-report=json``) and fails when a gated group of files
is below its threshold. Lines and branches are counted together, as coverage.py does for its total.
The overall 80 % gate lives in ``pyproject.toml`` (``tool.coverage.report.fail_under``).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Gate:
    """A group of source files that must reach ``min_percent`` coverage."""

    name: str
    pattern: str
    min_percent: float


GATES: tuple[Gate, ...] = (
    Gate("safety", "src/churchtune/safety/*", 100.0),
    Gate("writer", "src/churchtune/mixers/*/writer.py", 100.0),
    Gate("client", "src/churchtune/mixers/*/client.py", 100.0),
    Gate("transport", "src/churchtune/mixers/*/transport.py", 100.0),
    Gate("mixers", "src/churchtune/mixers/*", 90.0),
    Gate("measure", "src/churchtune/measure/*", 90.0),
    Gate("analysis", "src/churchtune/analysis/*", 90.0),
    Gate("design", "src/churchtune/design/*", 90.0),
    # "Everything else" of SPEC §14.3, per package, so a well-covered package cannot hide another.
    Gate("cli", "src/churchtune/cli/*", 80.0),
    Gate("audio", "src/churchtune/audio/*", 80.0),
    Gate("storage", "src/churchtune/storage/*", 80.0),
    Gate("report", "src/churchtune/report/*", 80.0),
    Gate("domain", "src/churchtune/domain/*", 80.0),
    Gate("platform", "src/churchtune/platform/*", 80.0),
)


def evaluate(files: dict[str, Any], gates: tuple[Gate, ...]) -> list[str]:
    """Return one message per gate that is below its threshold.

    Args:
        files: The ``files`` mapping of a coverage.py JSON report (path -> data with ``summary``).
        gates: Gates to check. A gate that matches no measured code fails: a renamed or moved
            file must not drop its gate silently.

    Returns:
        Human-readable failure messages; empty when every gate is met.
    """
    failures: list[str] = []
    for gate in gates:
        covered = 0
        total = 0
        for path, data in files.items():
            if not fnmatch(Path(path).as_posix(), gate.pattern):
                continue
            summary = data["summary"]
            covered += summary["covered_lines"] + summary.get("covered_branches", 0)
            total += summary["num_statements"] + summary.get("num_branches", 0)
        if total == 0:
            failures.append(f"{gate.name}: no measured code matches {gate.pattern}")
            continue
        percent = 100.0 * covered / total
        if percent < gate.min_percent:
            failures.append(
                f"{gate.name}: {percent:.1f}% < {gate.min_percent:.1f}% "
                f"({total - covered} of {total} lines+branches missed)"
            )
    return failures


def main(argv: list[str]) -> int:
    """Check ``coverage.json`` (or the path given as the first argument) against ``GATES``."""
    report_path = Path(argv[1]) if len(argv) > 1 else Path("coverage.json")
    if not report_path.exists():
        sys.stderr.write(
            f"{report_path} not found; run pytest with --cov --cov-report=json first\n"
        )
        return 2
    files = json.loads(report_path.read_text(encoding="utf-8"))["files"]
    failures = evaluate(files, GATES)
    for failure in failures:
        sys.stderr.write(f"coverage gate failed: {failure}\n")
    if not failures:
        sys.stdout.write("coverage gates passed\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
