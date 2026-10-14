# 0004 — Clarifications of the layer rule

Status: accepted (2026-10-08)

## Context

SPEC §5 gives the dependency rule between layers but leaves a few edges open that the ported code needs.

## Decision

`tests/unit/test_architecture.py` is the executable form of the rule. On top of the SPEC table:

- `platform` is a leaf that holds the dev-safe guard. `mixers` and `audio` import it, so each refuses real
  consoles and real audio devices next to its own socket or stream.
- `measure`, `design` and `report` may import `analysis` (shared response math and the analysis result);
  `analysis` imports none of them. `report` may also import `design` (it renders proposals).
- `storage` imports `domain` only. Mapping between stored models and the objects of `design`, `measure` and
  `analysis` is done by the caller (`cli/records.py` today, the wizard later).
- `safety` needs the console's value encoding to judge writes in real units. It defines the `ValueLaws`
  protocol; `mixers/x32/laws.py` implements it. `safety` never imports `mixers`.
- Only `mixers/x32/transport.py` imports `socket`; only `audio/sounddevice_backend.py` imports `sounddevice`.
- `mixers/x32/writer.py` is imported lazily, inside functions of `cli` (later `wizard`) only.

## Consequences

Adding an edge means changing the table in the test, with a reason, in the same change.

## Deferred after the M0 review

Left as they are so the port stays line-for-line comparable with the original; to be paid in the milestone
that next touches the module:

- `app` may import `domain` (the checker already allows it; SPEC §5 does not list it).
- Functions longer than the ~50 lines of CODING_STANDARDS §2: `safety/plan.py::plan_changes`,
  `safety/sub_plan.py::alignment_changes` (first, in M5, with a `safety-reviewer` pass),
  `report/markdown.py::session_report_md` and `export_for_chat`, `measure/gates.py::assess`,
  `design/alignment.py::analyze_alignment`, `design/system_eq_opt.py::optimize_design`; module
  `design/system_eq.py` (about 500 lines).
- Names without units in `mixers/x32/params.py` (`norm_to_freq`, `norm_to_gain`, ...), kept from the original.
- `NewType` identifiers (`MicId`, `PointId`, `OutputId`) arrive with the venue model in M1/M2.
- `transport.py` reads the clock directly; `client.py` and `writer.py` take it as an argument.
