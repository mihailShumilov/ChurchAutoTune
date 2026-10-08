"""The scoped writer against recording fakes (no network).

Never weaken, skip or xfail these tests.
"""

import dataclasses
import logging
import math

import pytest

from churchtune.domain.changes import Change, WriteScope
from churchtune.errors import ConsoleUnreachable, UnsafeWrite
from churchtune.mixers.x32 import params as P  # noqa: N812 - short alias kept from the original
from churchtune.mixers.x32.codec import decode
from churchtune.mixers.x32.writer import SETTLE_S, ScopedWriter, WriteResult
from churchtune.safety.authorize import authorize
from tests.fixtures.venues import reference_policy

POLICY = reference_policy(allow_unverified_writes=True)
GAIN = P.quantize_gain(P.gain_to_norm(-3.0))
FREQ = P.quantize_freq(P.freq_to_norm(480.0))
PLAN = [
    Change("/main/st/eq/2/g", 0.5, GAIN, "+0.00 dB", "-3.00 dB"),
    Change("/main/st/eq/2/f", 0.355, FREQ, "232.3 Hz", "479.8 Hz"),
    Change("/main/st/eq/2/type", 3, 2, "VEQ", "PEQ"),
]


class Console:
    """A fake console: applies what is sent and answers reads from its state."""

    def __init__(self):
        self.sent = []
        self.state = {}
        self.events = []
        self.answer = None  # override for query()

    def send_packet(self, packet):
        address, args = decode(packet)
        self.sent.append((address, args))
        self.events.append(("send", address))
        self.state[address] = args[0]

    def query(self, path):
        self.events.append(("query", path))
        return self.answer(path) if self.answer else self.state.get(path)

    def node(self, path):
        self.events.append(("node", path))
        return f"/{path} line"


@pytest.fixture
def undo(tmp_path):
    path = tmp_path / "undo.json"
    path.write_text("{}", encoding="utf-8")
    return path


def make_writer(console, undo, plan=PLAN, scope=WriteScope.MAIN, sleeps=None):
    auth = authorize(scope, plan, undo, "y", POLICY)
    sleeps = sleeps if sleeps is not None else []
    return ScopedWriter(console, console, auth, sleep=sleeps.append)


def test_apply_sends_the_plan_in_order_and_reads_every_write_back(undo):
    console, sleeps = Console(), []

    results = make_writer(console, undo, sleeps=sleeps).apply()

    assert [address for address, _ in console.sent] == [c.path for c in PLAN]
    assert console.sent[0][1] == [pytest.approx(GAIN)]
    assert console.sent[2][1] == [2]
    assert all(r.ok for r in results)
    assert [r.path for r in results] == [c.path for c in PLAN]
    assert results[0] == WriteResult(
        "/main/st/eq/2/g", GAIN, pytest.approx(GAIN), "/main/st/eq/2 line", ok=True
    )
    assert sleeps == [SETTLE_S] * 3


def test_each_write_is_followed_by_its_own_readback_before_the_next_write(undo):
    console = Console()

    make_writer(console, undo).apply()

    assert console.events[:6] == [
        ("send", "/main/st/eq/2/g"),
        ("query", "/main/st/eq/2/g"),
        ("node", "main/st/eq/2"),
        ("send", "/main/st/eq/2/f"),
        ("query", "/main/st/eq/2/f"),
        ("node", "main/st/eq/2"),
    ]


def test_apply_refuses_when_the_undo_file_is_missing_and_sends_nothing(tmp_path):
    console = Console()
    writer = make_writer(console, tmp_path / "missing.json")

    with pytest.raises(UnsafeWrite, match="undo file") as excinfo:
        writer.apply()

    assert console.sent == []
    assert excinfo.value.context == {"undo_path": str(tmp_path / "missing.json")}


def test_apply_refuses_when_the_undo_path_is_a_directory(tmp_path):
    console = Console()

    with pytest.raises(UnsafeWrite, match="undo file"):
        make_writer(console, tmp_path).apply()

    assert console.sent == []


