#!/usr/bin/env python3
"""PostToolUse hook: format and lint-fix a Python file right after Claude edits it.

The edit is always kept. If ruff leaves unfixable problems, they are printed on stderr with exit code 2,
which Claude Code shows to Claude after a PostToolUse hook. F401 (unused import) is not auto-fixed, because
an import is often added one edit before its first use.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    path = data.get("tool_input", {}).get("file_path") or ""
    if not path.endswith(".py") or not Path(path).exists():
        return 0
    project = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    if not (Path(project) / "pyproject.toml").exists():
        return 0  # before M0 sets up the project there is no ruff config to run
    problems: list[str] = []
    for args in (["ruff", "format", path], ["ruff", "check", "--fix", "--unfixable", "F401", "--quiet", path]):
        try:
            res = subprocess.run(["uv", "run", "--quiet", *args], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"format_python: could not run {' '.join(args)}: {exc}", file=sys.stderr)
            return 0
        if res.returncode != 0:
            problems.append((res.stdout or res.stderr).strip()[-2000:])
    if problems:
        print("ruff found problems it could not fix:\n" + "\n".join(problems), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
