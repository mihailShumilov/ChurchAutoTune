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

## `golden/hall-2026-10-02/`

Real measurement data of the reference hall from the session of 2026-10-02 (empty hall, one measurement
microphone, X32 Producer), about 33 MB. It is the regression baseline for the ported analysis and design code:
`tests/golden/test_hall_2026_10_02.py` requires the new code to reproduce what the original tool computed and
what was applied to the console that evening.

| Directory | Content |
|---|---|
| `measurements/2026-10-02/` | baseline, 6 points: impulse response (`ir.wav`), raw 1/6-octave response (`fr.csv`), quality (`meta.json`); `p1-center/recording.wav` is the raw sweep recording of the one point taken with a single sweep |
| `measurements/after-sub/` | the same 6 points after the crossover and the sub level were set; the applied Main LR EQ was designed from these |
| `measurements/sub-2026-10-02/`, `measurements/lvl/` | sub / top junction at 2 points: `ir_sub.wav`, `ir_tops.wav`, `ir_all.wav` on one time base, before and after the first level change |
| `results/` | what the original tool produced: the EQ proposal that was applied, the session reports, the alignment summaries |
| `undo/` | the undo files of that evening: every console write with its readback |

WAV files are 32-bit float, mono, 48 kHz, exactly as recorded. Text files went through
`scripts/anonymize_fixtures.py` (console address and id). The hall's photos and floor plans are not part of
the repository.

Not available in the source data, so not covered by golden tests: a console dump after the changes, a verify
session, and the result of the greedy design method.
