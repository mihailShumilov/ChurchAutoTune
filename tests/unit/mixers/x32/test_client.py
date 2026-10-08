"""Read-only client against a scripted transport (no network).

``test_read_only_classification`` is ported from the original tool.
"""

import struct

import pytest

from churchtune.errors import ReadOnlyViolation
from churchtune.mixers.x32.client import METERS_RENEW_S, X32Client, is_read_only_message
from churchtune.mixers.x32.codec import decode


class ScriptedTransport:
    """Records sent packets and replays queued replies; ``None`` in the queue is a timeout."""

    host = "127.0.0.1"

    def __init__(self, replies=(), clock=None, recv_takes_s=0.0):
        self.sent = []
        self.replies = list(replies)
        self.timeouts = []
        self.closed = False
        self._clock = clock
        self._recv_takes_s = recv_takes_s

    def send_packet(self, packet):
        self.sent.append(decode(packet))

    def recv(self, timeout_s):
        self.timeouts.append(timeout_s)
        if self._clock is not None:
            self._clock.advance(self._recv_takes_s)
        return self.replies.pop(0) if self.replies else None

    def close(self):
        self.closed = True


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _meter_blob(*levels):
    return struct.pack("<i", len(levels)) + struct.pack(f"<{len(levels)}f", *levels)


# ------------------------------------------------------------ ported from the original tool
def test_read_only_classification():
    assert is_read_only_message("/xinfo", ())
    assert is_read_only_message("/node", ("main/st/eq/2",))
    assert is_read_only_message("/meters", ("/meters/0",))
    assert is_read_only_message("/main/st/eq/2/f", ())
    assert not is_read_only_message("/main/st/eq/2/f", (0.5,))
    assert not is_read_only_message("/ch/10/mix/on", (1,))
    assert not is_read_only_message("/save", ())  # no-arg action not whitelisted
    assert not is_read_only_message("/meters", ("/ch/01/mix/on",))


# ------------------------------------------------------------ allowlist
@pytest.mark.parametrize(
    "address",
    [
        "/xinfo", "/info", "/status",
        "/main/st/eq/on", "/main/st/eq/1/type", "/main/st/eq/6/q",
        "/mtx/01/eq/1/f", "/mtx/06/eq/6/g", "/mtx/03/eq/on", "/mtx/03/mix/on",
        "/mtx/03/mix/fader", "/mtx/03/preamp/invert",
        "/outputs/main/01/delay/on", "/outputs/main/16/delay/time",
    ],
)  # fmt: skip
def test_allowlisted_queries_without_arguments_are_read_only(address):
    assert is_read_only_message(address, ()) is True


@pytest.mark.parametrize(
    "address",
    [
        "/save", "/load", "/-action/goscene", "/-snap/load", "/-prefs", "/node", "/meters",
        "/main/st/eq/7/g", "/main/st/eq/0/g", "/main/st/eq/2", "/main/st/eq", "/main/st/mix/fader",
        "/main/st/mix/on", "/main/m/eq/1/f", "/mtx/07/eq/1/f", "/mtx/00/mix/on", "/mtx/3/mix/on",
        "/mtx/03/mix", "/outputs/main/17/delay/on", "/outputs/main/00/delay/on",
        "/outputs/main/06/src", "/ch/10/mix/on", "/bus/01/mix/on", "/headamp/041/phantom",
        "/config/routing/CARD", "/fx/1/par/01",
        # Neighbours of allowed addresses: prefixes, suffixes, embedded newlines.
        "/xinfo2", "/xinfo/", "xinfo", "/main/st/eq/on\n", "/main/st/eq/2/f\n", "/main/st/eq/2/fx",
        "x/main/st/eq/2/f", "/MAIN/st/eq/2/f", " /xinfo", "",
    ],
)  # fmt: skip
def test_other_messages_without_arguments_are_not_read_only(address):
    assert is_read_only_message(address, ()) is False


