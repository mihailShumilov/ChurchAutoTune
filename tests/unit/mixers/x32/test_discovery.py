"""Discovery: one fixed ``/xinfo`` packet, loopback only in dev-safe mode."""

import errno
import socket
import threading

import pytest

from churchtune.errors import DevSafeViolation
from churchtune.mixers.x32 import transport as transport_module
from churchtune.mixers.x32.codec import encode
from tests.unit.mixers.x32.test_transport import (  # noqa: F401 - fixtures
    LAN_HOST,
    XINFO,
    RecordingSocket,
    no_sockets,
    peer,
)

LAN_BROADCAST = "192.168.1.255"
LAN_CONSOLE = "192.168.1.80"
LAN_LOCAL = "192.168.7.23"
XINFO_REPLY = encode("/xinfo", "192.0.2.80", "Hall", "X32P", "4.09")


class DiscoverySocket(RecordingSocket):
    """Recorder with the extra calls discovery makes; never touches the network."""

    def __init__(self, datagrams=(), local=LAN_LOCAL, connect_error=None):
        super().__init__(datagrams=datagrams)
        self.options = []
        self.local = local
        self.connect_error = connect_error

    def setsockopt(self, *args):
        self.options.append(args)

    def connect(self, address):
        if self.connect_error:
            raise self.connect_error
        self.connected = address

    def getsockname(self):
        return (self.local, 5555)


@pytest.fixture
def outside(monkeypatch):
    """Dev-safe off, with a recording socket so nothing can reach the network."""
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CTUNE_DEV_SAFE", raising=False)
    sock = DiscoverySocket()
    monkeypatch.setattr(transport_module.socket, "socket", lambda *a, **k: sock)
    return sock


def test_discover_in_dev_safe_finds_a_loopback_console(peer):  # noqa: F811
    port = peer.getsockname()[1]

    def answer():
        data, sender = peer.recvfrom(1024)
        assert data == XINFO
        peer.sendto(encode("/other"), sender)
        peer.sendto(XINFO_REPLY, sender)
        peer.sendto(XINFO_REPLY, sender)  # duplicates are dropped

    thread = threading.Thread(target=answer)
    thread.start()
    found = transport_module.discover([("127.0.0.1", port)], 0.3)
    thread.join()
    assert found == [("127.0.0.1", port, ["192.0.2.80", "Hall", "X32P", "4.09"])]


def test_discover_in_dev_safe_accepts_localhost(peer):  # noqa: F811
    port = peer.getsockname()[1]
    assert transport_module.discover([("localhost", port)], 0.05) == []


@pytest.mark.parametrize("host", [LAN_HOST, "255.255.255.255", LAN_BROADCAST])
def test_discover_in_dev_safe_refuses_lan_and_broadcast(no_sockets, host):  # noqa: F811
    with pytest.raises(DevSafeViolation):
        transport_module.discover([(host, 10023)], 0.1)


def test_default_broadcasts_are_empty_in_dev_safe(no_sockets):  # noqa: F811
    assert transport_module.default_broadcasts() == []


@pytest.mark.parametrize(
    ("target", "error"),
    [(("127.0.0.1", 0), ValueError), (("127.0.0.1", 70000), ValueError)],
)
def test_discover_validates_ports(no_sockets, target, error):  # noqa: F811
    with pytest.raises(error):
        transport_module.discover([target], 0.1)


@pytest.mark.parametrize("host", ["console.local", "0.0.0.0", "224.0.0.1", "127.1"])
def test_discover_outside_dev_safe_rejects_bad_targets(outside, host):
    with pytest.raises(ValueError, match="IPv4"):
        transport_module.discover([(host, 10023)], 0.1)
    assert outside.sent == []


def test_discover_outside_dev_safe_broadcasts_only_xinfo(outside):
    outside.datagrams = [(XINFO_REPLY, (LAN_CONSOLE, 10023))]
    found = transport_module.discover([("255.255.255.255", 10023), (LAN_BROADCAST, 10023)], 0.1)
    assert [packet for packet, _ in outside.sent] == [XINFO, XINFO]
    assert (socket.SOL_SOCKET, socket.SO_BROADCAST, 1) in outside.options
    assert found == [(LAN_CONSOLE, 10023, ["192.0.2.80", "Hall", "X32P", "4.09"])]
    assert outside.closed


def test_discover_skips_unreachable_networks(outside):
    outside.error = OSError(errno.ENETUNREACH, "Network is unreachable")
    assert transport_module.discover([(LAN_BROADCAST, 10023)], 0.05) == []
    assert outside.closed


def test_default_broadcasts_outside_dev_safe(outside):
    assert transport_module.default_broadcasts() == ["255.255.255.255", "192.168.7.255"]
    assert outside.sent == []  # finding the interface sends nothing
    outside.local = "127.0.0.1"
    assert transport_module.default_broadcasts() == ["255.255.255.255"]
    outside.connect_error = OSError(errno.ENETUNREACH, "no network")
    assert transport_module.default_broadcasts() == ["255.255.255.255"]


def test_listening_stops_at_the_deadline_even_while_packets_arrive(outside, monkeypatch):
    ticks = iter([0.0, 0.0, 10.0])
    monkeypatch.setattr(transport_module.time, "monotonic", lambda: next(ticks))
    outside.datagrams = [(encode("/other"), ("127.0.0.1", 10023))]
    assert transport_module.discover([("255.255.255.255", 10023)], 0.5) == []


def test_discover_in_dev_safe_never_enables_broadcast(monkeypatch):
    sock = DiscoverySocket()
    monkeypatch.setattr(transport_module.socket, "socket", lambda *a, **k: sock)
    transport_module.discover([("127.0.0.1", 10023)], 0.01)
    assert sock.options == []
    assert sock.bound == ("127.0.0.1", 0)
    assert [address for _, address in sock.sent] == [("127.0.0.1", 10023)]
