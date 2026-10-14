# ChurchTune

[![CI](https://github.com/mihailShumilov/ChurchAutoTune/actions/workflows/ci.yml/badge.svg)](https://github.com/mihailShumilov/ChurchAutoTune/actions/workflows/ci.yml)
[![License: GPL v3+](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Status: pre-alpha](https://img.shields.io/badge/status-pre--alpha-orange.svg)](#project-status)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg)](https://github.com/astral-sh/ruff)

A free, open-source terminal app for macOS that guides church sound volunteers through measuring the room
and tuning a **Behringer X32 / Midas M32** system: sub/top crossover, sub alignment, system EQ,
verification and a report.

Most small churches run sound with volunteers who have no training, and a professional system tune costs
$1,000–3,000. ChurchTune aims to do that work in one rehearsal: it reads what it can from the console, tells
you where to put the microphone, measures, proposes changes, writes them safely with an undo file,
measures again and tells you whether it got better.

> [!WARNING]
> **Pre-alpha. Do not use it on a live system yet.** The measurement and design core works and reproduces a
> real tuning session, but the guided wizard does not exist yet and some console writes were never verified on
> hardware. See [Project status](#project-status).

## Contents

- [How it works](#how-it-works)
- [Project status](#project-status)
- [Safety](#safety)
- [Requirements](#requirements)
- [Getting started (developers)](#getting-started-developers)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

## How it works

1. **Read the console.** Model, firmware, routing, which matrix or bus feeds the tops and the sub, current EQ.
2. **Measure.** A measurement microphone (for example a Behringer ECM8000) on a console input, logarithmic
   sweeps played through the X32 USB card, several positions in the room, quality gates on every sweep.
3. **Analyse.** 1/6-octave response averaged over positions, spread between positions, RT60, the sub/top
   junction.
4. **Propose.** A Linkwitz-Riley crossover, sub level, delay and polarity, and a few broad system-EQ bands within
   strict limits. It also says what EQ cannot fix (room acoustics).
5. **Apply.** A diff of every change, a typed confirmation, an undo file written before the first change, and
   each value read back from the console after it is written.
6. **Verify and report.** Measure again, compare, offer to undo, write a Markdown report.

The method comes from a working tool that tuned one real hall on 2026-10-02. That session is part of the
golden tests.

## Project status

ChurchTune is built milestone by milestone (see [SPEC §15](docs/SPEC.md)).

| Milestone | Scope | State |
|---|---|---|
| M0 | Repository, port of the proven tool into a layered package, golden tests | done |
| M1 | X32/M32 driver: discovery, full read-only snapshot, topology inference | next |
| M2 | Audio and measurement engine: multi-mic, mic-set calibration, path checks | planned |
| M3 | Guided wizard (Textual TUI), storage, English / Russian / Ukrainian | planned |
| M4 | Analysis and proposals | planned |
| M5 | Apply, undo, verify | planned |
| M6 | Report | planned |
| M7 | Pilot in real churches and release 0.1.0 | planned |

What exists today is a transitional expert command line (`ctune --help`, Russian texts for now).
It is for developers, not for volunteers.

## Safety

A sound system can hurt ears and speakers, and a church needs its console to work on Sunday. The design follows
these rules:

- **Read-only by default.** The console client can only read from a fixed allowlist of addresses. One module
  writes, and only with a scoped authorization from the safety policy.
- **Limits.** EQ cut and boost, Q, crossover and level changes are bounded. Anything outside the limits is
  refused, not clipped.
- **Undo first.** An undo file is on disk before the first write. It is never overwritten.
- **Readback.** Every write is read back from the console and compared.
- **Quiet audio.** Nothing louder than −6 dBFS peak leaves the computer. Fades on every start and stop.
  Ctrl+C stops the sound immediately.
- **Unverified writes are opt-in.** Output delay and matrix polarity have not been checked on a real console
  yet. They are written only with `--write-unverified`
  ([ADR 0005](docs/adr/0005-unverified-writes-opt-in.md)).
- **Development never touches hardware.** With `CTUNE_DEV_SAFE=1` the code refuses any console that is not on
  `127.0.0.1` and any audio device that is not the fake backend. All tests run that way.

Hardware behaviour is confirmed only by a person at a console, recorded in
[`docs/hardware-checklist.md`](docs/hardware-checklist.md).

## Requirements

- macOS (the target platform). Linux runs the test suite in CI.
- Python 3.12 and [uv](https://docs.astral.sh/uv/).
- PortAudio and libsndfile (bundled with the Python wheels on macOS; `libportaudio2 libsndfile1` on Debian/Ubuntu).
- For real use, later: a Behringer X32 / Midas M32 with the X-USB card and a measurement microphone.

## Getting started (developers)

```bash
git clone https://github.com/mihailShumilov/ChurchAutoTune.git
cd ChurchAutoTune
uv sync
CTUNE_DEV_SAFE=1 uv run pytest -q          # all tests: X32 emulator + fake audio
CTUNE_DEV_SAFE=1 uv run ctune --help       # expert CLI
```

The full check that CI runs:

```bash
CTUNE_DEV_SAFE=1 uv run ruff format --check . && uv run ruff check . && uv run mypy \
  && CTUNE_DEV_SAFE=1 uv run pytest --cov --cov-report=json && uv run python scripts/check_coverage.py
```

## Documentation

- [Specification](docs/SPEC.md) (Russian): scope, wizard steps, algorithms, safety model, milestones.
- [Coding standards](docs/CODING_STANDARDS.md)
- [Architecture decisions](docs/adr/)
- [Hardware checklist](docs/hardware-checklist.md): what has and has not been confirmed on a real console.
- [Changelog](CHANGELOG.md)

## Contributing

Contributions are welcome: code, translations, measurements from your hall, and especially hardware check
results from an X32 or M32. Read [CONTRIBUTING.md](CONTRIBUTING.md) first. This project follows the
[Contributor Covenant](CODE_OF_CONDUCT.md). To report a security or safety problem privately, see
[SECURITY.md](SECURITY.md). For questions, see [SUPPORT.md](SUPPORT.md).

## License

[GPL-3.0-or-later](LICENSE). Behringer, X32, Midas and M32 are trademarks of their owners. This project is not
affiliated with or endorsed by them.
