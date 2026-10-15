# 0013 — System EQ while the sub / tops junction is not measured

Status: accepted (2026-10-15)

## Context

ADR 0011 §11 left one item open: without the sub and the tops measured apart, the system EQ
aimed at the bass of a hot, late or inverted sub with a deep cut (for example 125 Hz −9 dB Q0.8).
The cut is the same at every seat, and the seats without that bass got thin: one seat 3.25 dB
further from the target at 200 Hz while the average improved. The check measurement caught it and
offered the undo, but the proposal should not do it in the first place. The acoustics review
recommended broad, gentle cuts at the junction and a per-seat guard.

## Decision

When the topology has a sub and the junction was not measured, `optimize_design` gets a
`BlindJunction` (`design/blind_junction.py`):

1. **The zone** is 0.6–2 fc around the crossover the console has (the cut filter on the sub's
   last band and the tops' first band, 50–250 Hz, `packages.present_crossover_hz`), or 100 Hz
   when there is none.
2. **Cuts in the zone**: a new cut centred in the zone is at most −3 dB and no narrower than
   Q 1.0; a cut centred outside cuts no deeper than −3 dB at the nearest zone edge; all new cuts
   together no deeper than −3 dB anywhere in the zone. An existing correction centred in the zone
   stays as it was: blind, it is not ours to move. **No boost centred in the zone**: a dip
   at the junction may be a cancellation. The solver sees these limits as residuals; the
   finished proposal enforces them on the console grid.
3. **Seat guard**, over the whole corrected range, in thirds of an octave: no seat more than
   2 dB further from the target than it was, no third changed by more than 4 dB (a seat swinging
   from above the target to below it is not "no worse"), the average no more than 1.5 dB worse.
   Only cuts are softened (halfway back each step, on the console grid), always for the worst
   broken limit a cut can mend, so a boost's or the old EQ's doing elsewhere does not stop it; a
   cut that took the place of an existing correction gives that correction back whole.
4. **Working cut bands are matched to slots** by the nearest old frequency before uncommon bands
   are put back (in every design, not only blind): putting a band back restores what was at that place,
   not whatever the solver moved into that slot. This also fixed the hall's 500 Hz cut being lost.
5. **An idle boost** is started at one of the three largest shortfalls (outside the blind zone);
   the cut moves of ADR 0012 never brought a boost back.
6. The system-EQ package says, with the zone's own limits (0.6–2 fc), that the EQ only turns the
   sound down a little there and why; the general note says the crossover, the alignment and
   the sub level come after the step that measures the sub and the tops apart (the old text
   promised them "in a later version").

## Consequences

- On the defect hall with the reference hall's EQ, the worst seat (level-aligned thirds, as the
  check measurement aligns) is at most 1.85 dB worse across five junction variants (was up to
  3.25 dB); the golden 2026-10-02 proposal is unchanged.
- The slot matching of 4 and the idle boost of 5 change designs with a measured junction too: on
  the defect hall's "bad" junction a system EQ is now proposed where none was. Covered by the
  acceptance tests; the golden 2026-10-02 proposal does not change.
- A test checks the worst seat over five junction variants (≤ 2 dB per third, levels aligned) and
  that the hall's 500 Hz cut stays.
- Open: the report does not yet say "kept as it was: seat guard" when the guard gives an existing
  correction back.
