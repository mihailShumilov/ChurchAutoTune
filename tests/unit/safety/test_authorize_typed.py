"""Typed-word confirmation and the undo file's hash (M5). Never weaken, skip or xfail these tests."""

import dataclasses
import hashlib

import pytest

from churchtune.domain.changes import Change, WriteScope
from churchtune.errors import UnsafeWrite
from churchtune.mixers.x32 import params as P  # noqa: N812 - short alias kept from the original
from churchtune.mixers.x32.codec import decode
from churchtune.mixers.x32.writer import STOPPED, ScopedWriter
from churchtune.safety.authorize import TypedWord, authorize, authorize_typed, verify
from tests.fixtures.venues import reference_policy

POLICY = reference_policy()
GAIN = P.quantize_gain(P.gain_to_norm(-3.0))
PLAN = [
    Change("/main/st/eq/2/g", 0.5, GAIN, "+0.00 dB", "-3.00 dB"),
    Change("/main/st/eq/3/g", 0.5, GAIN, "+0.00 dB", "-3.00 dB"),
]
WORD = TypedWord("Apply", "apply")


class Console:
    def __init__(self):
        self.state = {}
        self.sent = []

    def send_packet(self, packet):
        address, args = decode(packet)
        self.sent.append(address)
        self.state[address] = args[0]

    def query(self, path):
        return self.state.get(path)

    def node(self, path):
        return f"/{path} line"


def undo_file(tmp_path, content=b'{"plan": 1}'):
    path = tmp_path / "undo.json"
    path.write_bytes(content)
    return path, hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize(
    ("typed", "expected", "ok"),
    [
        ("apply", "apply", True),
        (" APPLY ", "apply", True),
        ("применить", "применить", True),
        ("y", "apply", False),
        ("", "apply", False),
        ("appl", "apply", False),
        ("yes", "yes", False),  # a word shorter than four letters is never a confirmation word
    ],
)
def test_typed_word(typed, expected, ok):
    assert TypedWord(typed, expected).matches() is ok


def test_authorize_typed_needs_the_word_and_the_undo_hash(tmp_path):
    path, digest = undo_file(tmp_path)
    auth = authorize_typed(WriteScope.MAIN, PLAN, path, WORD, POLICY, undo_sha256=digest)
    assert verify(auth) is auth
    with pytest.raises(UnsafeWrite, match="word"):
        authorize_typed(WriteScope.MAIN, PLAN, path, TypedWord("y", "apply"), POLICY,
                        undo_sha256=digest)  # fmt: skip
    for bad in ("", "x" * 64, digest[:-1]):
        with pytest.raises(UnsafeWrite, match="hash"):
            authorize_typed(WriteScope.MAIN, PLAN, path, WORD, POLICY, undo_sha256=bad)


def test_the_undo_hash_is_sealed(tmp_path):
    path, digest = undo_file(tmp_path)
    auth = authorize_typed(WriteScope.MAIN, PLAN, path, WORD, POLICY, undo_sha256=digest)
    with pytest.raises(UnsafeWrite):
        verify(dataclasses.replace(auth, undo_sha256="0" * 64))


def test_the_writer_refuses_a_changed_undo_file(tmp_path):
    path, digest = undo_file(tmp_path)
    auth = authorize_typed(WriteScope.MAIN, PLAN, path, WORD, POLICY, undo_sha256=digest)
    path.write_bytes(b'{"plan": "edited"}')
    console = Console()
    with pytest.raises(UnsafeWrite, match="changed"):
        ScopedWriter(console, console, auth, sleep=lambda _s: None).apply()
    assert console.sent == []


def test_the_writer_stops_between_writes_when_asked(tmp_path):
    path, digest = undo_file(tmp_path)
    auth = authorize_typed(WriteScope.MAIN, PLAN, path, WORD, POLICY, undo_sha256=digest)
    console = Console()
    asked = iter([False, True])
    results = ScopedWriter(console, console, auth, sleep=lambda _s: None).apply(
        should_stop=lambda: next(asked)
    )
    assert console.sent == ["/main/st/eq/2/g"]
    assert [r.ok for r in results] == [True, False]
    assert results[-1].error == STOPPED
    assert not results[-1].sent


def test_the_new_scopes_go_through_the_same_seal(tmp_path):
    path, digest = undo_file(tmp_path)
    policy = dataclasses.replace(
        POLICY, sub_matrices=frozenset({3}), mute_originals=(("/mtx/03/mix/on", 1),)
    )
    mute = [Change("/mtx/03/mix/on", 1, 0, "ON", "OFF")]
    auth = authorize_typed(WriteScope.TEMP_MUTES, mute, path, WORD, policy, undo_sha256=digest)
    assert verify(auth) is auth
    other = dataclasses.replace(policy, mute_originals=(("/mtx/03/mix/on", 0),))
    with pytest.raises(UnsafeWrite):
        verify(dataclasses.replace(auth, policy=other))


def test_a_policy_that_is_not_exactly_a_write_policy_is_refused(tmp_path):
    class Permissive(type(POLICY)):  # type: ignore[misc]
        __slots__ = ()

    lax = Permissive(**{f.name: getattr(POLICY, f.name) for f in dataclasses.fields(POLICY)})
    path, digest = undo_file(tmp_path)
    with pytest.raises(UnsafeWrite, match="exactly a WritePolicy"):
        authorize_typed(WriteScope.MAIN, PLAN, path, WORD, lax, undo_sha256=digest)


@pytest.mark.parametrize(
    "scope",
    [WriteScope.TEMP_MUTES, WriteScope.SYSTEM_EQ, WriteScope.LEVELS, WriteScope.DELAY,
     WriteScope.POLARITY],
)  # fmt: skip
def test_the_new_scopes_never_accept_a_plain_yes(tmp_path, scope):
    path, _ = undo_file(tmp_path)
    with pytest.raises(UnsafeWrite, match="typed word"):
        authorize(scope, PLAN, path, "y", POLICY)
