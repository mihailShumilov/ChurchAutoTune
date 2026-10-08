# 0001 — M0 stack and repository layout

Status: accepted (2026-10-08)

## Context

Milestone M0 (SPEC §15) turns the documentation-only repository into a Python project and ports the proven
single-hall tool into a layered, strictly typed package. SPEC §4 lists the full MVP stack, but most of it is
not needed until later milestones.

## Decision

- Python 3.12, managed by uv; `src/` layout; hatchling build backend; entry point `ctune = churchtune.cli.main:run`.
- Every package from SPEC §5 exists from the start (some are empty until their milestone) so the layer test
  covers the full map from day one.
- Runtime dependencies in M0 are limited to what the ported code uses: numpy, scipy, sounddevice, soundfile,
  matplotlib, typer, rich, pydantic.
- Deferred until the milestone that needs them: textual and pytest-textual-snapshot (M3), fluent.runtime (M3),
  tomli-w (M3), jinja2 (M6), pytest-asyncio (M3).
- Quality gates: ruff (rule sets from CODING_STANDARDS §1), `mypy --strict` on `src/`, pytest, coverage with an
  overall gate in `pyproject.toml` and per-package gates (SPEC §14.3) in `scripts/check_coverage.py`.
- License: GPL-3.0-or-later (owner's decision).
- The working name stays ChurchTune / `ctune` / `churchtune`; availability is re-checked before the first
  public release.

## Consequences

- Adding a deferred dependency later needs no new ADR beyond a note in the milestone's PR, since SPEC §4 already
  justifies it.
- Empty packages carry only a docstring; the architecture test must not treat an empty package as a violation.
