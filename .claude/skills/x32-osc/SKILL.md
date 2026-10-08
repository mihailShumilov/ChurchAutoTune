---
name: x32-osc
description: Behringer X32 / Midas M32 OSC protocol reference — addresses, message formats, /node replies, meters, normalized 0..1 formulas for EQ/fader/delay, quantization, discovery, routing ids, and known gotchas, cross-checked against real dumps. Use when writing or debugging any code under src/churchtune/mixers/x32/ or the emulator.
---


# X32 OSC (fw 4.09, X32 Producer @ 192.168.0.80:10023)

Source of truth for formulas: Patrick-Gilles Maillot, "Unofficial X32/M32 OSC Remote Protocol".
Everything below was cross-checked against the real dump `tests/fixtures/x32_dump_2026-09-29.txt`.

## Transport
- UDP, port 10023. The console replies to the sender's address/port.
- OSC: address (padded to 4), type tags `,sif b` (padded), big-endian args. Blob = BE int32 size + data, padded.
- Project rule: all socket code lives in `src/churchtune/mixers/x32/client.py`; writes only via `src/churchtune/mixers/x32/writer.py`.

## Read-only messages (what X32Client may send)
| message | reply |
|---|---|
| `/xinfo` | `/xinfo ,ssss ip name model fw` (e.g. `192.168.0.80 X32P-04-7C-DF X32P 4.09`) |
| `/info`, `/status` | version / state strings |
| `/node ,s main/st/eq/2` | address **`node`** (NO leading slash!), `,s` = `"/main/st/eq/2 PEQ 232.3 -1.75 3.9\n"` |
| `/main/st/eq/2/f` (no args) | same address, `,f` normalized value |
| `/main/st/eq/2/type` (no args) | `,i` enum |
| `/meters ,s /meters/0` | stream of `/meters/0 ,b` for ~10 s |

**Gotcha 1:** compare reply addresses with `a.lstrip("/") == b.lstrip("/")` — `/node` replies come as `node`.
**Gotcha 2:** `/node` strings are human-readable and *formatted*, not raw: freq `232.3` / `1k97` / `16k25`
(>= 1 kHz truncated to 10 Hz), gain `+0.00`, `-1.75`, but `-15.0`/`-14.7` when |g| >= 10 (truncated),
Q `3.9`. Parse with `mixers.x32.params.parse_freq` (`"1k97"` -> 1970).
**Gotcha 3:** some no-arg addresses are actions — never send arbitrary no-arg messages; the client whitelists.

## Meters
- Subscribe: `/meters ,s /meters/0`; renew every < 10 s (we renew every 8 s).
- Reply blob: **little-endian** int32 count, then count **little-endian** float32, linear (1.0 = 0 dBFS).
- `/meters/0` = 32 ch, 8 aux, 8 fxrtn, 16 bus, 6 mtx (70 values). ch10 = index 9.

## Writing (only `mixers/x32/writer.py` with a `WriteAuthorization`; scopes per SPEC §9.2 and the safe-writes skill)
Normalized floats 0..1, then the console quantizes. Always pre-quantize and read back.

| param | address | formula | steps |
|---|---|---|---|
| freq | `/main/st/eq/N/f` | f = 20 · 1000^x  (20 Hz..20 kHz, log) | 201 (x = i/200) |
| gain | `/main/st/eq/N/g` | g = 30x − 15 ; x = (g+15)/30 | 121 (0.25 dB) |
| Q | `/main/st/eq/N/q` | Q = 10 · 0.03^x (10..0.3, log) | 72 (x = i/71) |
| type | `/main/st/eq/N/type` | int | 0 LCut, 1 LShv, 2 PEQ, 3 VEQ, 4 HShv, 5 HCut, 6 BU6, 7 BU12, 8 BS12, 9 LR12, 10 BU18, 11 BU24, 12 BS24, 13 LR24 |
| on | `/main/st/eq/on` | int 0/1 | |

Checks against the dump: 232.3 = i 71, 1k97 = i 133, 7k09 = i 170, 16k25 = i 194, 23.0 = i 4;
Q 3.9 = i 19, 2.0 = i 33. Main LR band 1 is `BU18 23.0` — a cut filter; types 6..13 are real here.

Write order per band in this project: type, f, q, g (gain last). After each write:
query the param (`,f`/`,i`) and `/node main/st/eq/N`, compare step indices, show the node string.

