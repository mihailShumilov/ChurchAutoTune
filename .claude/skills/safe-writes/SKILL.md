---
name: safe-writes
description: The ChurchTune safety model for anything that changes a live console or makes sound — write scopes and allowlists, value limits, write ordering, undo and pending-restore files, readback, confirmation, audio level limits, and the review checklist. Use before touching src/churchtune/safety/, mixers/*/writer.py, mixers/*/client.py, audio level code, wizard steps S6–S13, or undo/proposal formats.
---

# Safe writes

The console runs Sunday services. A bad write can feed a measurement mic into the PA, kill the mains,
or blast a sweep at people. Every rule here is enforced in code **and** in tests. If a requirement seems
to need breaking a rule, stop and ask the user; record the decision in an ADR.

## 1. Development guard
- `CTUNE_DEV_SAFE=1` (set by tests/CI and every command Claude runs): `mixers` refuses non-loopback hosts,
  `audio` refuses real devices. Implemented in one function each, unit-tested both ways.
- `.claude/hooks/guard_console.py` blocks shell commands targeting private IPs or running `ctune` without the guard.

## 2. Architecture of a write
```
design → Proposal (pure data) → safety.plan(proposal, fresh_read) → WritePlan (scope, Changes)
       → safety.order_plan(plan) → wizard writes undo file
       → safety.authorize(plan, undo_path, confirmation) → WriteAuthorization
       → driver.writer(auth) → ScopedWriter (mixers/x32/writer.py) → transport.py (the only socket)
```
- `safety` is pure policy (allowlists, validators, ordering, authorization) and imports only `domain`.
  `mixers` imports `safety`, never the reverse — no import cycle.
- `ScopedWriter` is the only object with a `write()` method. It requires a `WriteAuthorization`, which exists only
  after (a) an undo file for the whole plan is on disk and (b) a typed human confirmation token is present.
  Before each send it re-validates path and value against the policy (defence in depth).
- `writer.py` is imported lazily inside the apply/undo/restore code paths, never at module level.
- The read-only client rejects any message with arguments except `/node ,s`, `/meters ,s`, `/xremote`.

## 3. Scopes (allowlist = regex + value validator)
| scope | allowed | validator |
|---|---|---|
| `temp_mutes` | `/ch/NN/mix/on` (measurement-mic channels, open inputs), `/bus/NN/mix/on` (monitor buses fed by the playback channels), `/mtx/0N/mix/on` (speaker isolation, sub/top junction), `/main/st/mix/on` (junction only, when no other way to separate paths) | value 0 (mute) or the exact original from the snapshot; `pending_restore.json` first; measurement-mic channels are unmuted only after the user confirms the mics are unplugged |
| `system_eq` | `/main/st/eq/[1-6]/(type|f|g|q)`, `/main/st/eq/on`, `/mtx/0[1-6]/eq/[1-6]/(type|f|g|q)`, `/mtx/0[1-6]/eq/on`, `/bus/NN/eq/...` (sub path only; UNVERIFIED → suggest-only) | boost ≤ +3 dB (tops/main) / ≤ +6 dB (sub); cut-filter bands only in the `crossover` package, band 1 HPF / band 6 LPF of **separate** paths (never on a common path); never switch off an EQ containing a cut filter; switching an EQ **on** only if all its bands pass the limits; values pre-quantized |
| `levels` | `/mtx/0N/mix/fader`, `/bus/NN/mix/fader` of the sub path | |Δ| ≤ 10 dB per package vs fresh read; result ≤ 0 dB; cumulative vs the venue's first snapshot > ±12 dB needs an extra warning + confirmation |
| `delay` | `/outputs/main/NN/delay/(on|time)` | ≤ 50 ms; write `time` before `on`; write UNVERIFIED → suggest-only until hardware check |
| `polarity` | `/mtx/0N/preamp/invert` of the sub path (matrix only) | toggle only within the `align` package; write UNVERIFIED → suggest-only until hardware check |
Forbidden forever: `/config/*`, `/headamp/*`, `/-action`, `/-snap`, `/save`, `/load`, `/-prefs`, `/fx/*`, inserts,
channel EQ/dynamics/faders, `/main/st/mix/fader`, `/main/m/mix/fader`, card settings, anything not listed.

## 4. Ordering and transactions
1. Fresh read of every target parameter; if it differs from the snapshot, show the diff and require a decision.
   The same applies to undo and restore: compare the console with the expected "after" state first.
2. Undo file (`undo/<ts>-<scope>.json`: path, old normalized value, old step index, old `/node` line) — fsync.
3. **One ordering rule:** in every intermediate state the modeled response of every path stays ≤ max(before, after) + 0.5 dB
   at every frequency 20 Hz–20 kHz. `safety.order_plan()` builds the order; a hypothesis property test simulates all
   intermediate states. Base algorithm: faders down and mutes → for each EQ band whose type/f/q changes: neutralize
   (g → 0 dB), then type, f, q, then target g (bands changing only g downward go immediately) → boosts and faders up last
   → delay `time` then `on`.
4. Skip writes whose quantized step already equals the target.
5. Readback each write (plain query + `/node`), compare step index. Mismatch → stop, report, offer undo of what was written.
6. Log each write to `session.log.jsonl`.

## 5. Temporary mutes
- Write `pending_restore.json` (originals) **before** the first mute; delete it only after every restore reads back.
- Restore in `finally`, on Ctrl+C, on OSC loss (retry every 2 s, show manual instructions). If the file still exists at
  the next start of `ctune` (or on `ctune restore`): fresh read → diff → typed confirmation; never overwrite silently.
- Use `/xremote` during the mute window; if someone changes a muted target by hand, do not overwrite it on restore —
  report instead.

## 6. Confirmation
- Interactive: user types the confirmation word (i18n key `confirm-apply-word`), not just Enter.
- Non-interactive: `--yes-i-am-at-the-console` plus an explicit proposal/undo path. No env var can replace it.

## 7. Audio
- Output limiter at −6 dBFS peak in the backend (applies to every buffer), unit-tested with full-scale input.
  It does not limit SPL in the hall (console gain/faders follow) — loudness is controlled from the measurement mics:
  abort the ramp if the mic jumps > 6 dB above target, or if the output rose 12 dB while the mic rose < 6 dB.
- Default sweep −24 dBFS; increases only via the level step; SPL cap 90 dB(C) when anchored (max 95, config).
- Fade-in/out ≥ 50 ms on every stimulus; Ctrl+C → `stream.abort()`; fake backend asserts silence within 50 ms.
- First sound of a session is preceded by the warning screen (SPEC S8/§9.4).

## 8. Review checklist (safety-reviewer uses it)
- [ ] No new socket code outside transport.py; no write path outside writer.py.
- [ ] Every new allowed path has: regex test (accept + reject neighbours), validator test, emulator readback test.
- [ ] UNVERIFIED addresses are listed in `docs/hardware-checklist.md` and gated (suggest-only on that model until verified).
- [ ] Undo written before first write; pending_restore before first mute; both covered by tests with injected failures.
- [ ] Ordering property test still passes; limits cannot be bypassed by negative/NaN/inf values.
- [ ] Coverage 100 % for safety/, writer, client.
- [ ] `tests/unit/safety/*` not weakened (diff check).
