# 0014 — M6: the session report

Status: accepted (2026-10-15)

## Context

SPEC §10 asks for a report of every session: a self-contained HTML file (CSS and charts inside,
opens without internet, light and dark theme, prints), the same in Markdown, machine-readable
JSON, and a 4–6 KB text for a chat (`--for-chat`), in the interface language, with twelve
sections from the summary to the technical appendix. The report code that came with the original
tool (`report/markdown.py`, `report/plots.py`) writes Russian literals for the transitional CLI.
The layer rule (ADR 0004) lets `report/` import only `domain`, `analysis` and `design`: no
storage, no catalogues.

## Decision

1. **A language-neutral document.** `report/model.py` holds `ReportData` (the facts of a session)
   and a `Document` of blocks (paragraph, bullets, table, figure) whose texts are message keys
   (`Msg`); `report/sections.py` builds the twelve sections of §10 (`SECTION_IDS`). Numbers go
   through value messages (`report-value-db`, …), so the language also decides the decimal
   separator.
2. **Rendering with an injected translate function.** `report/html.py`, `report/session_md.py`
   and `report/chat.py` take `Translate`; charts (`report/figures.py`) are drawn with labels in
   the same language. The texts live in `i18n/locales/{en,ru,uk}/report.ftl`; the wizard and the
   CLI pass a `Translator`. `report/session_json.py` writes the facts without language (curves
   in thirds of an octave).
3. **HTML**: one file, styles inside, PNG charts as data URIs, `prefers-color-scheme` for the
   dark theme (charts stay on white cards), print styles, every text escaped, no scripts.
4. **The wizard writes it at S14** (`wizard/reporting.py`): the data comes from the session's
   own files (points, junction, proposal, undo, verify), so `ctune report [SESSION] [--lang]
   [--for-chat]` makes it again later without a console. `Services.translate` carries the
   language; without it (tests) no report is written. A failure to write it does not stop the
   wizard; the person is told to run `ctune report`.
5. The findings of S11 moved to `report/findings.py` and are shared by the wizard and the report.
   A hump is now measured against the target curve and only where the places agree; its wording
   depends on the frequency (boomy, boxy, sharp).
6. **The person's words, not the console's.** Writes are listed as the confirmation screen lists
   them ("Main LR, band 3: frequency · 232 Hz · 143 Hz"), one row per parameter (first old value,
   last new value; a parameter that ends where it began is left out): the console's write port
   describes an undo file read-only (`describe_undo`). Places are named as the wizard named them;
   the date is in words of the language; delay and polarity at the junction say which speaker to
   delay and that they are advice, never written.
7. **Interpretation within what was measured.** RT60 for speech and music, EDT against T20 in the
   mids (below 0.8: strong direct sound; above 1.1: mostly reflections — a heuristic), the bass
   ratio (above 1.3), and the limits: an empty hall, no STI, 63 Hz approximate. Room advice that
   the measurement cannot support is marked as general advice.
8. **The checklist follows the wizard's order**: what was changed by hand and the microphones
   first, the scene only after the mutes and sends are back, then the service, the undo command,
   and the channels only when there is channel advice. After a worse check that was undone, the
   summary says the console is as it was.
9. The transitional CLI's Russian `analyze`/`export-for-chat` output stays as it is (ADR 0003).

## Consequences

- `session/report/` holds `report.html`, `report.md` with its PNG charts, `report.json` and
  `for-chat.txt`.
- A structure snapshot of the HTML (`tests/unit/report/report_structure.txt`) and the demo's
  end-to-end run (report written at S14, made again by `ctune report`) guard the format.
- Open: `ctune report` does not know the sweep of the run (the wizard does not store it), so its
  appendix leaves the sweep length and range out; the report's order follows SPEC §10, with the
  summary pointing to "What to do next".
