"""Read-only X32/M32 client.

The client can send only messages that cannot change the console:
  * allowlisted queries without arguments (``/xinfo``, ``/main/st/eq/2/f``);
  * ``/node ,s <path>`` (human-readable read of a node);
  * ``/meters ,s /meters/N`` (meter subscription).
Everything else raises ``ReadOnlyViolation``. Some no-argument X32 addresses are actions, so
arbitrary no-argument messages are refused too. Writing is the job of ``writer.py`` alone.
"""

import logging
import re
import time
from collections.abc import Callable, Iterator, Sequence
from typing import Protocol

from churchtune.errors import ReadOnlyViolation
from churchtune.mixers.x32.codec import OscArg, encode, meter_floats, same_addr

logger = logging.getLogger(__name__)

NODE_ADDRESS = "/node"
METERS_ADDRESS = "/meters"
METERS_0 = "/meters/0"
XINFO_ADDRESS = "/xinfo"

DEFAULT_TIMEOUT_S = 0.5
PING_TIMEOUT_S = 1.5
# Meter subscriptions expire after about 10 s on the console.
METERS_RENEW_S = 8.0
METERS_POLL_S = 0.2
# One retry on UDP loss.
ATTEMPTS = 2

_QUERY_ADDRESSES = frozenset({XINFO_ADDRESS, "/info", "/status"})
_QUERY_RE = re.compile(
    r"^/main/st/eq(/on|/[1-6]/(type|f|g|q))$"
    r"|^/mtx/0[1-6]/(eq/[1-6]/(type|f|g|q)|eq/on|mix/(on|fader)|preamp/invert)$"
    r"|^/outputs/main/(0[1-9]|1[0-6])/delay/(on|time)$"
)


# A node path: plain segments only. No spaces, newlines or anything else that a console could
# take for "path + values". Reading the action/scene/recorder nodes is refused until someone
# confirms on hardware that reading them has no effect (docs/hardware-checklist.md).
_NODE_PATH_RE = re.compile(r"[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*")
MAX_NODE_PATH_CHARS = 64
# UNVERIFIED: reading these nodes is assumed harmless but was never tried on hardware.
REFUSED_NODE_ROOTS = frozenset({"-action", "-snap", "-show", "-usb", "-urec"})
_METERS_ARG_RE = re.compile(r"/meters/(\d|1[0-6])")


def is_node_path(path: object) -> bool:
    """Return True for a ``/node`` argument that is a plain, allowed node path."""
    return (
        isinstance(path, str)
        and len(path) <= MAX_NODE_PATH_CHARS
        and bool(_NODE_PATH_RE.fullmatch(path))
        and path.split("/", 1)[0] not in REFUSED_NODE_ROOTS
    )


def is_read_only_message(address: str, args: Sequence[object]) -> bool:
    """Return True only for messages that cannot change the console.

    Args:
        address: OSC address.
        args: OSC arguments.
    """
    if not args:
        return address in _QUERY_ADDRESSES or bool(_QUERY_RE.fullmatch(address))
    if len(args) != 1 or not isinstance(args[0], str):
        return False
    if address == METERS_ADDRESS:
        return bool(_METERS_ARG_RE.fullmatch(args[0]))
    return address == NODE_ADDRESS and is_node_path(args[0])


class PacketTransport(Protocol):
    """What the client needs from the transport."""

    @property
    def host(self) -> str:
        """Host the transport sends to."""
        ...

    def send_packet(self, packet: bytes) -> None:
        """Send one datagram."""
        ...

    def recv(self, timeout_s: float) -> tuple[str | None, list[OscArg]] | None:
        """Wait for one decoded datagram; None on timeout."""
        ...

    def close(self) -> None:
        """Release the transport."""
        ...


