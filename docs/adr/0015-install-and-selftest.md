# 0015 — M7: one-command install and the self-check

Status: accepted (2026-10-15)

## Context

SPEC §13 asks for installation with `uv tool install churchtune` or one `curl … | sh` command, and
SPEC §12 for `ctune selftest`: a check of the installed package with the emulator in-process and
the simulated audio. The people who install ChurchTune are volunteers on a church Mac: no
developer tools, often no Homebrew, no administrator rights to spare. The package is not on PyPI
(publishing it is the owner's decision, before 0.1.0 or after).

## Decision

1. **`scripts/install.sh`** installs a pinned uv into `~/.local/bin` when it is missing (uv's
   own installer), then `uv tool install --python 3.12 --reinstall --force` ChurchTune (running
   it again updates), puts uv's tool folder on the shell's PATH and runs `ctune selftest`; its
   last line points at `ctune --emulator`, not at a real console. No `sudo`, nothing outside the
   user's home, the whole body inside a function called on the last line (a cut download runs
   nothing), children read `/dev/null`, not the piped script.
2. **From a source archive, not `git+`.** The default source is
   `churchtune @ https://github.com/…/archive/<ref>.tar.gz` (`CHURCHTUNE_REF`, default `main`; a
   tag after the release). A fresh Mac has no git until the Xcode command line tools are installed,
   and a `git+` URL would start that installation. `CHURCHTUNE_SOURCE` overrides the source (a
   local wheel in CI); `CHURCHTUNE_NO_PATH` (also passed to uv's installer) and
   `CHURCHTUNE_NO_SELFTEST` skip those steps.
3. **`ctune selftest` runs the whole wizard**, not the shorter "two points and a dry run" of
   SPEC §12: the in-process emulator (`churchtune_emulator`, loaded lazily — `cli/selftest.py` is
   the third allowed importer next to `cli/main.py` and `cli/console.py`), the demo rig of the
   simulated hall and a scripted volunteer (`cli/scripted.py`, also used by the end-to-end
   tests); the volunteer and the measurements share one simulated hall. It writes to the
   emulator, makes the check measurement and the report, in a temporary folder that is removed
   afterwards (kept with `--keep`, or after a failure with the traceback in
   `selftest-error.txt`). The person's settings (`venue.toml`, `--console`, `CTUNE_CONSOLE`) are
   not read for it. It sets `CTUNE_DEV_SAFE=1` for its own process first, so the transport and
   the audio factory refuse anything but the loopback emulator and the simulated hall; as a
   second line, its wizard run and the scripted volunteer (which types the confirmation words)
   refuse to start outside dev-safe mode.
4. **`--emulator` switches dev-safe on for the process too.** The README recommends it to
   volunteers for a safe try; without dev-safe, the wizard's discovery broadcast could list the
   church's real console next to the emulator. The wizard then says it uses only the emulator
   and the simulated hall.
5. **CI installs the way a person does**: a macOS job builds the wheel and runs
   `scripts/install.sh` with it into a throwaway tool folder; the job fails if the self-check does.

## Consequences

- A broken package (a missing locale file, the emulator's reference dump, a dependency) is caught
  in CI and on the person's Mac before the first real session, with a message that asks for an
  issue.
- The self-check proves the program, not the hall or the console: the hardware checklist and the
  pilot stay with people at real consoles.
- When the package is published on PyPI, `uv tool install churchtune` becomes the short form and the
  script's default source can follow it.
