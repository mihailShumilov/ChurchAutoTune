# Test fixtures

## `x32/dumps/`

Read-only `/node` dumps of a real X32 Producer (fw 4.09), one reply line per node.

| File | When | State |
|---|---|---|
| `x32_dump_2026-09-29.txt` | before the measurement session | Main LR EQ as it had been set by ear |
| `x32_dump_2026-10-02.txt` | evening of the session, before the first write | measurement mic patched, Main LR up |

The dumps are anonymized with `scripts/anonymize_fixtures.py`:

- scribble-strip names that were people's names are replaced by neutral ones (the script lists the paths);
- the console name and the console id are replaced;
- the console's LAN address is replaced by the documentation address `192.0.2.80`.

Every numeric value, the matrix names, the line order and the line count are unchanged, so the dumps still
describe the real routing, EQ and levels. Do not edit them by hand; to add a dump, run the script on the raw
file and commit only its output.
