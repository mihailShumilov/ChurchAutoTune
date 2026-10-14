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
   aborts the stream like Ctrl+C, the simulator between 1024-frame blocks (≤ 21 ms). Tests prove
   silence within 50 ms.
3. **Shared types in `domain`.** `StopFlag` and `MultiRecording` live in `domain/recording.py` and
   are re-exported by `audio.base`, because `measure` may not import `audio` (layer rule).
4. **Mute switching is injected.** `measure.junction.measure_junction_round` takes a `MuteSwitch`
   callable and restores the original mutes in `finally`. The formal `temp_mutes` write scope, its
   pending-restore file and readback arrive with M5; until then the caller (the M0 sub flow, later
   the wizard) supplies the switch. `measure` never touches the console.

Also: a stream dropout (xrun) now makes a point BAD, as SPEC §8.2 says (the original tool only
warned); the repeatability gate of two sweeps (≤ 1 dB OK, ≤ 2 dB WARN, else BAD, 1/3 octave,
100 Hz–8 kHz) is new.

## Consequences

- The simulator `audio/fake/hall.py` models several microphones (sensitivity, delay, polarity,
  shape, noise), mutes switched per block and scratches; `--audio fake` gives a four-mic rig.
- The wizard (M3) can drive every step through `MultiChannelBackend` and a stop flag tied to its
  keys.
