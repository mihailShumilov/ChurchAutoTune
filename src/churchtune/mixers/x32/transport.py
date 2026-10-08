"""The only socket in the package: UDP datagrams to and from one console.

``send_packet`` is a raw capability. Only two callers exist and a test pins that:
``client.py`` (after its read-only allowlist) and ``writer.py`` (after the write policy).

In dev-safe mode (SPEC §9.1) the transport refuses every host that is not loopback, before a
socket is created, and binds its socket to loopback. The decision is taken from the process
environment only; there is no parameter that could switch it off.

The host must be an IPv4 literal: names are never resolved here (a name could resolve differently
between two writes of one plan). Datagrams from any other sender than the console are dropped.

One owner at a time: the client and the writer share a transport, but request/reply pairs must
not interleave between threads.
"""

import errno
import ipaddress
import socket
import time
from types import TracebackType
from typing import Self

from churchtune.errors import ConsoleUnreachable
from churchtune.mixers.x32.codec import OscArg, decode
from churchtune.platform.devsafe import LOCALHOST, LOOPBACK_V4, ensure_loopback_host, is_dev_safe

X32_PORT = 10023
DEFAULT_TIMEOUT_S = 0.5
MIN_TIMEOUT_S = 0.001
MAX_DATAGRAM_BYTES = 65536
MAX_PORT = 65535
# "No route to host" on macOS and Linux: typically the Local Network permission or a VPN.
_NO_ROUTE_ERRNOS = frozenset({errno.EHOSTUNREACH, 113})


def _console_ipv4(host: str) -> str:
    """Return ``host`` as a canonical unicast IPv4 literal, or raise ``ValueError``."""
    try:
        address = ipaddress.IPv4Address(host)
    except ValueError:
        raise ValueError(f"console host must be an IPv4 address, got {host!r}") from None
    if str(address) != host or address.is_multicast or address.is_unspecified:
        raise ValueError(f"console host must be a unicast IPv4 address, got {host!r}")
    if address == ipaddress.IPv4Address("255.255.255.255"):
        raise ValueError("console host must not be the broadcast address")
    return host


class UdpTransport:
    """UDP socket for one console address."""

    def __init__(self, host: str, port: int) -> None:
        """Open a socket for ``host:port``.

        Args:
            host: Console IPv4 address, without a port (``localhost`` means 127.0.0.1).
            port: Console UDP port (the console listens on 10023).

        Raises:
            DevSafeViolation: Dev-safe mode is on and ``host`` is not loopback.
            ValueError: ``port`` is outside 1..65535 or ``host`` is not a unicast IPv4 literal.
        """
        if not 1 <= port <= MAX_PORT:
            raise ValueError(f"port must be 1..{MAX_PORT}, got {port}")
        # Connect only to what the guard returns, never to the caller's string.
        allowed = ensure_loopback_host(host)
        literal = _console_ipv4(LOOPBACK_V4 if allowed == LOCALHOST else allowed)
        self._address: tuple[str, int] = (literal, port)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if is_dev_safe():
            self._sock.bind((LOOPBACK_V4, 0))
        self._sock.settimeout(DEFAULT_TIMEOUT_S)

    @property
    def host(self) -> str:
        """Host the transport sends to."""
        return self._address[0]

    @property
    def port(self) -> int:
        """UDP port the transport sends to."""
        return self._address[1]

    def _unreachable(self, error: OSError) -> ConsoleUnreachable:
        no_route = error.errno in _NO_ROUTE_ERRNOS or "No route" in str(error)
        return ConsoleUnreachable(
            f"{error} ({self.host}:{self.port})",
            host=self.host,
            port=self.port,
            errno=error.errno,
            no_route=no_route,
        )

    def send_packet(self, packet: bytes) -> None:
        """Send one datagram to the console.

        Raises:
            ConsoleUnreachable: The network refused the packet (for example "No route to host").
        """
        try:
            self._sock.sendto(packet, self._address)
        except OSError as error:
            raise self._unreachable(error) from error

    def recv(self, timeout_s: float) -> tuple[str | None, list[OscArg]] | None:
        """Wait for one datagram from the console and decode it.

        Datagrams from any other host are dropped without ending the wait, so nothing else on
        the network can stand in for the console's answers.

        Args:
            timeout_s: How long to wait, in seconds.

        Returns:
            ``(address, args)`` as from ``codec.decode``, or None on timeout.

        Raises:
            ConsoleUnreachable: The socket reported an error other than a timeout.
        """
        deadline = time.monotonic() + max(timeout_s, MIN_TIMEOUT_S)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            self._sock.settimeout(max(left, MIN_TIMEOUT_S))
            try:
                data, sender = self._sock.recvfrom(MAX_DATAGRAM_BYTES)
            except TimeoutError:
                return None
            except OSError as error:
                raise self._unreachable(error) from error
            if sender[0] == self.host:
                return decode(data)

    def close(self) -> None:
        """Close the socket."""
        self._sock.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
