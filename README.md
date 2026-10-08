# ChurchTune

Free, open-source terminal app for macOS that guides church sound volunteers through measuring the room
and tuning a Behringer X32 / Midas M32 system: crossover, sub alignment, system EQ, verification and a report.

> **Status: pre-alpha.** The project is being ported from a tool that was proven in one real hall.
> It is not ready for use on a live system yet.

## Development

```bash
uv sync
CTUNE_DEV_SAFE=1 uv run pytest -q
```

Development and tests run only against the console emulator on `127.0.0.1` and the fake audio backend.
`CTUNE_DEV_SAFE=1` makes the code refuse real consoles and real audio devices.

What exists today is the measurement and design core with a transitional expert command line
(`ctune --help`, Russian texts). The guided wizard arrives in later milestones (SPEC §15).
Output delay and polarity writes were never verified on hardware and are off by default
([ADR 0005](docs/adr/0005-unverified-writes-opt-in.md), [`docs/hardware-checklist.md`](docs/hardware-checklist.md)).

- Specification: [`docs/SPEC.md`](docs/SPEC.md) (Russian)
- Coding standards: [`docs/CODING_STANDARDS.md`](docs/CODING_STANDARDS.md)
- Architecture decisions: [`docs/adr/`](docs/adr/)

## License

GPL-3.0-or-later. See [`LICENSE`](LICENSE).
