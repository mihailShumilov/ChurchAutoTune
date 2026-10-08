"""Dev-safe guard: keeps development and tests away from real consoles and real audio (SPEC §9.1).

Dev-safe mode is on when ``CLAUDECODE`` is present at all (set in shells driven by a coding agent),
or when ``CTUNE_DEV_SAFE`` is set to anything but an empty string or exactly ``0``. Setting
``CTUNE_DEV_SAFE`` to ``0`` does not switch the mode off while ``CLAUDECODE`` is present.

In dev-safe mode the console transport refuses non-loopback hosts and the audio factory refuses
real devices. This module only decides; the refusals live next to the socket and the audio stream.
"""

import ipaddress
import os
from collections.abc import Mapping

from churchtune.errors import DevSafeViolation

DEV_SAFE_VAR = "CTUNE_DEV_SAFE"
AGENT_VAR = "CLAUDECODE"
LOOPBACK_V4 = "127.0.0.1"
LOOPBACK_V6 = "::1"
LOCALHOST = "localhost"
# No stripping: " 0" or "0\n" keep the mode on.
_OFF_VALUES = frozenset({"", "0"})


def is_dev_safe(environ: Mapping[str, str] | None = None) -> bool:
    """Return True when dev-safe mode is on.

    Args:
        environ: Environment to inspect; the process environment when omitted.

    Returns:
        True if real consoles and real audio devices must be refused.
    """
    env = os.environ if environ is None else environ
    if AGENT_VAR in env:
        return True
    return env.get(DEV_SAFE_VAR, "") not in _OFF_VALUES


def is_loopback_host(host: object) -> bool:
    """Return True only for ``localhost``, ``::1`` or a canonical IPv4 literal in 127.0.0.0/8.

    Host names are never resolved: a lookup could itself reach the network, and a name can point
    at a console. Everything else is not loopback: shorthand (``127.1``), integers and packed
    bytes, padded strings, ``host:port``, IPv6 zone ids (``::1%en0``), IPv4-mapped IPv6 and
    non-canonical spellings of a loopback address.

    Args:
        host: Host part of a console address, without a port.
    """
    if not isinstance(host, str):
        return False
    if host in (LOCALHOST, LOOPBACK_V6):
        return True
    try:
        address = ipaddress.IPv4Address(host)
    except ValueError:
        return False
    return address.is_loopback and str(address) == host


def ensure_loopback_host(host: str, environ: Mapping[str, str] | None = None) -> str:
    """Return the host to hand to the socket, refusing non-loopback hosts in dev-safe mode.

    Callers must connect to the returned value, not to their own copy of ``host``: in dev-safe
    mode ``localhost`` is replaced by ``127.0.0.1`` so the system resolver is never asked.

    Args:
        host: Host part of a console address, without a port.
        environ: Environment to inspect; the process environment when omitted.

    Returns:
        The address to connect to. Outside dev-safe mode this is ``host`` unchanged.

    Raises:
        DevSafeViolation: Dev-safe mode is on and ``host`` is not loopback.
    """
    if not is_dev_safe(environ):
        return host
    if not is_loopback_host(host):
        raise DevSafeViolation(
            f"dev-safe mode refuses console host {host!r}; only loopback (the emulator) is allowed",
            host=host,
        )
    return LOOPBACK_V4 if host == LOCALHOST else host
