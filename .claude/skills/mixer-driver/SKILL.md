---
name: mixer-driver
description: Contract for ChurchTune console drivers (MixerDriver protocol, ConsoleSnapshot, topology inference, USB audio map, meters, scoped writers, emulator and fixtures) and the procedure for extending the X32/M32 driver or adding a new console family later. Use when working in src/churchtune/mixers/ or tests/emulator/.
---

# Mixer drivers

## Contract (`src/churchtune/mixers/base.py`)
```python
class MixerDriver(Protocol):
    @classmethod
    def discover(cls, timeout_s: float) -> list[DiscoveredConsole]: ...
    model: ConsoleModel            # family, model string, firmware, Capabilities
    def read_snapshot(self, progress: ProgressCb) -> ConsoleSnapshot: ...      # read-only, ≤ 20 s
    def infer_topology(self, snap: ConsoleSnapshot) -> TopologyGuess: ...       # pure, uses snapshot only
    def usb_audio_map(self, snap: ConsoleSnapshot) -> UsbAudioMap: ...          # pure
    def meters(self, which: MeterSet) -> Iterator[MeterFrame]: ...
    def writer(self, scope: WriteScope, session: Session) -> ScopedWriter: ...  # delegated to safety
```
- `Capabilities` declares what this model/firmware supports per scope: `full` (verified write+readback),
  `suggest_only` (addresses UNVERIFIED → the wizard shows instructions instead of writing), `none`.
- `ConsoleSnapshot` is console-neutral (inputs, buses, matrices, mains, outputs, USB routing, clock, model)
  plus `raw: dict[str, str]` of original lines for undo/debugging. Its JSON is versioned.

## Topology inference
- Input: snapshot only. Output: per physical output → path (main L/R/mono, matrix N, bus N) → role guess
  (`top_l`, `top_r`, `top_mono`, `sub`, `fill`, `delay`, `monitor`, `unused`) with confidence 0..1 and reasons.
- Evidence, in order: output source routing; sends into matrices; existing HPF/LPF on the path; names
  (multilingual dictionary in `mixers/x32/names.py`, lowercase, transliteration-insensitive); fader position (−∞ → unused?).
- Never decide silently: the wizard always shows the guess for confirmation.

## Tests every driver must have
1. Codec round-trip and parsing of real `/node` lines (fixtures from dumps).
2. Snapshot from emulator == snapshot from fixture parsing.
3. Topology on each fixture in `tests/fixtures/<family>/topologies/*.yaml` (expected roles) — add one per real dump users share.
4. Writer: every allowed path accepted, neighbours rejected; readback mismatch raises; emulator quantization equals real.
5. Meters: blob decoding (BE size, LE count + LE floats for X32), renew timing with an injected clock.

## Emulator (`tests/emulator/`)
- Seeds state from a real dump fixture; replies like the real console (address quirks included).
- Supports model variants, injected faults (drop rate, latency, readback drift, disconnect), and a hook that the
  fake audio backend uses to apply current EQ/mute/fader/delay/polarity of the paths to the simulated room.
- Logs every write; tests assert on the log.

## Adding a console family later (not in MVP)
New package `mixers/<family>/`, its own skill with verified protocol notes, emulator, fixtures from real dumps,
`Capabilities` starting at `suggest_only` until a human verifies writes on hardware (hardware-checklist).
Known constraints from research: X Air (official OSC, port 10024, similar paths); WING (OSC 2223 + native protocol);
A&H Qu (MIDI over TCP 51325 incl. PEQ/GEQ/delay); Yamaha DM3 official OSC lacks EQ/delay → suggest-only.