@pytest.mark.parametrize(
    "answer",
    [None, "0.4", b"x", math.nan, math.inf, True, 0.9],
    ids=["silent", "str", "bytes", "nan", "inf", "bool", "other-step"],
)
def test_apply_stops_at_the_first_readback_mismatch(undo, answer):
    console = Console()
    console.answer = lambda path: answer

    results = make_writer(console, undo).apply()

    assert len(results) == 1
    assert results[0].ok is False
    assert len(console.sent) == 1


def test_readback_within_the_same_grid_step_counts_as_a_match(undo):
    console = Console()
    # The console answers as float32, slightly off the float64 value that was sent.
    console.answer = lambda path: console.state[path] + (1e-7 if path.endswith(("/g", "/f")) else 0)

    results = make_writer(console, undo).apply()

    assert [r.ok for r in results] == [True, True, True]


def test_mismatch_in_the_middle_keeps_earlier_results_and_skips_the_rest(undo):
    console = Console()
    console.answer = lambda path: None if path.endswith("/f") else console.state[path]

    results = make_writer(console, undo).apply()

    assert [r.ok for r in results] == [True, False]
    assert [address for address, _ in console.sent] == ["/main/st/eq/2/g", "/main/st/eq/2/f"]


def test_writer_requires_an_authorization_object(undo):
    console = Console()

    for not_auth in (None, PLAN, {"scope": "main"}, "y"):
        with pytest.raises(UnsafeWrite, match="WriteAuthorization"):
            ScopedWriter(console, console, not_auth)  # type: ignore[arg-type]

    assert console.sent == []


def test_writer_refuses_an_authorization_altered_after_it_was_issued(undo):
    console = Console()
    auth = authorize(WriteScope.MAIN, PLAN, undo, "y", POLICY)
    unmute = Change("/mtx/05/mix/on", 0, 1, "OFF", "ON")
    forged = dataclasses.replace(auth, scope=WriteScope.SUB, changes=(unmute,))

    with pytest.raises(UnsafeWrite, match="altered"):
        ScopedWriter(console, console, forged)

    assert console.sent == []


def test_writer_sends_its_plan_only_once(undo):
    console = Console()
    writer = make_writer(console, undo)
    writer.apply()

    with pytest.raises(UnsafeWrite, match="already sent"):
        writer.apply()

    assert len(console.sent) == len(PLAN)


def test_a_refused_start_does_not_use_up_the_writer(tmp_path):
    console = Console()
    undo_path = tmp_path / "undo.json"
    writer = make_writer(console, undo_path)
    with pytest.raises(UnsafeWrite, match="undo file"):
        writer.apply()

    undo_path.write_text("{}", encoding="utf-8")

    assert len(writer.apply()) == len(PLAN)


@pytest.mark.parametrize("answer", [math.inf, -math.inf, math.nan, 0.9, 1.7, 1.0, True, None, "1"])
@pytest.mark.parametrize(
    ("path", "wanted"),
    [("/mtx/03/mix/on", 0), ("/mtx/03/preamp/invert", 1), ("/mtx/03/eq/6/type", 13)],
)
def test_switches_and_types_match_only_the_exact_integer(undo, path, wanted, answer):
    console = Console()
    console.answer = lambda queried: answer
    plan = [Change(path, 1 - wanted if wanted < 2 else 4, wanted, "a", "b")] * 2

    results = make_writer(console, undo, plan=plan, scope=WriteScope.SUB).apply()

    assert [r.ok for r in results] == [False]
    assert len(console.sent) == 1


def test_console_unreachable_during_readback_is_reported_with_what_was_sent(undo):
    console = Console()

    def lost(path):
        if path.endswith("/f"):
            raise ConsoleUnreachable("no route", host="127.0.0.1")
        return console.state[path]

    console.answer = lost

    results = make_writer(console, undo).apply()

    assert [(r.path, r.ok, r.sent) for r in results] == [
        ("/main/st/eq/2/g", True, True),
        ("/main/st/eq/2/f", False, True),
    ]
    assert results[1].error == "no route"
    assert [address for address, _ in console.sent] == ["/main/st/eq/2/g", "/main/st/eq/2/f"]


