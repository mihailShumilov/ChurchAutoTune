# 0011 — M5 second part: the junction with temporary mutes, a fuller write order, the demo

Status: accepted (2026-10-14)

## Context

After the first part of M5 (ADR 0010) the wizard wrote the system EQ and the sub level, but never
proposed a crossover: that needs the sub and the tops measured apart at the junction points, and
the wizard could not mute anything. Writing a real crossover also showed that the never-louder
order of ADR 0010 refuses common changes (a high-Q shelf turning into a crossover slope, two cuts
that swap places). The `--emulator` demo stopped at the playback path, and three SPEC items were
open: the ±12 dB warning against the venue's first snapshot, a non-interactive mode, the delay
switch check.

## Decision

1. **Temporary matrix mutes** (`cli/mute_writes.py`, the `temp_mutes` scope). Only matrices,
   as the owner decided. The switches of the sub and top matrices are read fresh and written to
   the pending-restore file before the first mute; every switch is authorized with the typed
   word (`mute` / `заглушить` / `заглушити`), bound to that file, and may only mute or put back
   the exact recorded value. Nothing starts while mutes of an earlier run are pending.
2. **The junction step S10b** (`wizard/steps/junction.py`) runs when the confirmed topology has
   a sub and tops on their own matrices (otherwise mutes cannot separate them, and no crossover
   is proposed). Per round of microphones: place, warn, record the switches, three sweeps in one
   stream (sub, tops, everything; `measure.junction.measure_junction_round`), the originals back
   in `finally`. A restore that fails is tried every 2 s; after five tries the person is told
   which matrices to switch on by hand and the pending file stays. Junction points live in
   `junction/` of the session (apart from the ordinary points of the same name) and go into the
   design, which can then propose the crossover.
3. **Pending mutes come back first**: before the junction and before the last screen, the
   wizard reads the console fresh, shows the switches it would put back and asks for the word;
   a console changed while the list was shown is not overwritten. The wizard does not finish
   while mutes are pending.
4. **The order searches further before refusing** (`safety/order.py`). A band with no safe order
   now waits while other bands move and is tried again. When nothing moves: a stuck cut is copied
   into a spare band of its strip first (flat before and after; adding a cut is never louder), the
   cut moves, and the copy is removed later like any other change; failing that, a speaker matrix
   (never Main LR) is muted, all its remaining bands are changed, and it is switched back on. The
   simulation checks every step as before; the mutes are `temp_mutes` writes validated against
   the switch state of the fresh read (the undo uses the values its file recorded).
5. **Advice packages count as done by hand.** The alignment (delay, polarity) stays advice; the
   packages designed on top of it are written, and the advice is shown as a task right after the
   writes, before the check measurement judges the result. Every "before" is still checked
   against the console.
6. **Levels far from the venue's first snapshot**: a sub fader more than 12 dB away from the
   first snapshot of the venue needs a separate "yes" (default no) before the word. The design
   proposes at most 9.5 dB per step, so a quantized fader always stays within the ±10 dB scope.
7. **Non-interactive** `ctune apply-packages --yes-i-am-at-the-console`: replaces only the typed
   word; the fresh read, the second plan right before sending, the undo file and readback stay.
8. **The `--emulator` demo** (`cli/demo.py`) mutes the channels of the demo microphones once (a
   person would press MUTE) and connects the simulated hall to the emulator: the playback goes
   through Main LR's EQ and each speaker sounds only while its matrix is on. The demo reaches the
   junction, the writes and the check measurement.
9. **The delay switch**: the only paths that write an output delay (`ctune sub` and `hwcheck`
   with `--write-unverified`, and their undo) write the time with the switch or check the stored
   time before switching on (`cli/apply.py`); package plans never write delay. It stays behind
   the opt-in until the hardware checklist confirms it.

## Consequences

- The wizard can now propose and write a crossover, with the matrix briefly muted when a slope
  cannot be reached otherwise; the volunteer hears a short dropout of the sub or the tops.
- Hardware checklist rows 26–28 cover the temporary mutes, the junction and the crossover writes.
- The property tests of the order (PEQ, shelves, narrow bands on a dense grid, swaps) keep their
  strict "never louder" check; refusals remain possible only where no strategy applies.
