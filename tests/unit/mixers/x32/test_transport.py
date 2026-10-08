import errno
import socket

import pytest

from churchtune.errors import ConsoleUnreachable, DevSafeViolation
from churchtune.mixers.x32 import transport as transport_module
from churchtune.mixers.x32.codec import encode
from churchtune.mixers.x32.transport import UdpTransport

LAN_HOST = "192.168.0.80"
XINFO = b"/xinfo\0\0,\0\0\0"


@pytest.fixture
def peer():
    """A plain UDP socket on loopback standing in for a console."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(1.0)
    yield sock
    sock.close()


class RecordingSocket:
    """Stands in for ``socket.socket``: records calls, never touches the network."""

    def __init__(self, error=None, datagrams=()):
        self.error = error
        self.datagrams = list(datagrams)
        self.bound = None
        self.sent = []
        self.closed = False

    def bind(self, address):
        self.bound = address

    def settimeout(self, timeout):
        pass

    def sendto(self, packet, address):
        if self.error:
            raise self.error
        self.sent.append((packet, address))

    def recvfrom(self, size):
        if self.error:
            raise self.error
        if not self.datagrams:
            raise TimeoutError
        return self.datagrams.pop(0)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_socket(monkeypatch):
    """Replace the socket class in the transport module by a recorder."""
    sock = RecordingSocket()
    monkeypatch.setattr(transport_module.socket, "socket", lambda *a, **k: sock)
    return sock


@pytest.fixture
def no_sockets(monkeypatch):
    """Fail the test if anything tries to create a socket."""

    def _forbidden(*args, **kwargs):
        raise AssertionError("a socket was created")

    monkeypatch.setattr(transport_module.socket, "socket", _forbidden)


@pytest.fixture
def outside_dev_safe(monkeypatch, fake_socket):
    """Dev-safe off for one test. Always together with the fake socket: nothing can be sent."""
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CTUNE_DEV_SAFE", raising=False)
    return fake_socket


# ------------------------------------------------------------ dev-safe
@pytest.mark.parametrize("host", [LAN_HOST, "10.0.0.5", "x32.local", "8.8.8.8", "::1%en0", ""])
def test_refuses_non_loopback_host_in_dev_safe_before_creating_a_socket(no_sockets, host):
    with pytest.raises(DevSafeViolation):
        UdpTransport(host, 10023)


def test_agent_variable_alone_is_enough_to_refuse(no_sockets, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CTUNE_DEV_SAFE", "0")

    with pytest.raises(DevSafeViolation):
        UdpTransport(LAN_HOST, 10023)


def test_constructor_has_no_parameter_that_could_switch_the_guard_off(no_sockets):
    with pytest.raises(TypeError):
        UdpTransport(LAN_HOST, 10023, environ={})  # type: ignore[call-arg]


def test_localhost_is_replaced_by_the_literal_loopback_address(fake_socket):
    udp = UdpTransport("localhost", 10023)

    assert (udp.host, udp.port) == ("127.0.0.1", 10023)


def test_socket_is_bound_to_loopback_in_dev_safe(fake_socket):
    UdpTransport("127.0.0.1", 10023)

    assert fake_socket.bound == ("127.0.0.1", 0)


def test_outside_dev_safe_a_lan_host_is_accepted_and_the_socket_is_not_bound(outside_dev_safe):
    udp = UdpTransport(LAN_HOST, 10023)

    assert udp.host == LAN_HOST
    assert outside_dev_safe.bound is None
    assert outside_dev_safe.sent == []


def test_outside_dev_safe_localhost_still_becomes_the_literal_address(outside_dev_safe):
    assert UdpTransport("localhost", 10023).host == "127.0.0.1"


# ------------------------------------------------------------ address validation
@pytest.mark.parametrize(
    "host",
    ["x32.local", "console", "::1", "224.0.0.1", "0.0.0.0", "255.255.255.255", "10.0.0.5:10023",
     "10.0.0.05", " 10.0.0.5", "<broadcast>", ""],
)  # fmt: skip
def test_outside_dev_safe_only_unicast_ipv4_literals_are_accepted(outside_dev_safe, host):
    with pytest.raises(ValueError, match="console host"):
        UdpTransport(host, 10023)


def test_ipv6_loopback_is_refused_because_the_socket_is_ipv4(no_sockets):
    with pytest.raises(ValueError, match="IPv4"):
        UdpTransport("::1", 10023)


@pytest.mark.parametrize("port", [0, -1, 65536, 100000])
def test_rejects_port_outside_range(no_sockets, port):
    with pytest.raises(ValueError, match="port"):
        UdpTransport("127.0.0.1", port)


# ------------------------------------------------------------ send / receive on loopback
def test_send_packet_delivers_the_datagram_to_the_console_address(peer):
    with UdpTransport("127.0.0.1", peer.getsockname()[1]) as udp:
        udp.send_packet(XINFO)

        data, _ = peer.recvfrom(1024)

    assert data == XINFO


def test_recv_decodes_a_reply(peer):
    with UdpTransport("127.0.0.1", peer.getsockname()[1]) as udp:
        udp.send_packet(encode("/xinfo"))
        _, sender = peer.recvfrom(1024)
        peer.sendto(encode("/xinfo", "127.0.0.1", "X32P-EMU", "X32P", "4.09"), sender)

        reply = udp.recv(1.0)

    assert reply == ("/xinfo", ["127.0.0.1", "X32P-EMU", "X32P", "4.09"])


def test_recv_returns_none_on_timeout(peer):
    with UdpTransport("127.0.0.1", peer.getsockname()[1]) as udp:
        assert udp.recv(0.0) is None


def test_recv_passes_garbage_through_as_undecodable(peer):
    with UdpTransport("127.0.0.1", peer.getsockname()[1]) as udp:
        udp.send_packet(b"x\0\0\0")
        _, sender = peer.recvfrom(1024)
        peer.sendto(b"abc", sender)

        assert udp.recv(1.0) == (None, [])


def test_recv_drops_datagrams_from_other_hosts_and_keeps_waiting(fake_socket):
    fake_socket.datagrams = [
        (encode("node", "/main/st/eq ON\n"), ("10.9.9.9", 10023)),
        (encode("node", "/main/st/eq OFF\n"), ("127.0.0.1", 10023)),
    ]
    udp = UdpTransport("127.0.0.1", 10023)

    assert udp.recv(1.0) == ("node", ["/main/st/eq OFF\n"])


def test_recv_times_out_when_only_other_hosts_answer(fake_socket):
    fake_socket.datagrams = [(XINFO, ("10.9.9.9", 10023))] * 3
    udp = UdpTransport("127.0.0.1", 10023)

    assert udp.recv(0.5) is None


def test_recv_stops_at_the_deadline_even_if_foreign_datagrams_keep_coming(fake_socket, monkeypatch):
    ticks = iter([0.0, 0.0, 0.3, 0.6])
    monkeypatch.setattr(transport_module.time, "monotonic", lambda: next(ticks))
    fake_socket.datagrams = [(XINFO, ("10.9.9.9", 10023))] * 10
    udp = UdpTransport("127.0.0.1", 10023)

    assert udp.recv(0.5) is None
    assert len(fake_socket.datagrams) == 8


# ------------------------------------------------------------ errors
@pytest.mark.parametrize(
    ("error", "no_route"),
    [
        (OSError(errno.EHOSTUNREACH, "No route to host"), True),
        (OSError(113, "No route to host"), True),
        (OSError(errno.ENETDOWN, "Network is down"), False),
    ],
)
def test_socket_errors_become_console_unreachable(fake_socket, error, no_route):
    fake_socket.error = error
    udp = UdpTransport("127.0.0.1", 10023)

    with pytest.raises(ConsoleUnreachable) as sent:
        udp.send_packet(XINFO)
    with pytest.raises(ConsoleUnreachable) as received:
        udp.recv(0.1)

    for excinfo in (sent, received):
        assert excinfo.value.context["no_route"] is no_route
        assert excinfo.value.context["host"] == "127.0.0.1"
        assert excinfo.value.context["port"] == 10023
        assert excinfo.value.__cause__ is error


def test_close_closes_the_socket(peer):
    udp = UdpTransport("127.0.0.1", peer.getsockname()[1])

    udp.close()

    with pytest.raises(ConsoleUnreachable):
        udp.send_packet(XINFO)
