# 0007 — M2 measurement engine: multichannel backends, the stop flag, injected mute switching

Status: accepted (2026-10-14)

## Context

M2 (SPEC §15) adds several microphones in one stream, mic-set calibration, auto-mapping, the level
ramp, background noise, the path check and the sub/top junction with temporary mutes. Four choices
needed a record.

## Decision

1. **Multichannel contract.** `audio.base.MultiChannelBackend` records any set of 1-based inputs in
   the playback stream (`playrec_multi`, `record_multi`, `run_stream_multi`). The one-microphone
   methods of M0 remain and are now thin wrappers in the real backend, so the expert CLI is
   unchanged. Inputs are validated (`check_inputs`) before a stream opens.
2. **Stop flag.** In the TUI, Ctrl+C and Space are keys, not SIGINT (SPEC §9.4). Every multichannel
   method takes a `StopFlag` (`is_set()`); the real backend checks it on every poll (≤ 20 ms) and
   aborts the stream like Ctrl+C, the simulator between 1024-frame blocks (≤ 21 ms). The level
   ramp also fades its noise out within 10 ms of a stop or a clipping alarm, checked per block.
   Tests prove silence within 50 ms, measured from the moment of the stop.
3. **Shared types in `domain`.** `StopFlag` and `MultiRecording` live in `domain/recording.py` and
   are re-exported by `audio.base`, because `measure` may not import `audio` (layer rule).
4. **Mute switching is injected.** `measure.junction.measure_junction_round` and
   `measure.speakers.measure_speakers_one_by_one` take a switch callable, validate their inputs
   before the first switch and restore the original mutes in `finally`. The formal `temp_mutes`
   write scope, its pending-restore file and readback arrive with M5; until then the wizard (M3)
   supplies the switch. `measure` never touches the console.
5. **One backend protocol in `measure`** (`measure/backend.py`), satisfied by
   `audio.base.MultiChannelBackend`. Stream callbacks stay light: blocks are copied in the audio
   thread and every decision runs in `on_tick` in the calling thread.
6. **Calibration method** (dsp-reviewer): delay and polarity from a band-limited cross-correlation
   of the direct sound with the set's median response (sub-sample); a polarity split without a clear
   majority blames nobody; the shape uses a 10 ms window above 500 Hz and is faded out outside
   100 Hz–8 kHz; noise is compared after the sensitivity; bad sweeps are refused; with two
   microphones the pair is judged as a whole.

Also: a stream dropout (xrun) now makes a point BAD, as SPEC §8.2 says (the original tool only
warned); the repeatability gate of two sweeps (≤ 1 dB OK, ≤ 2 dB WARN, else BAD, 1/3 octave,
100 Hz–8 kHz) is new.

## Status of the engine

M2 delivers the engine; the wizard (M3) wires it into steps S6–S10. Deferred, with the milestone:

- channel checks of a found microphone (phantom, gain, open paths) shown as steps: M3 (S6);
- the path check through the console meters (channel → main → outputs): M3 (S7);
- the §8.2 gate "latency jump > 2 ms within a round without a geometric reason": M4, when the point
  plan gives positions;
- the transitional CLI still knows the simulated rig (`FakeHall.scratch_next`, `place_all`) to
  stand in for a person; the wizard drives the simulator from its scripted prompter instead.

## Consequences

- The simulator `audio/fake/hall.py` models several microphones (sensitivity, fractional delay,
  polarity, shape, noise scaled by sensitivity), two-way tops, capsules a few centimetres apart
  with independent late reverberation at the calibration point, mutes switched per block and
  scratches; `--audio fake` gives a four-mic rig.
- The wizard (M3) can drive every step through `MultiChannelBackend` and a stop flag tied to its
  keys.
