# 0003 — Transitional expert CLI with literal Russian texts

Status: accepted (2026-10-08), to be superseded in M3

## Context

The original tool is driven through one command-line module, and its safety tests run whole scenarios through
those commands (apply and undo round trips, read-only sweeps, temporary mutes with failures). SPEC puts the
wizard, the Textual UI and the Fluent catalogues in M3. CODING_STANDARDS §11 forbids user-facing literals in
code.

## Decision

- M0 ports the commands (`check`, `backup`, `level`, `measure`, `analyze`, `propose`, `apply`, `undo`,
  `verify`, `export-for-chat`, `sub`) to Typer in `src/churchtune/cli/`, one module per group, with the
  original behaviour and the original Russian operator texts as literals.
- Everything below the CLI returns codes with parameters (`ReasonCode`, `SkipCode`, `DesignWarning`,
  `NoteCode`, quality `WarningCode`); the sentences live in `report/texts.py` and in the command modules only.
- Lint exceptions are limited to `src/churchtune/cli/*.py` (complexity rules) and `report/texts.py`.
- Data stays in the flat home-directory layout of the original tool (`measurements/`, `results/`, `undo/`,
  `backups/`); the venue / session layout of SPEC §6.4 arrives with the wizard.
- Exit codes follow SPEC §12, not the original numbers.
- Dropped from the port: the `diag` command (it loaded a script with a second socket) and `selftest`
  (SPEC defines a different one).

## Consequences

- In M3 the texts move to `.ftl` files and `report/texts.py` becomes key lookups; the command modules are
  rebuilt on the wizard engine.
- Until then the CLI is Russian-only.
