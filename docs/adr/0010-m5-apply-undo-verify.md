# 0010 — M5 apply, undo, verify: five scopes, a never-louder order, a typed word

Status: accepted (2026-10-14)

## Context

M5 (SPEC §15) writes the chosen packages to the console, takes them back, and measures again to
judge the result (S12–S14, §8.6, §9.2–9.3). Until M4 the only writes were the expert CLI's Main LR
EQ (`main` scope) and the sub commands (`sub` scope). The owner decided four questions before the
work started: temporary mutes on matrices only; a typed confirmation word everywhere (wizard and
CLI); output delay and polarity stay advice in the wizard; when the console no longer matches a
package's "before", the default is to cancel and measure again.

## Decision

1. **Five more write scopes** (`WriteScope`): `temp_mutes` (matrix mutes only: 0 or the exact
   original value), `system_eq` (Main LR and the speaker matrices; boosts ≤ +3 dB Main/tops,
   ≤ +6 dB sub; cut filters only on band 1 of a top path and band 6 of the sub path, and only with
   `allow_cut_filters`; an EQ is never switched off by a plan), `levels` (only the sub path's
   fader, ≤ 0 dB, ≤ ±10 dB from the fresh read), `delay` and `polarity` (behind the unverified
   opt-in of ADR 0005). The validators live in `safety/policy.py`; the sub and top matrices come
   from a fresh read of the console (`detect_roles`), never from the proposal file.
2. **One ordering rule, by simulation** (`safety/order.py`). A plan is a set of strip states
   before and after. Faders down and mutes go first; each changed band is tried in every order of
   its parameters and with "neutralise first"; boosts, faders up, EQ switches and unmutes go last.
   Every intermediate state is simulated on every speaker path (Main LR in front of every matrix,
   the strict choice) and must stay within `max(before, after) + 0.5 dB` from 20 Hz to 20 kHz; no
   such order is a refusal. A Hypothesis property test checks random plans on Main LR and two
   matrices.
3. **The driver maps steps to writes** (`mixers/x32/plan_writes.py`): each step becomes the write
   of one parameter in its scope, writes already on the console's grid step are left out, and the
   old values come from the fresh read. Consecutive writes of one scope form a run; **every run is
   authorized on its own** and all runs share one undo file.
4. **A typed word, bound to the undo file.** `authorize_typed` needs a `TypedWord` (≥ 4 letters,
   case-insensitive; `apply`/`применить`/`застосувати`, `undo`/`вернуть`/`повернути`) and the
   SHA-256 of the undo file, which is on disk before the first write. The writer refuses when the
   file changed after the authorization, and checks a stop flag between writes. The undo file gets
   the results after every run, so an interruption leaves an honest record.
5. **The proposal file is untrusted.** Only chosen packages that are not suggestions are written,
   each after the packages it requires. The packages were designed one on top of the other, so
   each "before" is compared on the console's grid with the fresh state plus the packages before
   it; any difference refuses the whole plan (measure again). Every write is validated again.
6. **Three-way undo.** For each write of a package undo file: still the new value → put the old
   one back; already the old value → nothing; anything else → changed by hand, left alone and
   listed (SPEC §9.3 says not to overwrite hand changes silently; we never overwrite them). The
   way back is planned with the same ordering rule and the same scopes.
7. **The wizard writes through a port** (`wizard.services.WritePort`). The wizard shows the table
   (setting · now · will be, on the console's grid) and asks for the word; the port reads fresh,
   validates, orders, writes the undo file, authorizes each run and reads back. The X32 port lives
   in `cli/apply_packages.py` next to `ctune apply-packages`, which does the same from the
   terminal. The layer rule lets the wizard import `safety` again (pure policy only).
8. **Verify (§8.6)** is `analysis/verdict.py`: `M`, `ΔW_worst`, `ΔB`, `Δσ` on third-octave
   centres, ranges starting at the speakers' roll-off at the lowest; thresholds configurable.
   S13 measures at least four baseline points again (the worst, the one nearest the middle, then
   in walking order; as many as there are microphones) into `verify/` of the session, shows the
   verdict with a two-row block chart, and offers the undo right away when it got worse. The
   junction criterion `J` is not used: the wizard writes no alignment yet.
9. **Rules added after the first safety review** (each with a regression test on the emulator):
   a package never switches an EQ on or off (a block that was off may hold an unchecked boost,
   and the undo of "on" would have to switch it off); only the crossover package sets, moves or
   removes a cut filter, and only a plan holding it gets `allow_cut_filters`; right before
   sending, the port (and the CLI after the word) plans again from a fresh read and sends only
   if that plan equals the one on the screen; the order after "neutralise first" brings the gain
   back, and `order_steps` refuses steps that do not reach the planned state; the undo collapses
   the writes of one path (first old value, last new value); a run refused after an earlier run
   was written ends the plan as incomplete, so the undo is offered; the old `authorize()` (a
   plain "y") accepts only the `main` and `sub` scopes; a package that requires an advice
   package is left out.
10. **After the second safety review and the DSP review**: `safety/cut_filters.py` keeps every
   cut-filter change (crossover and undo alike) off Main LR, on band 1 of a top path or band 6 of
   the sub path, with the corner in 40–250 Hz; only an undo may remove a cut. An undo never
   writes an EQ switch and recognises every value its plan wrote (a stop half-way leaves a
   neutralised band that still goes back). The order is simulated on a 1/48-octave grid plus
   every band centre of the plan. The verdict aligns each "after" curve to "before" by the
   median band difference (a cut near 500 Hz moves the 500–2000 Hz normalisation), takes the
   deviation from the target without the overall level, and uses third-octave band means; the
   averages are made from the common points only. The verify target is the default
   `TargetSpec`, the one the wizard designs with (the proposal file does not store it yet).
   The simulated hall keeps its EQ filter state between stream blocks.
11. **Delay and polarity are advice** in the wizard and in `apply-packages`: shown as values to set
   by hand. Their writes stay behind `--write-unverified` in the sub commands until the hardware
   checklist confirms them.

## Consequences

- Every write path (CLI and wizard) goes through the same planning, validation, ordering and
  authorization code; the static guarantees (one `WriteAuthorization` construction site, lazy
  writer imports) still hold.
- A plan whose never-louder order does not exist is refused instead of being written in a
  "best effort" order.
- Not yet in M5: temporary mutes and the junction measurement inside the wizard (so the wizard
  still never proposes a crossover blind), the ±12 dB warning against the venue's first snapshot,
  a non-interactive `--yes-i-am-at-the-console` mode, and a check of the stored delay time when a
  plan switches an output delay on (delay writes stay behind `--write-unverified` until then).
