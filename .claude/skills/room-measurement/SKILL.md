---
name: room-measurement
description: Method for measuring a church hall through an X32/M32 (sweeps, mic placement, levels, quiet room, single vs multi-mic, mic set calibration) and for interpreting results (1/6-oct response, spread, RT60, sub/top junction, what EQ can and cannot fix). Grounded in the real session of 2026-10-02. Use when designing measure/, analysis/, design/ code, writing wizard instructions about mic placement, or judging a proposal.
---

> The section "Original single-hall method" below is the proven baseline from `~/sites/my/X32-church`
> (one ECM8000, 6 points, fixed routing). The section "Generalization for ChurchTune" says what changes.

# Original single-hall method
# Room measurement — method and interpretation

## Signal chain
Mac `./x32 measure` → X-USB out 1+2 → ch23/24 "PC" → **Main LR (incl. its 6-band EQ)** → speakers → room →
ECM8000 (AES50-A 10, headamp 041, +48V) → X-USB in 10 (pre-processing). One duplex CoreAudio stream =
common clock. The measured response therefore **includes the Main LR EQ active at that moment**
(stored in `measurements/<session>/session.json` as `eq_at_measurement`).

## Before measuring
- +48V ON on headamp 041; ch10 muted; ch23/24 and Main LR up (not -oo); GEQ/TEQ inserts stay OFF.
- Save a scene on the console; `./x32 backup`.
- Quiet hall: HVAC off if possible, nobody talks/walks; phones silent. `check` noise ideally < -60 dBFS.
- `./x32 level`: pink noise; set headamp 041 gain so the mic reads **-20..-12 dBFS RMS**.
  Typical acoustic level ~75–85 dB SPL — loud but comfortable. Ear protection not needed at this level.

## Mic positions (6 points)
- Height **1.2 m** (ears of a seated listener), capsule **pointed at the speakers** (ECM8000 is
  omni but on-axis toward the source is the convention), on a stand, ≥ 1 m from walls and large objects,
  not exactly on the centre line *and* symmetric (avoid identical L/R arrival everywhere).
- Spread over where people sit: `p1-center` (middle of seating), `p2-left`, `p3-right` (side blocks,
  ~1/3 in), `p4-mid` (between front and back), `p5-side` (aisle/side), `p6-back` (last rows, not under a balcony edge).
- Avoid the first row right in front of a speaker and spots < 1 m from the subwoofer.
- Operator stands still, away from the mic (≥ 2 m), not between mic and speakers.
- Same positions again for `verify` (mark the floor with tape).

## Levels and quality gates (measure.py)
- Sweep 20 Hz–20 kHz, 6 s, default **-24 dBFS** peak; hard limit -6 dBFS; 3.5 s tail for the reverb.
- `CLIP` (≥ -0.5 dBFS) = BAD → lower gain/level. Peak > -6 dBFS = warn.
- SNR (sweep RMS vs pre-roll noise) ≥ 30 dB; IR PNR ≥ 45 dB for T30 (≥ 35 for T20). Use `--avg 2` or `--level -18` if low.
- Latency is constant (same stream); it appears as the IR peak position (~tens of ms incl. acoustic path).

## Analysis conventions
- IR window 500 ms for the magnitude, 1/6-oct power smoothing, each point normalized so 500–2000 Hz = 0 dB,
  **energy** average across points, σ = spread between points.
- RT60 per octave 63 Hz–8 kHz: Schroeder backward integration with noise subtraction/truncation;
  EDT (0..-10), T20 (-5..-25), T30 (-5..-35). T30 only with ≥ 45 dB dynamic range.
- Above ~10 kHz ECM8000 (uncalibrated) is unreliable → "ориентировочно". Optional `mic_cal.txt` ("freq dB").

## Interpretation
- **RT60 500–1k**: < 0.8 s dry; 0.8–1.4 s good for mixed speech/worship; 1.4–2.0 s boomy for speech;
  > 2 s acoustic treatment needed — EQ will not fix intelligibility.
