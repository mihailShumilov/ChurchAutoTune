"""The X32/M32 driver: discovery, identity, snapshot, topology, USB map, meters and link quality.

Read-only. Writing stays in ``writer.py`` behind a ``WriteAuthorization`` (SPEC §9.3).
"""

import statistics
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime

from churchtune.domain.snapshot import ConsoleSnapshot, UsbAudioMap
from churchtune.domain.topology import TopologyGuess
from churchtune.errors import ConsoleUnreachable, UnsupportedConsole
from churchtune.mixers.base import (
    ConsoleModel,
    DiscoveredConsole,
    MeterFrame,
    MeterSet,
    ProgressCb,
)
from churchtune.mixers.x32.client import X32Client
from churchtune.mixers.x32.models import console_model, identity_from_xinfo
from churchtune.mixers.x32.snapshot import parse_snapshot, read_raw
from churchtune.mixers.x32.topology import infer_topology
from churchtune.mixers.x32.transport import X32_PORT, default_broadcasts, discover
from churchtune.mixers.x32.usb import usb_audio_map

DISCOVERY_TIMEOUT_S = 1.5
LINK_PINGS = 20
LINK_PING_TIMEOUT_S = 0.5
# /meters/0 layout: 32 channels, 8 aux, 8 fx returns, 16 buses, 6 matrices.
_CH, _AUX, _FX, _BUS, _MTX = 32, 8, 8, 16, 6
_BUS_FIRST = _CH + _AUX + _FX
_MTX_FIRST = _BUS_FIRST + _BUS


@dataclass(frozen=True, slots=True)
class LinkQuality:
    """Round trips of repeated ``/xinfo`` requests.

    Attributes:
        sent: Requests sent.
        answered: Requests answered in time.
        median_ms: Median round trip of the answered requests; None when none was answered.
        max_ms: Slowest answered round trip; None when none was answered.
    """

    sent: int
    answered: int
    median_ms: float | None
    max_ms: float | None

    @property
    def loss(self) -> float:
        """Share of unanswered requests, 0..1."""
        return 1.0 - self.answered / self.sent if self.sent else 0.0


def discover_consoles(
    timeout_s: float = DISCOVERY_TIMEOUT_S,
    extra: tuple[tuple[str, int], ...] = (),
) -> list[DiscoveredConsole]:
    """Find X32/M32 consoles: broadcast ``/xinfo`` (never in dev-safe mode) plus ``extra`` hosts.

    Args:
        timeout_s: How long to wait for answers.
        extra: Known addresses to ask directly (last known console, the emulator).

    Returns:
        Every console that answered with a model this driver knows, without duplicates.
    """
    targets = [(host, X32_PORT) for host in default_broadcasts()] + list(extra)
    if not targets:
        return []
    found: list[DiscoveredConsole] = []
    for host, port, args in discover(targets, timeout_s):
        try:
            identity = identity_from_xinfo(list(args), host)
        except UnsupportedConsole:
            continue
        found.append(DiscoveredConsole(identity, port))
    return found


def _frame(values: list[float]) -> MeterFrame:
    return MeterFrame(
        tuple(values[:_CH]),
        tuple(values[_BUS_FIRST:_MTX_FIRST]),
        tuple(values[_MTX_FIRST : _MTX_FIRST + _MTX]),
    )


class X32Driver:
    """A connected X32 or M32, read-only."""

    def __init__(
        self,
        client: X32Client,
        model: ConsoleModel,
        *,
        now: Callable[[], datetime] = datetime.now,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Wrap a client of a console whose identity is known.

        Args:
            client: Read-only client.
            model: Identity and capabilities.
            now: Wall clock for the snapshot time stamp.
            clock: Monotonic clock for round trips.
        """
        self._client = client
        self._model = model
        self._now = now
        self._clock = clock

    @classmethod
    def connect(cls, client: X32Client, timeout_s: float = 1.5) -> "X32Driver":
        """Ask the console who it is and build a driver.

        Raises:
            ConsoleUnreachable: The console did not answer ``/xinfo``.
            UnsupportedConsole: It is not an X32 or an M32.
        """
        answer = client.ping(timeout_s)
        if answer is None:
            raise ConsoleUnreachable(f"no answer to /xinfo from {client.host}", host=client.host)
        return cls(client, console_model(identity_from_xinfo(list(answer), client.host)))

    @property
    def model(self) -> ConsoleModel:
        """Model, firmware and capabilities."""
        return self._model

    def read_snapshot(self, progress: ProgressCb | None = None) -> ConsoleSnapshot:
        """Read every snapshot node and parse it."""
        taken_at = self._now().isoformat(timespec="seconds")
        raw = read_raw(self._client, progress)
        return parse_snapshot(self._model.identity, raw, taken_at)

    def infer_topology(self, snapshot: ConsoleSnapshot) -> TopologyGuess:
        """Guess speaker roles. Pure."""
        return infer_topology(snapshot)

    def usb_audio_map(self, snapshot: ConsoleSnapshot) -> UsbAudioMap:
        """Map the USB card. Pure."""
        return usb_audio_map(snapshot)

    def meters(self, which: MeterSet, duration_s: float) -> Iterator[MeterFrame]:
        """Stream strip meters for ``duration_s`` seconds."""
        if which is not MeterSet.STRIPS:  # pragma: no cover - one set exists today
            raise ValueError(f"unknown meter set {which!r}")
        for values in self._client.iter_meters0(duration_s):
            yield _frame(values)

    def link_quality(
        self, count: int = LINK_PINGS, timeout_s: float = LINK_PING_TIMEOUT_S
    ) -> LinkQuality:
        """Measure round trips and loss with ``count`` ``/xinfo`` requests (SPEC S2)."""
        trips: list[float] = []
        for _ in range(count):
            start = self._clock()
            if self._client.ping(timeout_s) is not None:
                trips.append((self._clock() - start) * 1000.0)
        if not trips:
            return LinkQuality(count, 0, None, None)
        return LinkQuality(count, len(trips), statistics.median(trips), max(trips))
