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
- Keep the project website (`site/`, GitHub Pages, en/uk/es) in step with progress: whenever a milestone
  or a user-visible feature lands, update the status, roadmap and the detailed how-to instructions on all
  language pages in the same PR (or a follow-up PR right after). Honest status only, no invented claims.
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
State after M6 plus the M7 install and self-check (ADR 0010, 0011, 0014, 0015).
- `cli/` Typer expert CLI, transitional, Russian literals (ADR 0003): `main` (app, `run()`), `context`, `records`,
  `check`, `measure`, `analyze`, `apply` (apply/undo), `apply_packages` (`apply-packages` and the package
  undo, terminal side), `package_writes` (plan, check, re-plan and send package plans; `X32WritePort` for the
  wizard, incl. the read-only `describe_undo` for the report; with `apply` the only places that send a plan), `mute_writes` (temporary matrix mutes, pending
  restore), `demo` (`--emulator` hall link), `sub`, `hwcheck`, `report` (a session's report again, `--for-chat`),
  `console` (`doctor`, `snapshot`, `emulator`; global `--emulator`), `mics` (`devices`, `mics map|calibrate`,
  `noise`, `pathcheck`), `wizard` (plain `ctune`, `wizard`, `resume`; console/audio ports, line UI), `selftest` (whole wizard on the in-process emulator, ADR 0015), `scripted` (the scripted
  volunteer of selftest and e2e tests)
- `wizard/` UI-agnostic engine (ADR 0008): `protocol` (serializable `Prompt`/`Answer`, `Prompter`), `state`, `engine`,
  `services` (ports incl. `WritePort`, stop flag), `ui` (prompt helpers), `run` (start/resume), `labels` (console
  words as texts), `proposals` (plan/packages ↔ files), `applying` (proposal → ordered steps from a fresh read),
  `steps/` `setup`, `measure`, `junction` (S10b), `proposal` (S11 packages, audit), `apply` (S12), `verify` (S13),
  `finish` (S14, writes the report); `reporting` (a session's report data and files, ADR 0014)
- `app/` Textual UI: `app` (`WizardApp`, `TuiPrompter` queue bridge, sound keys), `widgets` (header, prompt, meter)
- `i18n/` `Translator` (Fluent), `locales/{en,ru,uk}/*.ftl` (`report.ftl`: the session report)
- `domain/` pure models: `eq`, `eq_model` (band responses, ADR 0002), `changes`, `console`, `venue`, `levels`, `arrays`,
  `snapshot` (console-neutral `ConsoleSnapshot`, USB map), `topology` (`TopologyGuess`, roles, variants, `with_roles`),
  `console_paths` (snapshot → `ConsoleState`/`Roles` for the design code),
  `recording` (`MultiRecording`, `StopFlag`, ADR 0007)
- `mixers/base.py` driver contract (`MixerDriver` incl. `eq_grid`, `headamp_of`; `Capabilities`/`Feature`/`Support`,
  meters, discovery result)
- `mixers/x32/` `transport` (the only socket; also `/xinfo`-only discovery), `codec`, `client` (read-only allowlist),
  `params`, `nodes`, `dump`, `snapshot` (node list, read, tolerant parse), `routing` (source ids, blocks, headamps),
  `names` (multilingual speaker names), `topology` (`infer_topology`; M0 `detect_roles`), `filters`,
  `speaker_paths`, `topology_issues`, `usb`, `models`
  (model strings, `VERIFIED_CONSOLES`), `driver` (`X32Driver`, `discover_consoles`), `laws`, `plan_writes` (ordered
  steps → scoped writes, wire ↔ state for undo), `writer` (the only writer)
- `churchtune_emulator/` (separate top-level package, loopback only, ADR 0006): `x32` emulator with model variants,
  latency and loss; seeded from the packaged reference dump
- `audio/` `base` (protocols incl. `MultiChannelBackend`, −6 dBFS limiter), `sounddevice_backend`, `factory`,
  `devices` (card ranking), `fake/` (`backend` rooms, `hall` multi-mic simulator and dev rig, `defects` M4 hall)
- `measure/` `config`, `stimulus`, `deconvolution`, `gates`, `point`, `junction` (also multi-mic with injected mute
  switch), `multi` (rounds, repeatability), `calibration`, `mic_mapping`, `level` (ramp, sweep level), `noise`,
  `path_check`, `rounds`, `plan` (point plan, Halton, hall map)
- `analysis/` `bands`, `response`, `rt60`, `points`, `verdict` (§8.6) · `design/` `target`, `system_eq`, `system_eq_opt`, `blind_junction`, `crossover`,
  `alignment`, `proposal`, `packages` (S11 packages, ADR 0009), `channel_audit`
- `safety/` `policy` (scopes of §9.2), `plan`, `sub_plan`, `order` (never-louder order by simulation), `cut_filters`
  (where a crossover or undo may touch a cut filter), `authorize`
  (typed word, undo hash), `laws` (protocol; never imports `mixers`)
- `storage/` `models`, `migrations`, `paths`, `files` · `report/` (M6, ADR 0014) `model`, `sections`, `findings`,
  `figures`, `html`, `session_md`, `session_json`, `chat`; transitional `texts`, `markdown`, `plots` · `platform/` `devsafe`
- Layer rule enforced by `tests/unit/test_architecture.py` (SPEC §5, clarifications in ADR 0004).
- Writes never verified on hardware (output delay, polarity) need `--write-unverified` (ADR 0005); in the wizard and
  `apply-packages` they are advice only (ADR 0010).
- `scripts/install.sh` one-command install (uv tool install from the source archive, then selftest).
- Tests: `tests/emulator/` (subclass of the packaged emulator), `tests/fixtures/` (anonymized dumps, golden hall data,
  `x32/topologies/*.toml` = dump + overrides + expected roles), `tests/golden/`, `tests/e2e/` (scripted wizard),
  `tests/tui/` (Pilot tests, SVG snapshots in `__snapshots__/`).
- `site/` static project website (GitHub Pages, en/uk/es). When a milestone or user-visible feature lands, update
  its roadmap and guide pages in all three languages (`site/README.md`).

## Commands
```bash
uv sync                                         # install
CTUNE_DEV_SAFE=1 uv run pytest -q               # all tests (emulator + fake audio)
CTUNE_DEV_SAFE=1 uv run pytest tests/golden -q  # the hall session of 2026-10-02
CTUNE_DEV_SAFE=1 uv run pytest --cov --cov-report=json && uv run python scripts/check_coverage.py   # coverage gates
CTUNE_DEV_SAFE=1 uv run ctune --help            # expert CLI; --console HOST[:PORT] --audio fake --home DIR
CTUNE_DEV_SAFE=1 uv run ctune report --for-chat # the newest session's report again, or its chat text
# Emulator and doctor (M1); wizard and TUI (M3):
CTUNE_DEV_SAFE=1 uv run ctune emulator --model X32P   # prints port
CTUNE_DEV_SAFE=1 uv run ctune --emulator doctor              # doctor against an in-process emulator
CTUNE_DEV_SAFE=1 uv run ctune selftest                       # the whole wizard on the in-process emulator
CTUNE_DEV_SAFE=1 uv run ctune --emulator --audio fake        # the wizard (TUI) on the emulator + simulated hall
CTUNE_DEV_SAFE=1 uv run ctune --emulator --audio fake wizard --plain --lang ru   # line interface
CTUNE_DEV_SAFE=1 uv run ctune --emulator --audio fake resume # continue the newest session
CTUNE_DEV_SAFE=1 uv run pytest tests/tui --snapshot-update   # only after reviewing visual diffs
```

## Reference hardware (owner's hall; for fixtures and golden tests only)
X32 Producer fw 4.09; X-USB 32/32 48 kHz; ECM8000 on AES50-A 10 → USB in 10; playback USB 1/2 → ch23/24 → Main LR;
tops Mtx1/2 (out 8/7), sub Mtx3 "Sub buffer" (out 6), balcony Mtx5/6 (out 4/3, normally unused).
Applied 2026-10-02: crossover LR24 101.4 Hz, sub fader −29.3, no delay, Main EQ 479.8 −9 Q1.5 / 1k97 −1.75 Q6.4 / 50.8 −6 Q3.7.