- **Bass ratio** (RT 125–250 / 500–1k) > 1.3 → bass rings longer → low-mid "mud"; broad cuts 100–300 Hz help.
- **Peaks common to all points** (modes < 200 Hz, speaker/room build-up 100–400 Hz, harshness 2–5 kHz) → cut.
- **Narrow dips** that move between points = interference/comb filtering (L/R speakers, reflections).
  Boosting them wastes headroom and makes feedback worse → never.
- Broad, consistent shortfall (e.g. -4 dB over 2 octaves in all points) → gentle boost, max +3 dB.
- Large σ (> 3–4 dB) at a frequency = position-dependent → no EQ.
- Highs falling 1–3 dB/oct above 1–2 kHz in a reverberant hall is natural; target here: flat to 1 kHz, -1 dB/oct above.
- The existing Main EQ (232 Hz -1.75, 1k97 -3.5 Q6.4) was probably set by ear (feedback/harshness);
  if the measurement shows a dip there, the proposal may relax it — check with the sound team before removing.

## What EQ cannot do
Fix reverberation, comb filtering between L/R speakers, nulls, a room mode's ringing in time, or
coverage differences between seats. Those need speaker aiming, delay/time alignment, or acoustics.

# Generalization for ChurchTune
- **Positions:** ≥ 8 in the listening area (L-Acoustics guidance; more than ~8–12 adds little). Exclude the
  front and back 15–25 % of seating depth from the average (keep them as control points), ≥ 1 m from walls
  (2–3 m when possible), height 1.2 m, irregular spacing, never exactly on the symmetry axis or the toe-in
  convergence point of the mains. Sub/top junction: 4 points spread in width and depth.
- **Single mic:** sequential positions, route ordered to minimize walking; same tape marks for verify.
- **Multi-mic (2–8):** all mics captured in one duplex stream (same clock). Rounds of K positions.
  Mandatory **set calibration** first: capsules within 10 cm, same height and direction, 3–5 m from speakers;
  two sweeps; per-mic relative response to the set median (1/3 oct), sensitivity offset, relative delay.
  Reject a mic if shape deviates > ±3 dB in 100 Hz–8 kHz, delay differs > 0.5 ms, or polarity is inverted.
  ECM8000 units vary; a generic calibration file can be worse than none — prefer the relative calibration.
- **Level:** pink-noise ramp from −50 dBFS in 2 dB steps to −20..−12 dBFS RMS at the mic; hard limit −6 dBFS
  peak at the output; optional dB(C) anchor from a phone SLM, sweep ≤ 90 dB(C) at the mic by default.
- **Noise:** check per-octave background; dimmers/LED drivers give 100/120 Hz hum + harmonics (seen in the
  real hall — turn stage lights off), HVAC gives broadband LF.
- **Quality gates:** clip = BAD; SNR ≥ 30 dB OK / 20–30 WARN; PNR ≥ 45 OK / 35–45 WARN; two-sweep repeatability
  ≤ 1 dB OK / 1–2 WARN (1/3 oct, 100 Hz–8 kHz); any xrun = BAD.
- **Occupied hall:** people add roughly 0.3 m² (125 Hz) to 0.7 m² (2–4 kHz) Sabine absorption each; an empty-hall
  tuning sounds brighter/longer than during the service. Offer an optional target correction, off by default.
- **Sub/top junction lesson (real hall):** a −9…−13 dB dip around 100 Hz at all points came from no real LPF on
  the sub (shelves used instead) + HPF and a VEQ hack on the tops. LR24 at ~100 Hz on both + sub level +8 dB fixed it;
  delay search then showed < 1 dB gain → no delay. Geometry predicted 5–10 ms, measurement disagreed — trust measurement
  (sub DSP latency). Encode: "fix filters and level first, then re-measure, then align".
- **Acceptance (verify):** mean deviation from target (60 Hz–10 kHz) improves ≥ 0.5 dB; worst point not worse > 1 dB
  in any 1/3-oct band 80 Hz–8 kHz; spread (100 Hz–4 kHz) not up > 0.5 dB.
