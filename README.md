# ChurchTune

**English** · [Русский](README.ru.md) · [Українська](README.uk.md)

[![CI](https://github.com/mihailShumilov/ChurchAutoTune/actions/workflows/ci.yml/badge.svg)](https://github.com/mihailShumilov/ChurchAutoTune/actions/workflows/ci.yml)
[![License: GPL v3+](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Status: pre-alpha](https://img.shields.io/badge/status-pre--alpha-orange.svg)](#project-status)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg)](https://github.com/astral-sh/ruff)

**Website:** <https://mihailshumilov.github.io/ChurchAutoTune/> (English, Українська, Español): project status,
roadmap and a guide.

A free, open-source terminal app for macOS that guides church sound volunteers through measuring the room
and tuning a **Behringer X32 / Midas M32** system: sub/top crossover, sub alignment, system EQ,
verification and a report.

Most small churches run sound with volunteers who have no training, and a professional system tune costs
$1,000–3,000. ChurchTune aims to do that work in one rehearsal: it reads what it can from the console, tells
you where to put the microphone, measures, proposes changes, writes them safely with an undo file,
measures again and tells you whether it got better.

> [!WARNING]
> **Pre-alpha. Do not use it on a live system yet.** Every step of the wizard exists and works against the
> console emulator and a simulated hall, but nothing has been tried on a real console yet. The hardware checks
> and a pilot in real churches (M7) come first. See [Project status](#project-status).

## Contents

- [Install](#install)
- [First steps](#first-steps)
- [How it works](#how-it-works)
- [Project status](#project-status)
- [Safety](#safety)
- [Requirements](#requirements)
- [Developers](#developers)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

## Install

Open the Terminal app on your Mac, paste this line and press Return:

```bash
curl -LsSf https://raw.githubusercontent.com/mihailShumilov/ChurchAutoTune/main/scripts/install.sh | sh
```

The [script](scripts/install.sh) installs [uv](https://docs.astral.sh/uv/) if you do not have it, then installs the
`ctune` command with its own Python and adds it to your PATH. It needs no administrator password. At the end it runs
the self-check: if the last lines say "Self-check passed", all is well. Then open a new Terminal window so that the
`ctune` command is found.

If you already use uv, you can install it directly (the self-check then does not run by itself):

```bash
uv tool install --reinstall --force "churchtune @ https://github.com/mihailShumilov/ChurchAutoTune/archive/main.tar.gz"
```

To update, run the install line again. To remove it: `uv tool uninstall churchtune` (your measurements in
`~/ChurchTune` stay).

## First steps

```bash
ctune selftest           # check the installation: the whole wizard on a built-in emulator, no sound
ctune --emulator         # try the wizard on the built-in console emulator and a simulated hall
ctune --help             # all commands
```

`ctune selftest` and `ctune --emulator` use only a console emulator built into the program and a simulated hall:
they never contact a real console and never play sound.

To run the wizard on a real console, type just `ctune`. Until it has been tried in real churches, please do that
only as part of the [hardware checks](docs/hardware-checklist.md), with a scene saved on the console first.

## How it works

The guided wizard (English, Russian or Ukrainian) takes you through it step by step:

1. **Read the console.** Model, firmware, routing, which matrix or bus feeds the tops and the sub, the current
   EQ, and the USB routing for the measurement.
2. **Measure.** One or several measurement microphones (for example a Behringer ECM8000) on console inputs.
   Several mics are first calibrated against each other. The sweeps are played through the console's USB card,
   at several places in the room, with a quality check on every sweep.
3. **Analyse.** The response averaged over the places, the spread between places, the reverberation (RT60)
   and the junction between the subwoofer and the main speakers.
4. **Propose.** Packages of changes to choose from: a crossover, the sub level, broad system-EQ bands within
   strict limits, and advice on delay and polarity. It also says plainly what EQ cannot fix (room acoustics).
5. **Apply.** A table of every change, a typed confirmation word, an undo file written before the first change,
   and each value read back from the console after it is written.
6. **Check and report.** Measure again, compare, offer to put everything back. At the end a report is written:
   a page that opens without internet, Markdown, JSON and a short text to paste into a chat.

The method comes from a working tool that tuned one real hall on 2026-10-02. That session is part of the
golden tests.

## Project status

ChurchTune is built milestone by milestone (see [SPEC §15](docs/SPEC.md)).

| Milestone | Scope | State |
|---|---|---|
| M0 | Repository, the proven tool moved into a layered package, golden tests | done |
| M1 | X32/M32 driver: discovery, full read-only snapshot, speaker layout | done |
| M2 | Audio and measurement: one or several mics, mic calibration, signal checks | done |
| M3 | Guided wizard, saved progress, English / Russian / Ukrainian | done |
| M4 | Analysis and proposals | done |
| M5 | Apply, undo, check measurement, crossover | done, not yet tried on a real console |
| M6 | Report | done |
| M7 | Hardware checks and a pilot in real churches, one-command install, release 0.1.0 | in progress |

There is no release yet. Version 0.1.0 comes after the full wizard has run on a real X32 Producer with results no
worse than the manual session of 2026-10-02, and every row of the hardware checklist has been checked by a person.

## Safety

A sound system can hurt ears and speakers, and a church needs its console to work on Sunday. The design follows
these rules:

- **Read-only by default.** The console client can only read from a fixed list of addresses. One module
  writes, and only with a permission from the safety policy for one kind of change.
- **Limits.** EQ cut and boost, Q, crossover and level changes are bounded. Anything outside the limits is
  refused by the final check (the subwoofer planner keeps its target within the limits before that).
- **Undo first.** An undo file is on disk before the first write. It is never overwritten.
- **Readback.** Every write is read back from the console and compared.
- **Quiet audio.** Nothing louder than −6 dBFS peak leaves the computer. Fades on every start and stop.
  Ctrl+C stops the sound immediately.
- **Unchecked settings are not written.** Output delay and matrix polarity have not been checked on a real
  console yet. The wizard gives their values to set by hand; the expert command line writes them only with
  `--write-unverified` ([ADR 0005](docs/adr/0005-unverified-writes-opt-in.md)).
- **Development never touches hardware.** With `CTUNE_DEV_SAFE=1` the code refuses any console that is not on
  this computer (loopback, the emulator) and any audio device that is not the simulated one. All tests run that way.

Hardware behaviour is confirmed only by a person at a console, recorded in
[`docs/hardware-checklist.md`](docs/hardware-checklist.md).

## Requirements

- A Mac (macOS is the target platform; Linux runs the tests in CI).
- For real use: a Behringer X32 / Midas M32 (firmware 4.x) with the X-USB card, connected to the Mac by
  network and USB, and a measurement microphone.
- The installer brings Python 3.12 and everything else.

## Developers

```bash
git clone https://github.com/mihailShumilov/ChurchAutoTune.git
cd ChurchAutoTune
uv sync
CTUNE_DEV_SAFE=1 uv run pytest -q                 # all tests: X32 emulator + simulated audio
CTUNE_DEV_SAFE=1 uv run ctune --emulator          # the wizard on the emulator
CTUNE_DEV_SAFE=1 uv run ctune --emulator doctor   # find, read and explain an emulated X32
```

The full check that CI runs:

```bash
CTUNE_DEV_SAFE=1 uv run ruff format --check . && uv run ruff check . && uv run mypy \
  && CTUNE_DEV_SAFE=1 uv run pytest --cov --cov-report=json && uv run python scripts/check_coverage.py
```

The expert commands besides the wizard are transitional and print Russian texts for now
([ADR 0003](docs/adr/0003-transitional-expert-cli.md)).

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
