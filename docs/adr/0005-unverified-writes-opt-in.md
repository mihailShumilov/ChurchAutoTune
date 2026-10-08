# 0005 — Writes never verified on hardware need an explicit opt-in

Status: accepted (2026-10-08) by the project owner

## Context

Two kinds of writes in the `sub` scope were never done on a real console:

- output delay, `/outputs/main/NN/delay/{time,on}` (hardware checklist row 1; the formula and the
  step count are unconfirmed as well);
- matrix polarity, `/mtx/NN/preamp/invert` (hardware checklist row 2).

In the hall session of 2026-10-02 the alignment ended with "no delay, level only", so neither was
exercised. SPEC says a capability stays `suggest_only` until a human has filled the checklist row.
The original tool wrote both without any gate.

## Decision

1. `WritePolicy` refuses these writes unless it is built with `allow_unverified_writes=True`. The
   default is off. `safety.policy.is_unverified_write(path)` is the single definition of the set.
2. The expert CLI builds such a policy only for `ctune sub align --write-unverified`,
   `ctune undo <file> --write-unverified` and `ctune hwcheck` (see below). For an undo the gate is lifted only when the file really
   holds a delay or polarity line.
3. Without the flag:
   - `sub align` prints the values as advice ("set by hand") and exits with code 3; the sub level
     can still be written with `--only-level`;
   - `undo` of a file that holds a delay or polarity line is refused with code 3 before the console
     is asked anything, and prints what to put back by hand.
4. With the flag the diff is followed by a warning that lists the unverified parameters, so the
   operator confirms them knowingly.
5. A delay is written time first, switch second; on the way back the switch goes off before the
   time changes. A stored time never reaches the signal path half-way through a plan.

## Consequences

- The default behaviour is stricter than the original tool: an alignment with delay or polarity is
  advice until someone opts in.
- An alignment written with the flag can be undone only with the flag. That is deliberate: the undo
  is the same unverified write in the other direction.
- When rows 1 and 2 of the checklist are filled, the gate moves to the per-model capability table
  (`mixers/x32/capabilities.py`, milestone M1/M5) and the flag is removed.

## Why the flag exists

The alternative was "advice only", with no way to write these parameters from the CLI. The owner
chose to keep the flag: the hardware check of rows 1 and 2 needs a way to send the write, and the
wizard (M5) will not expose it.

The check itself has its own command, because in the reference hall the alignment of 2026-10-02
found less than 1 dB to gain and `sub align` writes no delay there even with the flag:

- `ctune hwcheck delay --output N [--ms 1.0]` sets one output delay to an absolute time (at most
  10 ms) and switches it on. The output must be fed by a matrix, its delay must be off (a live
  delay line is never moved) and its stored time must be one an undo can write back (50 ms or less);
- `ctune hwcheck polarity --matrix N` flips the polarity of the sub matrix (SPEC §9.2 allows a
  polarity write on the sub path only). The current polarity is read as a parameter right before
  the plan is built; no answer is a refusal, here and in `sub align`.

Running `hwcheck` is the consent to the unverified write, so it takes no extra flag; the diff still
carries the warning, and the way back is `ctune undo <file> --write-unverified`.
