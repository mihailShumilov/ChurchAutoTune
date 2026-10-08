# Hardware checklist (run by a human at the console)

Claude Code never talks to a real console. Every OSC address or behaviour marked `# UNVERIFIED:` in code gets a row
here. A human runs the command in the hall, records the result, and only then may the driver capability for that
scope move from `suggest_only` to `full` for that model/firmware.
The small proposal files under `tests/hw/` are created in milestone M5 (each touches one parameter; `apply` does a fresh read and shows the diff first).
Capabilities per model/firmware live in `src/churchtune/mixers/x32/capabilities.py`; a human changes a row from
`suggest_only` to `full` only together with a filled row here (PR must reference the row number).

| # | Model / fw | What to verify | Command (human runs) | Expected | Result | Date / who |
|---|---|---|---|---|---|---|
| 1 | X32P 4.09 | Output delay write + readback; formula and steps of `/outputs/main/NN/delay/time` | `ctune apply tests/hw/delay_1ms.json` | 1.0 ms reads back; `ctune undo <undo-file printed by apply>` restores | | |
| 2 | X32P 4.09 | Matrix polarity write + readback | `ctune apply tests/hw/mtx3_invert.json` | invert=1 reads back, undo restores | | |
| 3 | X32P 4.09 | Channel mute write + restore (temp_mutes) | wizard S6 on the measurement channel | channel muted, restored on exit and after Ctrl+C | | |
| 4 | X32P 4.09 | Broadcast discovery | `ctune doctor` | console found without typing the IP | | |
| 5 | X32P 4.09 | Channel config/preamp parsing (names, sources, HPF) | `ctune snapshot` | names and HPF match the console screen | | |
| 6 | M32 (any) | `/xinfo` model string, USB device name | `ctune doctor` | recorded into fixtures | | |
| 7 | X32P 4.09 | Fader law and step count (1024?) on a matrix fader | `ctune apply tests/hw/mtx_fader_steps.json` | 5 values across the range read back exactly | | |
| 8 | X32P 4.09 | Bus EQ / mute / fader paths | `ctune apply tests/hw/bus_eq.json` | read back, undo restores | | |
| 9 | X32P 4.09 | Main Mono (`/main/m/*`) EQ / mute / fader | `ctune snapshot` + `ctune apply tests/hw/mono_eq.json` | read back, undo restores | | |
| 10 | X32P 4.09 | Output source enum (`/outputs/main/NN/src`) | `ctune snapshot` | sources match the console routing screen | | |
| 11 | X32P 4.09 | Sends of the USB playback channels to aux/monitor buses detected | `ctune doctor` | matches the console screen | | |
| 12 | X32P 4.09 | Ctrl+C and Space stop sound instantly in the TUI on Terminal.app and iTerm2 | wizard S8 | silence < 100 ms, mutes restored | | |