@pytest.mark.parametrize(
    ("address", "args"),
    [
        ("/node", ("main/st/eq/2", "x")),
        ("/node", (1,)),
        ("/node", (0.5,)),
        ("/node", (b"main/st/eq",)),
        ("/meters", ("/meters/0", 1)),
        ("/meters", (0,)),
        ("/meters", ("meters/0",)),
        ("/meters", ("/ch/01/mix/on",)),
        ("/xinfo", ("x",)),
        ("/main/st/eq/2/g", ("0.5",)),
        ("/main/st/eq/on", (1,)),
        ("/mtx/03/mix/on", (0,)),
        ("/mtx/03/mix/fader", (0.5,)),
        ("/ch/10/mix/on", ("main/st/eq",)),
        ("/-action/goscene", (1,)),
        ("/node/", ("main/st/eq",)),
        ("node", ("main/st/eq",)),
    ],
)
def test_messages_with_arguments_are_read_only_only_for_node_and_meters(address, args):
    assert is_read_only_message(address, args) is False


@pytest.mark.parametrize(
    "path",
    ["main/st/eq", "main/st/eq/2", "mtx/03/mix", "-prefs", "-prefs/card", "headamp/041",
     "config/userrout/in", "config/routing/CARD", "outputs/main/06/delay", "ch/01/mix/01",
     "-stat/usbmounted", "a" * 64],
)  # fmt: skip
def test_node_accepts_plain_node_paths(path):
    assert is_read_only_message("/node", (path,)) is True


@pytest.mark.parametrize(
    "path",
    [
        # Anything that could be read as "path + values".
        "ch/10/mix ON", "ch/10/mix\nON", "main/st/eq/2 PEQ 100 0 2", "ch/10/mix\0 ON",
        "ch/10/mix\t", " main/st/eq", "main/st/eq ", "main/st/eq\n",
        # Not a plain path.
        "", "/", "/main/st/eq", "main//st", "main/st/eq/", "main/st/eq,1", "main/st/eq=1",
        "main/st/eq;x", "ch/[0-9]", "ch/*", "a" * 65,
        # Action, scene and recorder nodes are refused until verified on hardware.
        "-action", "-action/goscene", "-snap", "-snap/load", "-show", "-show/prepos/current",
        "-usb", "-usb/path", "-urec", "-urec/state",
    ],
)  # fmt: skip
def test_node_refuses_everything_that_is_not_a_plain_allowed_path(path):
    assert is_read_only_message("/node", (path,)) is False


@pytest.mark.parametrize("arg", ["/meters/0", "/meters/2", "/meters/9", "/meters/10", "/meters/16"])
def test_meters_accepts_meter_bank_addresses(arg):
    assert is_read_only_message("/meters", (arg,)) is True


@pytest.mark.parametrize(
    "arg",
    ["/meters/", "/meters/17", "/meters/99", "/meters/0 1 2", "/meters/0\n", "/meters/00",
     "meters/0", "/meters/0/1", "/meters/x", ""],
)  # fmt: skip
def test_meters_refuses_anything_else(arg):
    assert is_read_only_message("/meters", (arg,)) is False


# ------------------------------------------------------------ send / ask
@pytest.mark.parametrize(
    ("address", "args"),
    [
        ("/main/st/eq/2/g", (0.5,)),
        ("/ch/10/mix/on", (1,)),
        ("/main/st/mix/fader", (0.0,)),
        ("/headamp/041/phantom", (1,)),
        ("/save", ()),
        ("/-action/goscene", (1,)),
    ],
)
def test_send_refuses_anything_that_could_change_the_console(address, args):
    transport = ScriptedTransport()
    client = X32Client(transport)

    with pytest.raises(ReadOnlyViolation) as excinfo:
        client.send(address, *args)

    assert transport.sent == []
    assert excinfo.value.context == {"address": address}


def test_send_passes_read_messages_to_the_transport():
    transport = ScriptedTransport()

    X32Client(transport).send("/node", "main/st/eq/2")

    assert transport.sent == [("/node", ["main/st/eq/2"])]


def test_ask_returns_arguments_of_the_matching_reply_and_skips_others():
    transport = ScriptedTransport([("/meters/0", [b""]), (None, []), ("/main/st/eq/2/f", [0.355])])

    values = X32Client(transport).ask("/main/st/eq/2/f")

    assert values == [0.355]


def test_ask_returns_none_when_the_transport_times_out():
    assert X32Client(ScriptedTransport()).ask("/xinfo") is None


