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
- `ctune hwcheck delay | polarity`: one-parameter hardware check of the two writes that were never verified
  on a console (hardware checklist rows 1 and 2, ADR 0005).
- Golden tests on the anonymized hall session of 2026-10-02 (`tests/fixtures/golden/`, about 33 MB).
- Layer-rule test, coverage gates per package, pre-commit and CI configuration.
- Open-source project files: contributing guide, Contributor Covenant code of conduct, security and safety
  policy with private reporting, support page, issue forms (bug, feature, hardware check result), pull request
  template with a safety checklist, CODEOWNERS, Dependabot, CodeQL, `.editorconfig`, `.gitattributes`.

- Console driver (M1): discovery of X32/M32 consoles (`/xinfo` only; loopback only in dev-safe
  mode), a full read-only snapshot parsed into console-neutral models (inputs, buses, matrices,
  mains, outputs, headamps, USB routing, clock), speaker topology inference with confidence and
  reasons (multilingual names in en/ru/uk/es/pt, cut filters and crutch shelves, stereo links, what
  feeds each matrix), sub pairs, speakers in other rooms (`zone`), the issues the wizard must explain,
  the USB audio map, model capabilities (`full` only for a verified model and firmware pair, today
  X32 Producer 4.09), meters as a stream and link quality.
- `ctune doctor` (computer, console, link, snapshot, topology, USB card, capabilities; also
  `--from-file`), `ctune snapshot` (saved to `console/<ts>-snapshot.json`), `ctune emulator` and the
  global `--emulator` option.
- The X32 emulator moved into the package `churchtune_emulator` with model variants, reply latency
  and packet loss (ADR 0006); fourteen topology fixtures in `tests/fixtures/x32/topologies/`.
- A `/node` reply that arrives late for an earlier path is skipped instead of costing the current
  path an attempt, so one slow answer no longer shifts the replies of all later nodes; a snapshot
  read stops with "console unreachable" after 20 silent nodes in a row.

- Measurement engine (M2): several microphones in one stream (`MultiChannelBackend`, real and
  simulated), a stop flag that silences within 50 ms, mic-set calibration (sensitivity, delay,
  polarity, shape; rejection rules of SPEC S9; stored for 30 days), auto-mapping by a scratch, the
  pink-noise level ramp with every stop condition of SPEC S8, the sweep-level search, background
  noise per octave with hum and rumble hints, the path check (latency, heard or not), the
  speaker-by-speaker check (heard, polarity of the tops, side), multi-mic rounds with sweeps aligned
  before averaging, and the sub/top junction with restored mutes (ADR 0007).
- `ctune devices`, `ctune mics map`, `ctune mics calibrate`, `ctune noise`, `ctune pathcheck`; a
  four-microphone simulated rig behind `--audio fake`.

- Guided wizard (M3): plain `ctune` (or `ctune wizard`) walks a volunteer through 13 steps — venue
  and goal, Mac and audio card, console discovery and read, speaker roles, equipment, microphone
  mapping, playback path, level, mic calibration, measurement rounds, results in plain words and
  what to do next. The state is saved after every step and `ctune resume` continues; Back never
  crosses a step that changed the console (ADR 0008).
- Full-screen Textual interface with a step header, tables, live level meters with a colour-free
  target mark and translated key hints; Ctrl+C and Space stop the sound on every screen, Esc goes
  back, Ctrl+Q saves and quits. `ctune wizard --plain` (and any non-terminal) gives a line
  interface for pipes and screen readers.
- Before the first sound the wizard reads the console again and plays nothing while a channel of
  a measurement microphone can reach the speakers; every sound has a warning screen, the sweep
  rounds count down three seconds, and a failed check repeats the step instead of going back.
- Interface texts in English, Russian and Ukrainian (Fluent), with plural forms and a decimal comma
  in ru/uk; a test keeps the catalogues complete and in sync with the code.

### Changed (compared with the original tool)
- A stream dropout (xrun) makes a point BAD (SPEC §8.2); two sweeps must agree within 1 dB.
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
