# 0009 — M4 proposals: console-neutral packages, a predicted junction, the point plan

Status: accepted (2026-10-14)

## Context

M4 (SPEC §15) turns a measurement into change proposals: crossover, sub alignment, sub level and
system EQ (§8.5), the channel audit, the point plan (§8.3) and the S11 screens. Most of the design
mathematics came with M0 from the working tool; M4 had to connect it to the wizard's models and
decide a few things the original tool left to its operator.

## Decision

1. **Packages are console-neutral data** (`design/packages.py`). A package lists EQ blocks per
   path (`SignalRef`) before and after, output delays per output number and which side waits,
   faders and polarities of every sub matrix, coded notes (`NoteCode`), a modelled effect, a risk
   and the packages it `requires`. Turning them into console writes is the driver's job in M5. The
   M0 design code keeps working on `ConsoleState`/`Roles`; `domain/console_paths.py` translates a
   `ConsoleSnapshot` into them (no roles when one matrix would be both sub and tops), and
   `with_roles` applies the roles the person confirmed in S4.
2. **Fixed order, each package designed after the kept ones before it:** crossover → alignment →
   sub level → system EQ. A package's "before" is the "after" of the packages it requires; when
   the person leaves one out, the later ones are designed again without it. Nothing in a saved
   plan depends on a change that will not happen.
3. **Predictions apply only the change of an EQ block.** The measured responses already went
   through the console's EQ; a prediction multiplies their zero-padded spectrum by proposed /
   present EQ (limited to ±24 dB per frequency near the zeros of removed cut filters), never by
   the whole proposed block. The crossover frequency is chosen on the speakers' natural responses (the
   present matrix EQ taken out). The alignment is searched on the junction with the proposed
   crossover; the system EQ is designed on the analysis shifted, point by point, by the predicted
   change of the junction packages. The verify measurement tells the truth (M5).
   **No crossover without a junction measurement.** Set blind, a crossover can open a deep hole
   at the junction (the phase of the speakers' own roll-offs decides, and only separate sub and
   tops measurements show it); on the reference hall's EQ it did, by 20 dB. Without them the
   packages are the system EQ alone, designed on today's response, and a note says why.
4. **Where things go** (SPEC §8.5): the crossover only on separate sub and top matrices; tops on
   Main LR, a sub off the matrices or a matrix shared by sub and tops make it a suggestion. The
   system EQ goes on the path that feeds every speaker (Main LR feeding tops and sub; a note warns
   when Main LR also feeds a stream, a recording or another room); otherwise on the tops, each top
   matrix designed on its own (their locked bands may differ), and only above the crossover
   frequency. Crossover, alignment and sub level need separately measured sub and tops; the M4
   wizard does not measure them yet (temporary mutes are M5), so it proposes the system EQ and
   says why the others are missing. Output delay and polarity are suggestion-only until
   checked on hardware (ADR 0005); a sub fader above 0 dB is too.
5. **Crossover frequency:** the frequency in 80..120 Hz nearest to 100 Hz where the tops are
   within 6 dB of their 200..1000 Hz level and the sub within 6 dB of its 40..80 Hz level
   (medians, so a mode does not set the reference); 100 Hz without junction measurements.
6. **Roll-off detection changed** (`analysis.response.low_cutoff_hz`): the pass band starts at the
   first frequency from 25 Hz up above which the response stays within the drop for a full third
   of an octave. The old rule took the highest dip below 200 Hz, so a sub/top cancellation or a
   room null moved the "speaker roll-off" up to 185 Hz and forbade any correction below it.
7. **The optimiser's commonness rule** (M0, `system_eq_opt`) now judges a cut where the band acts:
   each point's deviation is averaged over ±1/(2Q) octave (between 1/12 and 1/3 octave) instead of
   a fixed ±1/3 octave. A narrow resonance shared by every seat is cut; the same resonance in three
   seats of eight is left alone. The golden test of the reference hall is unchanged.
8. **The point plan** (`measure/plan.py`): a Halton sequence (bases 2 and 3, the first shifted to
   avoid mirror pairs about the centre line) inside the middle 60 % of the depth, at least 1 m (up
   to 2.5 m) from the side walls, off the centre line, points at least 1 m apart; four junction
   points: one near the middle of the seats, three spread out; the walk goes row by row. Places are
   named by row and side of the whole hall with the distance from the stage and from the centre
   aisle, and drawn as a word-free map that highlights the round's places. The plan is stored per
   venue (`venues/<slug>/plan.json`) for verify and kept when a later session gives no hall size.
9. **The channel audit** (`design/channel_audit.py`) is advice only, grouped for a volunteer:
   voices without a high-pass or with one above 250 Hz (speech in a reverberant church is often
   cut at 150–220 Hz on purpose), narrow cuts (Q ≥ 4) as probable feedback protection ("at most
   half as deep, at a rehearsal, monitors on"), cuts shared by many channels — called room
   compensation only when the proposed system EQ cuts nearby —, other deep cuts, big boosts,
   phantom on dynamic microphones. Voices are recognised by name in five languages; instrument
   names never count as voices.
10. **Choosing** is one screen per package: the reasons, the before/after table, then the question
    "Add … to the change plan?" right above the options. It works the same in the full-screen and
    the line interface and fits 80×24. The choice is saved in the session's `proposal.json`;
    nothing is written to the console.
11. **Driver contract** (`mixers/base.py`) gained two read-only members: `eq_grid` (the console's
    EQ value grid) and `headamp_of` (the preamp behind a physical input).

## Acceptance

`tests/integration/test_defect_hall.py`, on a simulated hall (`audio/fake/defects.py`): a sub 6 ms
late (±1 ms from seat to seat), wired inverted and 6 dB too loud, a broad 500 Hz hump and a
strong 50 Hz mode common to the points, and a narrow 800 Hz null in two of eight points. Every
package is proposed in order; afterwards the junction no longer cancels and has no hole, the sub
is level with the tops within 2 dB, the hump and the mode end within 2.5 dB of the target, and no
EQ band aims at the null. A second case measures through the reference hall's own console EQ and
checks that it is replaced, not counted twice; a third takes the wizard's path without a junction
measurement on that EQ and checks that no band of 63 Hz–2 kHz gets worse by more than 1.5 dB. The simulator is simplified on purpose (one room
per point shared by sub and tops, more direct sound and a drier low end than a typical church) so
the defects, not random seat-to-seat effects, dominate.

## Consequences

- M5 consumes `proposal.json` and must not trust it: it maps paths to console parameters, checks
  every value against the write scopes after quantization, re-reads the console and refuses a
  package whose "before" no longer matches, applies a package only with everything it requires,
  never writes `suggest_only` packages or the channel advice, and caps output delays.
- M5 adds the junction measurement that unlocks crossover, alignment and level in the wizard.
  Before designing the sub level, M5 redesigns the system EQ from the response without the EQ it
  proposed earlier (a cut on Main LR may have stood in for a sub level difference), and it flags
  counter-corrections between a common path and a speaker path (a shelf on the sub matrix that
  Main LR then cuts again).