def test_ask_gives_up_when_the_deadline_passes_while_other_packets_keep_arriving():
    clock = FakeClock()
    noise = [("/meters/0", [b""])] * 10
    transport = ScriptedTransport(noise, clock=clock, recv_takes_s=0.2)

    values = X32Client(transport, clock=clock).ask("/xinfo", timeout_s=0.5)

    assert values is None
    # 0.5 s budget, 0.2 s per packet: three receives, each with the time that was left.
    assert transport.timeouts == pytest.approx([0.5, 0.3, 0.1])


def test_ping_asks_xinfo():
    transport = ScriptedTransport([("/xinfo", ["127.0.0.1", "X32P-EMU", "X32P", "4.09"])])

    reply = X32Client(transport).ping()

    assert reply == ["127.0.0.1", "X32P-EMU", "X32P", "4.09"]
    assert transport.sent == [("/xinfo", [])]
    assert transport.timeouts == [pytest.approx(1.5)]


# ------------------------------------------------------------ node / query
def test_node_returns_the_line_for_the_requested_path():
    transport = ScriptedTransport([("node", ["/main/st/eq/2 PEQ 232.3 -1.75 3.9\n"])])

    line = X32Client(transport).node("/main/st/eq/2")

    assert line == "/main/st/eq/2 PEQ 232.3 -1.75 3.9"
    assert transport.sent == [("/node", ["main/st/eq/2"])]


def test_node_retries_once_after_a_lost_packet():
    transport = ScriptedTransport([None, ("node", ["/main/st/eq ON\n"])])

    line = X32Client(transport).node("main/st/eq")

    assert line == "/main/st/eq ON"
    assert len(transport.sent) == 2


def test_node_gives_up_after_two_attempts():
    transport = ScriptedTransport()

    assert X32Client(transport).node("main/st/eq") is None
    assert len(transport.sent) == 2


@pytest.mark.parametrize(
    "reply", [["/main/st/eq/3 PEQ 1k97 -3.50 6.4\n"], [7], []], ids=["other-path", "int", "empty"]
)
def test_node_ignores_replies_that_are_not_the_requested_line(reply):
    transport = ScriptedTransport([("node", reply), ("node", reply)])

    assert X32Client(transport).node("main/st/eq/2") is None


def test_query_returns_the_first_argument():
    transport = ScriptedTransport([("/mtx/03/mix/on", [1])])

    value = X32Client(transport).query("mtx/03/mix/on")

    assert value == 1
    assert transport.sent == [("/mtx/03/mix/on", [])]


def test_query_retries_once_and_then_gives_up():
    transport = ScriptedTransport()

    assert X32Client(transport).query("/main/st/eq/2/f") is None
    assert len(transport.sent) == 2


def test_node_strips_the_leading_slash_but_refuses_a_path_with_values():
    transport = ScriptedTransport()

    with pytest.raises(ReadOnlyViolation):
        X32Client(transport).node("ch/10/mix ON")

    assert transport.sent == []


def test_query_refuses_addresses_outside_the_allowlist():
    transport = ScriptedTransport()

    with pytest.raises(ReadOnlyViolation):
        X32Client(transport).query("/ch/10/mix/on")

    assert transport.sent == []


# ------------------------------------------------------------ meters
def test_meters0_delivers_frames_and_ignores_other_packets():
    clock = FakeClock()
    replies = [
        ("/meters/0", [_meter_blob(0.5, 0.25)]),
        ("/xinfo", ["x"]),
        ("/meters/0", [7]),
        ("/meters/0", []),
        None,
        ("meters/0", [_meter_blob(1.0)]),
    ]
    transport = ScriptedTransport(replies, clock=clock, recv_takes_s=0.2)
    frames = []

    X32Client(transport, clock=clock).meters0(1.2, frames.append)

    assert frames == [[0.5, 0.25], [1.0]]
    assert transport.sent == [("/meters", ["/meters/0"])]


def test_meters0_renews_the_subscription_before_it_expires():
    clock = FakeClock()
    transport = ScriptedTransport(clock=clock, recv_takes_s=1.0)

    X32Client(transport, clock=clock).meters0(2 * METERS_RENEW_S + 4, lambda frame: None)

    # One second per receive: subscribed at 0 s, renewed once more than 8 s have passed (9 s, 18 s).
    assert transport.sent == [("/meters", ["/meters/0"])] * 3


def test_close_closes_the_transport_and_host_is_exposed():
    transport = ScriptedTransport()
    client = X32Client(transport)

    assert client.host == "127.0.0.1"
    client.close()

    assert transport.closed is True