def test_console_unreachable_while_sending_is_reported_as_not_sent(undo):
    console = Console()
    original = console.send_packet

    def failing(packet):
        if len(console.sent) == 1:
            raise ConsoleUnreachable("network down", host="127.0.0.1")
        original(packet)

    console.send_packet = failing

    results = make_writer(console, undo).apply()

    assert [(r.ok, r.sent) for r in results] == [(True, True), (False, False)]
    assert results[1].error == "network down"


def test_writer_has_no_way_to_send_anything_but_the_authorized_plan(undo):
    writer = make_writer(Console(), undo)
    public = {name for name in dir(writer) if not name.startswith("_")}

    assert public == {"apply", "readback"}


def test_changes_are_validated_again_right_before_sending(undo, monkeypatch):
    console = Console()
    writer = make_writer(console, undo)
    calls = []

    def refuse_second(path, value, scope):
        calls.append(path)
        if len(calls) == 2:
            raise UnsafeWrite("policy changed its mind", path=path)

    monkeypatch.setattr(type(POLICY), "validate", lambda self, p, v, s: refuse_second(p, v, s))

    results = writer.apply()

    assert [address for address, _ in console.sent] == ["/main/st/eq/2/g"]
    assert calls == ["/main/st/eq/2/g", "/main/st/eq/2/f"]
    assert [(r.path, r.ok, r.sent) for r in results] == [
        ("/main/st/eq/2/g", True, True),
        ("/main/st/eq/2/f", False, False),
    ]
    assert results[1].error == "policy changed its mind"


def test_a_change_that_looks_like_a_read_is_never_sent(undo, monkeypatch):
    console = Console()
    writer = make_writer(console, undo)
    monkeypatch.setattr(
        "churchtune.mixers.x32.writer.is_read_only_message", lambda address, args: True
    )

    results = writer.apply()

    assert console.sent == []
    assert len(results) == 1
    assert (results[0].ok, results[0].sent) == (False, False)
    assert "classified as read-only" in (results[0].error or "")


def test_sub_scope_plan_is_written_with_ints_and_floats_as_planned(undo):
    plan = [
        Change("/mtx/03/mix/on", 1, 0, "ON", "OFF"),
        Change(
            "/mtx/03/mix/fader", 0.2033, P.quantize_fader(P.fader_to_norm(-29.3)), "-37.5", "-29.3"
        ),
        Change(
            "/outputs/main/06/delay/time", 0.0, P.quantize_delay(P.delay_to_norm(4.4)), "0.3", "4.4"
        ),
    ]
    console = Console()

    results = make_writer(console, undo, plan=plan, scope=WriteScope.SUB).apply()

    assert all(r.ok for r in results)
    assert console.sent[0] == ("/mtx/03/mix/on", [0])
    assert isinstance(console.sent[1][1][0], float)
    assert results[1].node == "/mtx/03/mix line"


def test_every_write_is_logged_with_old_new_and_readback(undo, caplog):
    console = Console()

    with caplog.at_level(logging.INFO, logger="churchtune.mixers.x32.writer"):
        make_writer(console, undo).apply()

    first = caplog.records[0].getMessage()
    assert "/main/st/eq/2/g" in first
    assert "+0.00 dB -> -3.00 dB" in first
    assert "ok" in first


def test_readback_mismatch_is_logged_as_an_error(undo, caplog):
    console = Console()
    console.answer = lambda path: None

    with caplog.at_level(logging.INFO, logger="churchtune.mixers.x32.writer"):
        make_writer(console, undo).apply()

    assert [r.levelno for r in caplog.records] == [logging.INFO, logging.ERROR]
    assert "MISMATCH" in caplog.records[0].getMessage()


def test_an_empty_plan_writes_nothing(undo):
    console = Console()

    assert make_writer(console, undo, plan=[]).apply() == []
    assert console.events == []
