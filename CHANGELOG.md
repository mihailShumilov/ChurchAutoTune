# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Project skeleton: `pyproject.toml` (uv, ruff, mypy strict, pytest, coverage), package layout per SPEC §5,
  GPL-3.0-or-later license.
- Port of the working hall tool into the layered package: X32 codec, value laws, read-only client and the
  single UDP transport; write policy, sealed write authorization and the scoped writer with readback; sweep,
  deconvolution and quality gates; response, RT60 and band analysis; system-EQ, crossover and alignment
  design; versioned file schemas with migrations from the original tool's files; plots and the Markdown report.
- Dev-safe guard: with `CTUNE_DEV_SAFE=1` only loopback consoles and the fake audio backend are accepted.
- Transitional expert CLI (`ctune check | backup | level | measure | analyze | propose | apply | undo | verify |
  export-for-chat | sub`), Russian texts, exit codes per SPEC §12 (ADR 0003).
- Golden tests on the anonymized hall session of 2026-10-02 (`tests/fixtures/golden/`, about 33 MB).
- Layer-rule test, coverage gates per package, pre-commit and CI configuration.

### Changed (compared with the original tool)
- Output delay and matrix polarity are written only with `--write-unverified`; by default `sub align` prints
  them as advice and `undo` refuses a file that holds them (ADR 0005).
- An output delay is written time first, switch second.
- `EQ on` is written last and only when every band passed the limits.
- An undo file is never overwritten; `apply` and `undo` are refused while temporary mutes are pending.
- A role override (`--sub`, `--mains`, `--others`) must name a matrix that feeds a physical output.
- Stored write values must be real finite numbers; strings and booleans are refused.

### Fixed
- `sub align --level-match` no longer writes the delay and polarity it found when the predicted gain is
  below 1 dB and it reports "delay not changed".
