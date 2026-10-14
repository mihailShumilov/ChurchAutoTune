# 0008 — M3 wizard: a UI-agnostic engine, a serializable prompt protocol, Textual and Fluent

Status: accepted (2026-10-14)

## Context

M3 (SPEC §15) adds the guided wizard for volunteers: steps, the Textual app, saving and resume,
and catalogues in en/ru/uk. A cross-platform UI is a long-term goal, so how the engine talks to a
screen had to be decided now. Five choices needed a record.

## Decision

1. **The engine knows no UI.** `wizard/engine.py` runs `Step` objects over a `StateBox` and talks
   only to a `Prompter` (`header`, `ask`, `progress`, `meter`). The steps never import `app`,
   `i18n` or `cli`; the layer rule (ADR 0004) is extended accordingly: `wizard` may import
   `domain`, `mixers.base`, `audio.base`, `measure`, `analysis`, `design`, `storage`, `report`
   (not `safety` until the write steps of M5); `app` may import `wizard`, `i18n`, `domain`.
   A step ends with done, back, retry (run the same step again) or quit; an error of the
   project's own kind is explained in the person's language with "try again" or "save and quit".
2. **Prompts are data.** A `Prompt` holds a `Kind` (info, confirm, choose, input, task, typed),
   message keys with arguments (`Text`), options, a table and an ASCII picture; an `Answer` holds a
   value or a navigation (`back`, `quit`). Both serialize with `to_dict`, so a future UI in another
   process or language can drive the same engine. Numbers stay numbers until the UI formats them;
   an argument may itself be a `Text` (a console word such as "matrix 3", translated by the UI) or
   a list of them.
3. **Three interfaces over one engine.** The Textual app (`app/`) runs the engine in a worker
   thread and bridges prompts through a queue; the line interface (`cli/wizard.py`, `--plain`, no
   TTY, screen readers) prints and reads lines; the E2E tests use a scripted prompter. Leaving the
   Textual app silences the sound and answers the waiting question "save and quit", so the engine
   never blocks.
4. **Ctrl+C and Space are keys in the app.** Both set the services' stop flag (ADR 0007) on every
   screen; Space types into a text field instead. Esc goes back where the screen allows it, Ctrl+Q
   saves and quits (at the next question when pressed while the wizard is busy). Key hints are
   translated and drawn without colour; there is no English footer. In the line interface Ctrl+C
   is SIGINT: the stream aborts, the engine saves and quits.
   The stop flag is cleared before each step and after "Play the test sound", never at other
   times, and never once the UI has gone (`Services.shutdown` is set before the flag). Recordings
   that play nothing (mic mapping, background noise) stop only on `shutdown`, not on Space.
   A guard error (dev-safe, read-only) offers only "save and quit"; a damaged stored file is
   explained instead of crashing. Each session keeps a copy of the mic calibration it used.
5. **Fluent catalogues.** `i18n/locales/{en,ru,uk}/*.ftl` loaded with `fluent.runtime`
   (`use_isolating=False`); plural categories one/few/many, a decimal comma in ru/uk. A test keeps
   keys and variables identical across languages and checks that every key used in code exists and
   no key is unused. A missing key renders visibly as the key itself.

New dependencies: `textual`, `fluent-runtime`; dev:
`pytest-asyncio`, `pytest-textual-snapshot` (Pilot tests and SVG snapshots at 80×24 and 120×40,
en and ru).

## Consequences

- The M3 wizard is read-only on the console: it reads, measures and explains, and it asks the
  volunteer to make the few console changes by hand (mute the measurement channels, close monitor
  sends). The steps that switch mutes themselves — the speaker-by-speaker check (S7) and the
  sub/top junction (S10) — need the formal `temp_mutes` write scope with `pending_restore.json`,
  restore in `finally` and a diff-confirmed restore on the next run (SPEC §9.2). They arrive with the
  other write scopes in M5. This supersedes the remark in ADR 0007 that the M3 wizard supplies the
  mute switch.
- Every sound has its warning screen ("Play the test sound" / "Not yet"); the sweep rounds of the
  calibration and the measurement also count down three seconds. Before the first sound of a run
  the console is read again, and no sound plays while a channel that listens to a measurement
  microphone is unmuted and reaches Main, Mono or a send (feedback).
- The feedback check sees channels that listen to a microphone input (Main, Mono, sends). A
  microphone source routed straight to a physical output or a direct out, bypassing the channel,
  is not checked yet; M5 adds it with the routing reads it needs.
- The wizard state is saved after every step (`session.json` in the session folder) and `ctune
  resume` continues from the last finished step; Back never crosses a step that changed the
  console.
- The expert CLI stays as it is (ADR 0003); plain `ctune` now starts the wizard; `ctune wizard
  --plain --lang ru` chooses the line interface and the language.
