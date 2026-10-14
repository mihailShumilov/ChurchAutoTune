## What and why

<!-- What does this change, and why? Link the issue: "Closes #123". -->

## How it was tested

<!-- Commands you ran; new or changed tests. -->

## Checklist

- [ ] The full check passes locally (format, lint, mypy, tests, coverage gates; see CONTRIBUTING.md).
- [ ] Tests were written for the change; golden tests are green or the reference change is explained above.
- [ ] `CHANGELOG.md` is updated under *Unreleased*; an ADR is added for a significant decision.
- [ ] Commits follow Conventional Commits.

## Safety

<!-- Tick what applies. Changes in any of these areas get an extra safety review. -->

- [ ] This change does **not** touch the console client, the writer, `safety/`, audio levels, temporary mutes,
      undo or proposal files.
- [ ] If it does: no address was added to an allowlist without a test; no OSC address or formula was guessed
      (unconfirmed ones carry `# UNVERIFIED:` and a row in `docs/hardware-checklist.md`); safety tests were not
      weakened, skipped or marked xfail.
- [ ] Nothing was run against a real console or real speakers during development (`CTUNE_DEV_SAFE=1`).