## Useful read paths
`-prefs` (contains `48K INT`), `headamp/041` (`+0.0 OFF` = gain, phantom), `ch/10/mix` (`OFF ...` = muted),
`ch/23/mix`, `main/st/mix` (`ON -6.1 ...`; `-oo` = fader down), `main/st/insert`, `config/routing/CARD`,
`config/userrout/in` (32 source ids: 33..80 = AES50-A 1..48, 129..160 = Card 1..32).

## Emulator
`tests/emulator/x32_emulator.py` implements all of the above on 127.0.0.1 (random port), logs every write in `emu.writes`.

## ChurchTune extensions — status of every address beyond Main LR EQ
Legend: **VERIFIED** = seen in real dumps / written and read back on fw 4.09 in the 2026-10-02 session
(`~/sites/my/X32-church` sub scope); **UNVERIFIED** = from Maillot's document only — mark code with
`# UNVERIFIED:` and add a line to `docs/hardware-checklist.md` until a human confirms it on hardware.

| purpose | address | status |
|---|---|---|
| matrix EQ band | `/mtx/0N/eq/B/(type,f,g,q)` N=1..6, B=1..6, same formulas as Main; LR24 accepted on matrices | write+readback VERIFIED (Mtx1-3) |
| matrix mute | `/mtx/0N/mix/on` (1 = ON/unmuted, 0 = muted) | write+restore VERIFIED (sub measure) |
| matrix fader | `/mtx/0N/mix/fader` normalized, X32 fader law (see below) | write+readback VERIFIED (Mtx3 −37.5 → −29.3) |
| matrix polarity | `/mtx/0N/preamp/invert` int 0/1 | read VERIFIED, write UNVERIFIED (never written on hardware) |
| output delay | `/outputs/main/NN/delay/on`, `/outputs/main/NN/delay/time` (0.3..500 ms) | read VERIFIED, write UNVERIFIED (no delay was applied in the session) |
| output source | `/outputs/main/NN/src` (enum, see Maillot) | UNVERIFIED mapping table |
| bus EQ / mute / fader | `/bus/NN/eq/B/...`, `/bus/NN/mix/on`, `/bus/NN/mix/fader` | UNVERIFIED |
| main mono | `/main/m/...` | UNVERIFIED |
| channel mute (temp_mutes) | `/ch/NN/mix/on` | read VERIFIED (`ch/10/mix`), write UNVERIFIED |
| channel name / source | `/ch/NN/config` (`name icon color source`) | UNVERIFIED parse |
| channel HPF / EQ (audit, read-only) | `/ch/NN/preamp` (`trim invert hpon hpslope hpf`), `/ch/NN/eq/B` | UNVERIFIED parse |
| matrix sends from main | `/main/st/mix/MM/...` sends to matrices | UNVERIFIED |
| USB card routing | `/config/routing/CARD` blocks, `/config/userrout/in` ids | VERIFIED (see above) |

**Fader law (X32 normalized → dB)** — from Maillot, piecewise linear:
x ≥ 0.5: dB = 40x − 30; 0.25 ≤ x < 0.5: dB = 80x − 50; 0.0625 ≤ x < 0.25: dB = 160x − 70; x < 0.0625: dB = 480x − 90 (x = 0 → −∞).
1024 steps per Maillot (UNVERIFIED count). Verified points from the session: −37.5 and −29.3 dB read back correctly. Always pre-quantize and read back.

**Delay time** normalized: time_ms = 0.3 + x · 499.7 (UNVERIFIED formula and step count — check Maillot, then read back and compare ms with ±0.05 tolerance).

## Discovery and models
- Discovery: send `/xinfo` (no args) as UDP broadcast to port 10023 on each IPv4 interface; consoles reply
  unicast `/xinfo ,ssss ip name model fw`. UNVERIFIED that every model answers broadcast — fall back to the
  last known IP and to manual entry.
- Model strings (4th arg): X32 family `X32`, `X32C`, `X32P`, `X32RACK`, `X32CORE`; M32 family `M32`, `M32R`, `M32C`
  (UNVERIFIED except `X32P` — record the real string in fixtures whenever a user runs `ctune doctor`).
- X32 and M32 share the OSC protocol and port. Differences that matter here are local I/O counts and the
  USB card (X-USB / X-LIVE vs DN32-USB / DN32-LIVE); routing ids are the same.

## Subscriptions
`/xremote` keeps the console pushing changes for 10 s (renew every < 10 s). Use it during temp_mutes to detect
someone changing the console by hand. Meters and xremote renewals share one renew timer.
