"""THE ONLY MODULE THAT MAY WRITE TO THE CONSOLE.

``ScopedWriter`` sends exactly the plan of one ``WriteAuthorization`` (issued by
``safety.authorize``) and nothing else:
  * the undo file of the plan must be on disk before the first write;
  * every change is validated against the policy again right before it is sent;
  * every write is read back (plain query -> normalized value, ``/node`` -> readable line) and
    compared by grid step; the first mismatch or error stops the plan, and what was already
    sent is always reported;
  * a writer sends its plan once.

Import this module lazily, inside the apply / undo / restore code paths only.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from churchtune.domain.changes import Change, WireValue
from churchtune.errors import ChurchTuneError, UnsafeWrite
from churchtune.mixers.x32.client import is_read_only_message
from churchtune.mixers.x32.codec import OscArg, encode
from churchtune.safety.authorize import WriteAuthorization, verify
from churchtune.safety.plan import same_step

logger = logging.getLogger(__name__)

# The console needs a moment before a query returns the new value.
SETTLE_S = 0.03


class PacketSender(Protocol):
    """The raw send capability of the transport."""

    def send_packet(self, packet: bytes) -> None:
        """Send one datagram."""
        ...


class Reader(Protocol):
    """Read-only access used for readback (the read-only client)."""

    def query(self, path: str) -> OscArg | None:
        """Query one parameter; None when the console does not answer."""
        ...

    def node(self, path: str) -> str | None:
        """Read one node line; None when the console does not answer."""
        ...


@dataclass(frozen=True, slots=True)
class WriteResult:
    """Outcome of one write.

    Attributes:
        path: Parameter address.
        wanted: Value of the plan.
        readback: Value the console returned for a plain query, or None without an answer.
        node: The readable ``/node`` line of the parameter's parent, or None.
        ok: True when the write was sent and read back on the same grid step as ``wanted``.
        sent: False when the write was refused right before sending (nothing went out for it).
        error: Why the plan stopped here, when it stopped because of an error.
    """

    path: str
    wanted: WireValue
    readback: OscArg | None
    node: str | None
    ok: bool
    sent: bool = True
    error: str | None = None


class ScopedWriter:
    """Writes one authorized plan, once, reading every parameter back."""

    def __init__(
        self,
        sender: PacketSender,
        reader: Reader,
        auth: WriteAuthorization,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Bind the writer to one authorization.

        Args:
            sender: The transport's raw send capability.
            reader: Read-only client used for readback.
            auth: Authorization issued by ``safety.authorize``.
            sleep: Waits the given seconds between a write and its readback; injectable.

        Raises:
            UnsafeWrite: ``auth`` is not an untouched authorization from ``safety.authorize``.
        """
        self._auth = verify(auth)
        self._sender = sender
        self._reader = reader
        self._sleep = sleep
        self._used = False

    def _send(self, change: Change) -> None:
        self._auth.policy.validate(change.path, change.new_value, self._auth.scope)
        # Belt and braces (not an assert: survives python -O).
        if is_read_only_message(change.path, (change.new_value,)):
            raise UnsafeWrite(f"internal: {change.path} classified as read-only", path=change.path)
        self._sender.send_packet(encode(change.path, change.new_value))

    def _matches(self, kind: str, got: OscArg | None, wanted: WireValue) -> bool:
        """True when the console's answer is a number on the same grid step as ``wanted``."""
        if isinstance(got, bool) or not isinstance(got, int | float):
            return False
        try:
            return same_step(self._auth.policy.laws, kind, got, wanted)
        except ValueError:
            # A non-finite or otherwise unusable answer is a mismatch, not a crash mid-plan.
            return False

    def readback(self, path: str) -> tuple[OscArg | None, str | None]:
        """Read a parameter back: its plain value and the ``/node`` line of its parent."""
        return self._reader.query(path), self._reader.node(path.rsplit("/", 1)[0].lstrip("/"))

    def _write_one(self, change: Change) -> WriteResult:
        """Send one change and read it back; errors become a failed result."""
        try:
            self._send(change)
        except ChurchTuneError as error:
            # Refused or not delivered: treated as not sent; the readback of an undo will tell.
            return WriteResult(
                change.path, change.new_value, None, None, ok=False, sent=False, error=str(error)
            )
        try:
            self._sleep(SETTLE_S)
            got, node_line = self.readback(change.path)
        except ChurchTuneError as error:
            return WriteResult(
                change.path, change.new_value, None, None, ok=False, error=str(error)
            )
        kind = change.path.rsplit("/", 1)[1]
        ok = self._matches(kind, got, change.new_value)
        return WriteResult(change.path, change.new_value, got, node_line, ok)

    def apply(self) -> list[WriteResult]:
        """Send the authorized plan in order, reading each parameter back.

        Returns:
            One result per change that was attempted. The plan stops at the first result that
            is not ``ok`` (readback mismatch, refused change, console unreachable), so the list
            may be shorter than the plan; its last entry tells why.

        Raises:
            UnsafeWrite: The authorization is not valid any more, the writer was already used,
                or the undo file is missing. Nothing is sent in any of these cases.
        """
        auth = verify(self._auth)
        if self._used:
            raise UnsafeWrite("this writer already sent its plan; authorize a new one")
        # A missing undo file stops everything. Binding its content to the plan belongs to the
        # undo format (storage).
        if not auth.undo_path.is_file():
            raise UnsafeWrite(
                f"undo file {auth.undo_path} does not exist; refusing to write",
                undo_path=str(auth.undo_path),
            )
        self._used = True
        results: list[WriteResult] = []
        for change in auth.changes:
            result = self._write_one(change)
            results.append(result)
            logger.info(
                "console write %s: %s -> %s, readback %r (%s), node %r",
                change.path,
                change.old_display,
                change.new_display,
                result.readback,
                "ok" if result.ok else "MISMATCH",
                result.node,
            )
            if not result.ok:
                logger.error(
                    "write of %s failed (%s): stopping, the rest is not written",
                    change.path,
                    result.error or "readback mismatch",
                )
                break
        return results
