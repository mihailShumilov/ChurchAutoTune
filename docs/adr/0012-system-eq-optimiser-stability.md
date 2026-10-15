# 0012 — System-EQ optimiser: one proposal on every platform, one cut per problem

Status: accepted (2026-10-15)

## Context

After M5 was merged, CI on Linux failed the defect-hall acceptance test: the system-EQ package
vanished. Noise of 1e-7 dB in the input (another platform's floating point) sent the
least-squares solver of `design/system_eq_opt.py` into another of several near-equal local
minima, whose modelled RMS gain was 0.19999 dB against a 0.2 dB threshold. Five DSP reviews of
the fix showed more: the solver spread one resonance over up to five identical narrow bands (its
quadratic gain penalty makes N bands of g/N cheaper than one of g), which also let two bands cut
deeper than the −9 dB one band may.

## Decision

1. **Overlap penalty in the objective.** Two working cuts closer than half the wider one's width
   (at least 1/6 octave) add a residual proportional to both gains. The quadratic gain penalty
   stays: a pseudo-Huber penalty was tried and changed the golden 2026-10-02 proposal by one Q
   step.
2. **Moves from the plain solve.** Each round starts the solver from the best solution with one
   cut fewer (two cuts on one problem merged, centred by gain; or one cut taken out, with every
   idle cut held at 0 dB so the cut cannot move into one of them) and with an idle band put to
   work (at the largest remaining excess above the mode zone, or splitting a cut).
3. **One total order.** Every finished proposal (on the console grid, uncommon bands put back)
   gets one number in dB: weighted RMS deviation (as the solver weighs it), plus 0.001 per dB of
   gain (a tie-break), plus 0.05 for every second cut on the problem of another (the same
   spacing as the overlap penalty) and every stacked pair. Each round keeps the minimum; stacked
   cuts beyond the plain solve's are also refused outright. A pairwise rule tried before was not
   transitive and the result depended on the number of rounds. A flat price for every band was
   tried and priced the low-weighted 50 Hz mode cut out.
4. **The package gate.** A system EQ is proposed when the rounded RMS gain is at least 0.2 dB, or
   the largest excess above the target drops by at least 1 dB without the RMS getting worse; the
   screen then shows the largest excess (`effect-max-excess`).
5. A band that was flat and stays flat keeps its frequency and Q (no useless writes).

## Consequences

- The defect hall gives the identical proposal under noise; random halls get one cut at the
  centre of each resonance; two narrow resonances 0.42 octave apart keep two cuts; the golden
  2026-10-02 proposal is unchanged. That the choice is the same on every platform is checked by
  noise tests, not guaranteed.
- One `optimize_design` call takes 0.5–2.5 s on six points (main: 0.1–1.8 s). A hall without a
  Main path optimises each top matrix separately, so four matrices can take about 10 s.
- Open:
  - No move starts in the mode zone: a mode shared by half of the points but marked local by the
    greedy design (2/3 rule) is left alone, as on main. Decide whether such modes stay (they are
    positional) or get a start when at least `MODE_COMMON` of the points share them.
  - Stacked and crowded cuts are judged among the changed bands only: a new cut next to an
    unchanged existing one (for example the hall's 479.8 Hz −9 dB) is not counted, and their sum
    is not held to −9 dB.
  - The overlap residual is only C0 (`max(0, …)`, `sqrt(|g_i g_j|)`); a smooth kernel would give
    the solver a better Jacobian.
