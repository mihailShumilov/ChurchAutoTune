# Security and safety policy

ChurchTune talks to a live mixing console and plays test signals through a sound system. For this project a
**safety problem is treated like a security vulnerability**.

## What to report privately

- Any way to write to a console without the safety policy's authorization, outside a write scope, or without
  an undo file.
- Any way to read or write a console address that is not on an allowlist.
- Audio louder than −6 dBFS peak leaving the computer, a missing fade, or Ctrl+C not stopping the sound.
- Temporary mutes or other console changes that are not restored after an error or an interrupt.
- Undo or proposal files that can be crafted to write unexpected values.
- `CTUNE_DEV_SAFE=1` failing to block a real console or a real audio device.
- Classic security issues: unsafe file handling, code execution from data files, leaked personal data.

## How to report

Use GitHub's private vulnerability reporting:
[**Report a vulnerability**](https://github.com/mihailShumilov/ChurchAutoTune/security/advisories/new).

Please include the version or commit, the console model and firmware if relevant, steps to reproduce, and what
happened compared with what you expected. Please do not open a public issue for these problems.

You will get a first answer within 7 days. Once the problem is confirmed, a fix and an advisory are published
together, and you are credited unless you prefer not to be.

## Supported versions

ChurchTune is pre-alpha. Only the latest commit on `main` gets fixes until the first release.

## If something already went wrong at a console

Stop the app (Ctrl+C), then restore the console with the undo file the app wrote before its first change:
`ctune undo <undo-file>`. If that is not possible, recall the scene you saved before the session.
