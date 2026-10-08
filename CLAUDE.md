# CLAUDE.md — ChurchTune

Free, open-source terminal app (macOS) that guides church sound volunteers through measuring the room
and tuning a Behringer X32 / Midas M32 system: crossover, sub alignment, system EQ, verify, report.
Ported and generalized from the owner's working tool `~/sites/my/X32-church` (read it, don't modify it).

- Spec: `docs/SPEC.md` (Russian) — the source of truth for scope, steps, algorithms, safety, milestones.
- Coding standards: `docs/CODING_STANDARDS.md`.
- Talk to the user in Russian. Code, identifiers, comments, commits, docs for contributors — English.
  UI strings only via `src/churchtune/i18n/locales/{en,ru,uk}/*.ftl`.

## HARD SAFETY RULES
1. **You never talk to a real console and never play sound on real hardware.** Develop and test only
   against the emulator on 127.0.0.1 and the fake audio backend. Prefix every command that runs the app
   or tests with `CTUNE_DEV_SAFE=1` (the code then refuses non-loopback consoles and real audio devices).
   A PreToolUse hook (`.claude/hooks/guard_console.py`) blocks violations — do not work around it.
2. The only socket is `src/churchtune/mixers/x32/transport.py`, used by `client.py` (read-only allowlist) and
   `writer.py` (the only writer). `writer.py` works only with a `WriteAuthorization` from `safety.authorize()`
   for one write scope (SPEC §9.2–9.3). `safety/` is pure policy and never imports `mixers`. Never add a path to an
   allowlist without a test and a `safety-reviewer` pass.
3. Never weaken, skip, or xfail tests under `tests/unit/safety/` or `tests/integration/test_safety_e2e.py`.
4. Never guess an OSC address or formula. Use the `x32-osc` skill and fixtures from real dumps.
   Unconfirmed → `# UNVERIFIED:` comment + entry in `docs/hardware-checklist.md` + ask the user.
5. Audio: nothing louder than −6 dBFS peak leaves the computer; fades everywhere; Ctrl+C → `stream.abort()`.

## Workflow
- Work milestone by milestone (SPEC §15). Start each milestone in plan mode: read the relevant SPEC
  sections and skills, propose a plan, get approval, then implement test-first.
- Small, atomic Conventional Commits. Update `CHANGELOG.md`, ADRs (`docs/adr/`), this file when rules
  or the code map change.
- Use Context7 MCP for library docs (Textual, Typer, sounddevice, pydantic, numpy, scipy, fluent.runtime).
- Before finishing a task, run the full check:
  ```bash
  CTUNE_DEV_SAFE=1 uv run ruff format --check . && uv run ruff check . && uv run mypy \
    && CTUNE_DEV_SAFE=1 uv run pytest --cov --cov-report=json && uv run python scripts/check_coverage.py
  ```

## Agents (use them — they are part of Definition of Done)
| agent | when |
|---|---|
| `safety-reviewer` | any change to client/writer/safety/audio levels/temp mutes/undo format, wizard steps S6–S13, any proposal or undo file |
| `dsp-reviewer` | any change to measure/, analysis/, design/ math, thresholds, golden data |
| `acoustics-analyst` | interpreting measurements, sanity of proposals vs. real-world practice |
| `wizard-ux-reviewer` | new/changed screens, prompts, i18n strings, error messages |
| `code-reviewer` | every PR, against CODING_STANDARDS and layer rules |

## Skills
- `x32-osc` — protocol, addresses, formulas, quantization, gotchas (from real dumps).
- `room-measurement` — method, mic placement, interpreting results (from the real session 2026-10-02).
- `safe-writes` — write scopes, limits, ordering, undo/restore, review checklist.
- `wizard-ux` — UX and copy rules for volunteers, i18n conventions, screen patterns.
- `mixer-driver` — the `MixerDriver` contract and how to add/extend a console family.

## Code map
State after M0. Modules marked (M1+) are in SPEC §5 but not written yet.
- `cli/` Typer expert CLI, transitional, Russian literals (ADR 0003): `main` (app, `run()`), `context`, `records`,
  `check`, `measure`, `analyze`, `apply` (apply/undo, the only place that sends a plan), `sub`, `hwcheck`
- `app/` Textual UI (M3) · `wizard/` UI-agnostic step engine (M3) · `i18n/` catalogues (M3)
- `domain/` pure models: `eq`, `eq_model` (band responses, ADR 0002), `changes`, `console`, `venue`, `levels`, `arrays`
- `mixers/x32/` `transport` (the only socket), `codec`, `client` (read-only allowlist), `params`, `nodes`, `dump`,
  `topology`, `laws` (value laws given to the policy), `writer` (the only writer) · `mixers/base.py`, `capabilities` (M1)
- `audio/` `base` (protocol, −6 dBFS limiter), `sounddevice_backend`, `factory`, `fake/` (room simulation)
- `measure/` `config`, `stimulus`, `deconvolution`, `gates`, `point`, `junction` · mic mapping/calibration (M2)
- `analysis/` `bands`, `response`, `rt60`, `points` · `design/` `target`, `system_eq`, `system_eq_opt`, `crossover`,
  `alignment`, `proposal`
- `safety/` `policy`, `plan`, `sub_plan`, `authorize`, `laws` (protocol; never imports `mixers`)
- `storage/` `models`, `migrations`, `paths`, `files` · `report/` `texts`, `markdown`, `plots` · `platform/` `devsafe`
- Layer rule enforced by `tests/unit/test_architecture.py` (SPEC §5, clarifications in ADR 0004).
- Writes never verified on hardware (output delay, polarity) need `--write-unverified` (ADR 0005).
- Tests: `tests/emulator/` (X32 emulator), `tests/fixtures/` (anonymized dumps, golden hall data), `tests/golden/`.

## Commands
```bash
uv sync                                         # install
CTUNE_DEV_SAFE=1 uv run pytest -q               # all tests (emulator + fake audio)
CTUNE_DEV_SAFE=1 uv run pytest tests/golden -q  # the hall session of 2026-10-02
CTUNE_DEV_SAFE=1 uv run pytest --cov --cov-report=json && uv run python scripts/check_coverage.py   # coverage gates
CTUNE_DEV_SAFE=1 uv run ctune --help            # expert CLI; --console HOST[:PORT] --audio fake --home DIR
# From M1 (the emulator moves into the package) and M3 (TUI snapshots):
CTUNE_DEV_SAFE=1 uv run ctune emulator --model X32P   # prints port
CTUNE_DEV_SAFE=1 uv run ctune --emulator                     # wizard against an in-process emulator + fake audio
CTUNE_DEV_SAFE=1 uv run pytest tests/tui --snapshot-update   # only after reviewing visual diffs
```

## Reference hardware (owner's hall; for fixtures and golden tests only)
X32 Producer fw 4.09; X-USB 32/32 48 kHz; ECM8000 on AES50-A 10 → USB in 10; playback USB 1/2 → ch23/24 → Main LR;
tops Mtx1/2 (out 8/7), sub Mtx3 "Sub buffer" (out 6), balcony Mtx5/6 (out 4/3, normally unused).
Applied 2026-10-02: crossover LR24 101.4 Hz, sub fader −29.3, no delay, Main EQ 479.8 −9 Q1.5 / 1k97 −1.75 Q6.4 / 50.8 −6 Q3.7.
