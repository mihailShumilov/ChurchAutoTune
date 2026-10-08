#!/usr/bin/env python3
"""PreToolUse hook: keep Claude Code away from real consoles and real audio hardware.

Blocks (exit code 2, reason on stderr) any Bash command that:
  * mentions a private/LAN IPv4 address (10/8, 172.16/12, 192.168/16, 169.254/16) — real consoles live there;
  * runs `ctune` or `python -m churchtune` without CTUNE_DEV_SAFE=1 in the command;
  * talks to UDP 10023/10024 with generic tools (nc, socat, oscsend, sendosc);
  * sets CTUNE_AUDIO to anything but `fake`, or CTUNE_CONSOLE to a non-loopback host;
  * mentions an mDNS `.local` host name, or tries to switch the dev guard off
    (CTUNE_DEV_SAFE=0, `unset CTUNE_DEV_SAFE`, `env -u CTUNE_DEV_SAFE`).
Fails closed: unreadable hook input blocks the command. This hook is the second line of defence;
the first is the dev-safe guard in code (SPEC §9.1).
Loopback (127.0.0.0/8) and documentation addresses in comments of files are not affected — this hook
only inspects shell commands.
"""
from __future__ import annotations

import ipaddress
import json
import re
import sys

IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
PRIVATE_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")]
APP_RUN = re.compile(r"(^|[\s;&|(])(ctune|python3?\s+-m\s+churchtune|uv\s+run\s+(ctune|python3?\s+-m\s+churchtune))\b")
OSC_TOOLS = re.compile(r"\b(nc|ncat|netcat|socat|oscsend|sendosc|oscdump)\b.*\b1002[34]\b")
AUDIO_REAL = re.compile(r"CTUNE_AUDIO=(?!fake\b)\S+")
CONSOLE_ENV = re.compile(r"CTUNE_CONSOLE=([^\s:]+)")
MDNS = re.compile(r"\b[\w-]+\.local\b")
GUARD_OFF = re.compile(r"CTUNE_DEV_SAFE=(?!1\b)|unset\s+[^;&|]*CTUNE_DEV_SAFE|env\s+[^;&|]*-u\s*CTUNE_DEV_SAFE")


def reason(cmd: str) -> str | None:
    for m in IPV4.finditer(cmd):
        try:
            ip = ipaddress.ip_address(m.group(1))
        except ValueError:
            continue
        if any(ip in net for net in PRIVATE_NETS):
            return f"command mentions LAN address {ip} — real consoles live there; use the emulator on 127.0.0.1"
    if MDNS.search(cmd):
        return "command mentions a .local host name — consoles may answer there; use the emulator"
    if GUARD_OFF.search(cmd):
        return "switching the dev-safe guard off is not allowed"
    if OSC_TOOLS.search(cmd):
        return "raw OSC traffic to port 10023/10024 is not allowed; use the emulator and project code"
    if AUDIO_REAL.search(cmd):
        return "CTUNE_AUDIO must be 'fake' when Claude runs the app"
    m = CONSOLE_ENV.search(cmd)
    if m and not (m.group(1).startswith("127.") or m.group(1) == "localhost"):
        return f"CTUNE_CONSOLE={m.group(1)} is not loopback; use the emulator"
    if APP_RUN.search(cmd) and "CTUNE_DEV_SAFE=1" not in cmd:
        return "run the app/tests with CTUNE_DEV_SAFE=1 (refuses real consoles and real audio)"
    return None


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        print("Blocked by guard_console: unreadable hook input (fail closed).", file=sys.stderr)
        return 2
    if data.get("tool_name") != "Bash":
        return 0
    cmd = str(data.get("tool_input", {}).get("command", ""))
    why = reason(cmd)
    if why:
        print(f"Blocked by guard_console: {why}. See CLAUDE.md 'HARD SAFETY RULES'.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
