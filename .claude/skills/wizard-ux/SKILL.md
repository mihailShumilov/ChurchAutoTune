---
name: wizard-ux
description: UX, copy and i18n rules for ChurchTune's terminal wizard aimed at untrained church sound volunteers — screen anatomy, question patterns, auto-detected values, errors, progress, keyboard, accessibility, Fluent (.ftl) conventions for en/ru/uk. Use when building or changing anything in src/churchtune/app/, wizard prompts, error messages, or locale files.
---

# Wizard UX for volunteers

The user is a volunteer in an empty church hall, holding a mic stand, glancing at a laptop on the balcony.
Every screen must be understandable in 5 seconds without audio knowledge.

## Screen anatomy (every step)
```
┌ Step 7 of 13 · Microphones  ───────────────────────── Venue: Vidrodzhennia ┐
│ What happens now: we find which console input each measurement mic is on.  │
│                                                                            │
│  <one task or one question>                                                │
│                                                                            │
│  <live widget if relevant: level meters, hall map, diff table>             │
└ Enter continue · B back · S skip · Q save & quit · Ctrl+C stop sound ──────┘
```
- Header: step n/total (counting only steps that apply to this goal/configuration; recompute when that changes) + plain name + venue. One line "What happens now" (≤ 2 sentences).
- One question or one physical task per screen. At most 3 actions besides the footer keys.
- Footer always shows the available keys. Works at 80×24; scrolls inside panels, never the whole screen.

## Patterns
- **Detected value → confirm, don't ask.** "Found: X32 Producer at 192.0.2.80 (firmware 4.09). [Enter] Correct · [E] Edit".
  Mark auto-detected values with "detected" so trust is explicit.
- **Physical task:** imperative + where + how + why in one short block, then "[Enter] Done".
  "Put mic 2 at point 5 — centre aisle, row 6. Capsule at ear height (1.2 m), pointing at the stage. Step back 2 m."
- **Before any sound:** say what will be heard and how loud, how long, and that Space/Ctrl+C stops it.
- **Before any console change:** show the diff table (what · now · after), the reason in one line, and the undo promise.
- **Progress for anything > 1 s:** progress bar + what is happening ("Reading the console… 412/1960").
- **Results:** verdict first ("Better", "No real change", "Worse — undo recommended"), then 1–3 findings in plain words,
  numbers last and optional ("Details: D").
- **Choices:** ≤ 5 options, the recommended one first and labelled "(recommended)".

## Copy rules
- Plain words; a term appears with a short explanation the first time (glossary in `glossary.ftl`):
  "crossover (splits the sound: lows to the sub, the rest to the main speakers)".
- Active voice; buttons say exactly what happens ("Apply 4 changes", "Undo changes", "Measure again").
- Errors: what happened + what to do, no blame, no apologies, no codes in the main line
  ("The console doesn't answer. Check that the Mac is on the same network and VPN is off. [R] Retry · [I] Enter IP").
- Never claim certainty the data doesn't support; "probably", "in 6 of 8 points" are fine.
- Never promise EQ will fix reverberation; say what will help instead.
- Numbers formatted per locale (ru/uk decimal comma); units Hz, kHz, dB, ms, m with a non-breaking space.

## Accessibility
- Meaning never by color alone: ✓ OK, ! warning, ✗ problem + words. Respect `NO_COLOR`.
- No mouse required. No blinking. Live meters update ≤ 10 Hz.
- Contrast works in light and dark terminal themes (use Textual theme variables only).

## i18n (Fluent)
- Files: `src/churchtune/i18n/locales/{en,ru,uk}/{common,steps,errors,report,glossary}.ftl`.
- Keys: `kebab-case`, prefixed by area: `step-mics-title`, `err-console-unreachable`, `report-verdict-better`.
- Variables named by meaning with units: `{ $freq_hz }`, `{ $count }`. Plurals with Fluent selectors (ru/uk need one/few/many).
- English is the source; ru and uk must be natural, not literal (the owner reads ru/uk; ask for review of new strings).
- Test `tests/unit/test_i18n.py`: same key set in all locales, same variables, no unused keys, no missing keys used in code.

## Snapshot tests
Every screen has a Textual snapshot at 80×24 and 120×40 in en and ru. Update snapshots only after reviewing the SVG diff.
