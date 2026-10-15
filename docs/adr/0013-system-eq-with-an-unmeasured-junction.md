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
`BlindJunction` (design/system_eq_opt.py):

1. **The zone** is 0.6–2 fc around the crossover the console has (the cut filter on the sub's
   last band and the tops' first band, 50–250 Hz, `packages.present_crossover_hz`), or 100 Hz
   when there is none.
2. **Cuts in the zone**: a new cut centred in the zone is at most −3 dB and no narrower than
   Q 1.0; a cut centred outside cuts no deeper than −3 dB at the nearest zone edge; all new cuts
   together no deeper than −3 dB anywhere in the zone. **No boost centred in the zone**: a dip
   at the junction may be a cancellation. The solver sees these limits as residuals; the
   finished proposal enforces them on the console grid.
3. **Seat guard**, over the whole corrected range, in thirds of an octave: no seat more than
   2 dB further from the target than it was, no third changed by more than 4 dB (a seat swinging
   from above the target to below it is not "no worse"), the average no more than 1.5 dB worse.
   Only cuts are softened (halfway back each step); a cut that took the place of an existing
   correction gives that correction back whole.
4. **Cut bands are matched to slots** by the nearest old frequency before uncommon bands are put
   back (in every design, not only blind): putting a band back restores what was at that place,
   not whatever the solver moved into that slot. This also fixed the hall's 500 Hz cut being lost.
5. **An idle boost** is started at one of the three largest shortfalls (outside the blind zone);
   the cut moves of ADR 0012 never brought a boost back.
6. The note to the volunteer says that around 60–200 Hz the EQ only makes gentle cuts and that
   measuring the sub and the tops apart tunes the bass fully.

## Consequences

- On the defect hall with the reference hall's EQ, the worst seat (level-aligned thirds, as the
  check measurement aligns) is at most 1.85 dB worse across five junction variants (was up to
  3.25 dB); the golden 2026-10-02 proposal is unchanged.
- Without the junction measured, a dip at about 3 fc is left as it is when a boost's skirt would
  feed the excess the gentle junction cuts leave; the acceptance test asks there for "not worse"
  (acoustics review: the 200–400 Hz region is where churches boom, a small shortfall is harmless).
- Open: the report does not yet say "kept as it was: seat guard" when the guard gives an existing
  correction back.
