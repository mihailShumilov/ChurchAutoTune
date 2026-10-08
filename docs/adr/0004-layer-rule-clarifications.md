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