class X32Client:
    """Read-only X32/M32 client over a packet transport."""

    def __init__(
        self, transport: PacketTransport, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        """Wrap a transport.

        Args:
            transport: Where packets go; the client never touches a socket itself.
            clock: Monotonic time source in seconds, injectable for tests.
        """
        self._transport = transport
        self._clock = clock

    @property
    def host(self) -> str:
        """Host of the console this client reads."""
        return self._transport.host

    def send(self, address: str, *args: OscArg) -> None:
        """Send a read-only message.

        Raises:
            ReadOnlyViolation: The message is not on the read allowlist; nothing is sent.
        """
        if not is_read_only_message(address, args):
            raise ReadOnlyViolation(
                f"the client is read-only; refused {address} {args!r}", address=address
            )
        self._transport.send_packet(encode(address, *args))

    def ask(
        self,
        address: str,
        *args: OscArg,
        expect: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> list[OscArg] | None:
        """Send a read message and wait for the reply.

        Args:
            address: OSC address to send.
            *args: OSC arguments.
            expect: Address of the awaited reply; ``address`` when omitted.
            timeout_s: How long to wait for the reply.

        Returns:
            The reply's arguments, or None on timeout. Other packets are skipped.
        """
        expected = expect or address
        self.send(address, *args)
        deadline = self._clock() + timeout_s
        while True:
            left = deadline - self._clock()
            if left <= 0:
                return None
            reply = self._transport.recv(left)
            if reply is None:
                return None
            reply_address, values = reply
            if same_addr(reply_address, expected):
                return values

    def ping(self, timeout_s: float = PING_TIMEOUT_S) -> list[OscArg] | None:
        """Ask ``/xinfo``; returns ``[ip, name, model, firmware]`` or None when nothing answers."""
        return self.ask(XINFO_ADDRESS, timeout_s=timeout_s)

    def node(self, path: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> str | None:
        """Read one node as text.

        ``node("main/st/eq/2")`` gives ``/main/st/eq/2 PEQ 232.3 -1.75 3.9``. The console replies
        with the address ``node`` (no leading slash); a reply for another path is ignored.

        Returns:
            The reply line without the trailing newline, or None after one retry.
        """
        path = path.lstrip("/")
        for attempt in range(1, ATTEMPTS + 1):
            values = self.ask(NODE_ADDRESS, path, expect="node", timeout_s=timeout_s)
            if values and isinstance(values[0], str):
                line = values[0].strip()
                if line.lstrip("/").split(" ")[0] == path:
                    return line
            logger.warning("no reply to /node %s (attempt %d of %d)", path, attempt, ATTEMPTS)
        return None

    def query(self, path: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> OscArg | None:
        """Query one parameter without arguments.

        Returns:
            The first argument of the reply (normalized float or int), or None after one retry.
        """
        address = "/" + path.lstrip("/")
        for attempt in range(1, ATTEMPTS + 1):
            values = self.ask(address, timeout_s=timeout_s)
            if values:
                return values[0]
            logger.warning("no reply to %s (attempt %d of %d)", address, attempt, ATTEMPTS)
        return None

    def iter_meters0(self, duration_s: float) -> Iterator[list[float]]:
        """Yield ``/meters/0`` frames for ``duration_s`` seconds, renewing the subscription.

        The layout is 32 channels, 8 aux, 8 fx returns, 16 buses, 6 matrices (70 linear values).
        """
        start = last_renew = self._clock()
        self.send(METERS_ADDRESS, METERS_0)
        while self._clock() - start < duration_s:
            if self._clock() - last_renew > METERS_RENEW_S:
                self.send(METERS_ADDRESS, METERS_0)
                last_renew = self._clock()
            reply = self._transport.recv(METERS_POLL_S)
            if reply is None:
                continue
            address, values = reply
            if same_addr(address, METERS_0) and values and isinstance(values[0], bytes):
                yield meter_floats(values[0])

    def meters0(self, duration_s: float, on_frame: Callable[[list[float]], None]) -> None:
        """Receive ``/meters/0`` frames for ``duration_s`` seconds.

        Args:
            duration_s: How long to listen.
            on_frame: Called with the linear levels of each frame.
        """
        for frame in self.iter_meters0(duration_s):
            on_frame(frame)

    def close(self) -> None:
        """Close the underlying transport."""
        self._transport.close()
