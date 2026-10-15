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
- Project website in English, Ukrainian and Spanish (`site/`: landing page, roadmap, guide), published to
  GitHub Pages by `.github/workflows/pages.yml`.

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
- Proposals (M4): after the measurement the wizard proposes packages of changes — the system EQ
  now; crossover, sub alignment and sub level once the wizard measures sub and tops separately
  (M5), because a crossover set blind can open a hole in the bass — each with a before/after
  table, the reason, the expected effect and the risk. The person adds each one to the plan or leaves it out (the later packages are then
  designed again without it) and the plan is saved in `proposal.json`. Nothing is written to the
  console yet (ADR 0009).
- A measurement point plan for the hall (at least eight seats in the middle of the room, off the
  centre line, irregular spacing, four junction points), named by row and side, drawn as a map and
  kept for the venue.
- Channel advice: voices without a low cut or with a very high one, narrow cuts that probably
  protect against feedback, cuts shared by many channels, deep cuts, big boosts, phantom power on
  dynamic microphones.
- A simulated hall with known defects and an acceptance test: the proposals fix the junction dip,
  the sub level, the 500 Hz hump and the 50 Hz mode, and leave a narrow positional null alone.
- Applying (M5): the wizard's step "Apply the changes" writes the chosen packages after a fresh
  read of the console, shows every setting with its present and new value and asks for a typed
  word (`apply` / `применить` / `застосувати`). The old values are saved before the first write,
  the writes come in an order that never makes the sound louder in between, every write is read
  back, and a write that does not read back stops the plan and offers to put back what was written.
  A console changed since the measurement refuses the plan (measure again). Output delay and
  polarity are shown as values to set by hand (ADR 0010).
- `ctune apply-packages proposal.json`: the same from the terminal; `ctune undo` puts back the
  values of such a plan where they were not changed by hand since, and lists the ones it leaves.
- Check measurement (M5): the wizard measures at least four seats again (the worst one and the one
  in the middle among them) and says "better", "no noticeable difference" or "worse" by the
  criteria of SPEC §8.6, with a small before/after picture; when it got worse it offers the way
  back right away. The last screen reminds to save the scene under a new name.
- Five write scopes (temporary matrix mutes, system EQ, sub level, output delay, polarity) with
  their limits, an authorization bound to the typed word and to the undo file's content, and a
  stop between writes. The simulated hall now hears the console's Main LR EQ.
- Subwoofer and main speakers measured apart (M5): at the junction points the wizard plays three
  sweeps in a row — subwoofer alone, main speakers alone, everything — muting only their matrices
  for a few seconds after a typed word (`mute` / `заглушить` / `заглушити`). The switches are saved
  before the first mute and put back right after, also on a stop; mutes left by an interrupted
  run are put back first, after a fresh read and the word. With this measurement the wizard can
  propose and write the crossover (ADR 0011).
- The write order finds more safe ways: a change waits while others move, a cut is held by a
  spare band while it moves, and a speaker matrix may be muted briefly for a change that has no
  other safe order (never Main LR).
- A sub level more than 12 dB away from the venue's first reading asks a separate question;
  `ctune apply-packages --yes-i-am-at-the-console` confirms without the typed word.
- `ctune --emulator` goes all the way: the demo mutes its microphone channels and the simulated
  hall hears the emulator's EQ and matrix switches.

### Changed (compared with the original tool)
- A system EQ that fixes a hump or a mode is proposed when the largest excess above the target drops by 1 dB
  or more (and the overall deviation does not get worse), even if the RMS deviation moves less than 0.2 dB;
  its screen then shows the largest excess before and after.
- The speakers' low roll-off is the start of the pass band; a sub/top cancellation or a room dip
  above it no longer stops the system EQ from correcting lower frequencies.
- The system-EQ optimiser judges whether a feature is common to the seats where its band acts,
  so a narrow resonance shared by every seat is cut.
- A stream dropout (xrun) makes a point BAD (SPEC §8.2); two sweeps must agree within 1 dB.
- Output delay and matrix polarity are written only with `--write-unverified`; by default `sub align` prints
  them as advice and `undo` refuses a file that holds them (ADR 0005).
- An output delay is written time first, switch second.
- `EQ on` is written last and only when every band passed the limits.
- An undo file is never overwritten; `apply` and `undo` are refused while temporary mutes are pending.
- A role override (`--sub`, `--mains`, `--others`) must name a matrix that feeds a physical output.
- Stored write values must be real finite numbers; strings and booleans are refused.

### Fixed
- System EQ design no longer depends on floating-point noise: the optimiser puts idle bands (and a second
  cut on a room mode) to work on what is left and solves again, keeping a lower cost, so near-equal local
  minima give the same proposal on every platform. A modelled gain of exactly 0.2 dB is no longer lost to
  rounding.
- System EQ no longer spreads one cut over several near-identical bands: the optimiser is penalised for two
  working cuts closer than half the wider one's width, so the table stays short and two bands never add up
  to more than the −9 dB one band may cut. Every candidate proposal gets one number (weighted deviation, a small
  price per dB of gain, 0.05 dB for each second cut within half an octave of another) and the lowest wins, so a
  second cut on one problem stays only when it buys more than that; a band that stays flat is not moved.
- `sub align --level-match` no longer writes the delay and polarity it found when the predicted gain is
  below 1 dB and it reports "delay not changed".
