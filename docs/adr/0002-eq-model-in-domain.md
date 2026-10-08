# 0002 — EQ response model and quantizer protocol live in `domain`

Status: accepted (2026-10-08)

## Context

The magnitude-response model of the console EQ (biquads for parametric bands and shelves, analog prototypes
for the Butterworth / Bessel / Linkwitz-Riley slopes) is needed by three layers:

- `design` — to predict what a proposal does;
- `safety` — to check that no intermediate state of a write plan is louder than the start or the end (SPEC §9.3);
- `audio/fake` — to apply the emulated console EQ to simulated audio.

SPEC §5 lets each of them depend on `domain` only, and `safety` must never import `mixers`. SPEC §9.3 says the
band models come "from `design`", which would make `safety` import `design` and contradict §5.

`design` also has to snap proposed values to the grid the console can store. In the original tool it imported
the X32 parameter module directly, which the layer rule forbids.

## Decision

- `domain/eq.py` holds the data (`EqType`, `Band`, `SystemEq`) and the `EqQuantizer` protocol.
- `domain/eq_model.py` holds the response model (`band_response_db`, `eq_response_db`, `band_sos`).
- `mixers/x32/params.py` implements `EqQuantizer` for the X32/M32 grid; callers pass it into `design`.
- The model takes the sample rate as a required argument; nothing in it assumes 48 kHz.

## Consequences

- `domain` depends on numpy and scipy. It stays pure: no IO, no sockets, no clocks, no global state.
- The model is X32-shaped (its type names are the console's). A second console family with different band
  types will need the model generalised; that is out of scope for the MVP.
- Where SPEC §9.3 says "models from `design`", read "models from `domain`".
