# Contributing to ChurchTune

Thank you for helping. ChurchTune is meant for church volunteers who are not sound engineers, and it can change
a live console and play test signals through speakers. So the bar for safety and clarity is high, but the
process is simple.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Ways to help

- **Hardware checks.** If you have an X32 or M32, the most valuable contribution is running a row of
  [`docs/hardware-checklist.md`](docs/hardware-checklist.md) and reporting the result with the
  *Hardware check result* issue form. Only a person at a console can confirm how it behaves.
- **Console dumps.** Read-only dumps of other models, firmware versions and routings help the topology
  inference (milestone M1). Remove names and anything personal first (`scripts/anonymize_fixtures.py`).
- **Bugs and ideas.** Use the issue forms.
- **Translations.** English, Russian and Ukrainian are planned (milestone M3).
- **Code.** Pick an issue labelled `good first issue` or `help wanted`, or open an issue first for anything
  larger than a small fix, so we can agree on the approach.

## Safety rules for every contributor

1. **Never run tests or development builds against a real console or real speakers.** Prefix every command
   that runs the app or the tests with `CTUNE_DEV_SAFE=1`. The code then refuses any console that is not on
   `127.0.0.1` and any audio device that is not the fake backend.
2. **Do not guess OSC addresses or value formulas.** Anything not confirmed by a real dump gets an
   `# UNVERIFIED:` comment in the code and a row in `docs/hardware-checklist.md`.
3. **Writes go through one place.** The only socket is `src/churchtune/mixers/x32/transport.py`. The read-only
   client has an allowlist; the writer needs a scoped authorization from `safety/`. Adding an address to an
   allowlist needs a test and a careful review.
4. **Never weaken, skip or xfail the safety tests** (`tests/unit/safety/`, `tests/integration/test_safety_e2e.py`).
5. **Audio:** nothing louder than −6 dBFS peak leaves the computer, fades on start and stop, Ctrl+C aborts
   the stream.

If you find a way around any of these, please report it privately (see [SECURITY.md](SECURITY.md)).

## Development setup

You need macOS or Linux, Python 3.12 and [uv](https://docs.astral.sh/uv/).
On Debian/Ubuntu also install `libportaudio2 libsndfile1`.

```bash
git clone https://github.com/mihailShumilov/ChurchAutoTune.git
cd ChurchAutoTune
uv sync
uv run pre-commit install
CTUNE_DEV_SAFE=1 uv run pytest -q
```

Useful commands:

```bash
CTUNE_DEV_SAFE=1 uv run pytest tests/golden -q   # the reference hall session of 2026-10-02
CTUNE_DEV_SAFE=1 uv run ctune --help             # expert CLI; --console HOST[:PORT] --audio fake --home DIR
uv run pre-commit run --all-files
```

## Before you open a pull request

Run the full check. CI runs the same steps on Linux and a smoke test on macOS.

```bash
CTUNE_DEV_SAFE=1 uv run ruff format --check . && uv run ruff check . && uv run mypy \
  && CTUNE_DEV_SAFE=1 uv run pytest --cov --cov-report=json && uv run python scripts/check_coverage.py
```

- Follow [`docs/CODING_STANDARDS.md`](docs/CODING_STANDARDS.md): mypy strict, units in names (`freq_hz`,
  `gain_db`), pure `domain/`, `analysis/` and `design/` packages, the layer rule (checked by
  `tests/unit/test_architecture.py`).
- Write the test first. DSP code is tested with synthetic signals whose answer is known.
  Safety-critical code needs 100 % line and branch coverage (`scripts/check_coverage.py`).
- Changes to the measurement, analysis or design math must keep the golden tests green, or explain in the PR
  why the reference values change.
- Update [`CHANGELOG.md`](CHANGELOG.md) under *Unreleased*. Record a significant design decision as an ADR in
  [`docs/adr/`](docs/adr/).
- User-facing text goes into the translation catalogues once they exist (milestone M3), not into code.

## Commits and pull requests

- [Conventional Commits](https://www.conventionalcommits.org/): `feat(scope): …`, `fix(scope): …`, `docs: …`,
  `test: …`, `chore: …`. Imperative mood, English.
- Small, atomic commits. One topic per pull request.
- Fill in the pull request template, including the safety checklist.
- Pull requests are merged after CI is green and a maintainer has reviewed them. Changes to the writer, the
  read-only client, `safety/`, audio levels or undo files get an extra safety review.

## Language

Code, identifiers, comments, commit messages and contributor documentation are in English. The specification
([`docs/SPEC.md`](docs/SPEC.md)) is in Russian; issues and discussions in English, Russian or Ukrainian are
welcome.

## License

By contributing you agree that your contribution is licensed under the
[GPL-3.0-or-later](LICENSE), the license of this project.
