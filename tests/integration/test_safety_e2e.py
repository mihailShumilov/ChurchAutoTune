"""End-to-end safety checks against the emulator. Never weaken, skip or xfail these tests."""

from dataclasses import replace

import pytest

from churchtune.domain.changes import Change, WriteScope
from churchtune.errors import ReadOnlyViolation, UnsafeWrite
from churchtune.mixers.x32 import params as P  # noqa: N812 - short alias kept from the original
from churchtune.mixers.x32.nodes import read_main_eq
from churchtune.safety.authorize import authorize
from churchtune.safety.plan import plan_changes
from tests.fixtures.venues import reference_policy

POLICY = reference_policy()
# Output delay and polarity writes are unverified on hardware and need an explicit opt-in.
UNVERIFIED_OK = reference_policy(allow_unverified_writes=True)


def _writer(transport, client, auth):
    # The writer is imported lazily in production code; tests follow the same habit.
    from churchtune.mixers.x32.writer import ScopedWriter  # noqa: PLC0415

    return ScopedWriter(transport, client, auth)


@pytest.fixture
def undo_file(tmp_path):
    path = tmp_path / "undo.json"
    path.write_text("{}", encoding="utf-8")
    return path


# ------------------------------------------------------------ ported from the original tool
def test_reader_refuses_writes(emu, client):
    for address, args in [
        ("/main/st/eq/2/g", (0.5,)),
        ("/ch/10/mix/on", (1,)),
        ("/main/st/mix/fader", (0.0,)),
        ("/headamp/041/phantom", (1,)),
        ("/save", ()),
        ("/-action/goscene", (1,)),
    ]:
        with pytest.raises(ReadOnlyViolation):
            client.send(address, *args)
    # Give the emulator a chance to log anything that slipped through before asserting.
    assert client.ping() is not None
    assert emu.writes == []
    assert emu.unknown == []


def test_unconfirmed_plan_never_reaches_the_console(emu, client, tmp_path):
    """Ported from ``test_writer_dry_run_by_default_sends_nothing``: without a confirmed
    authorization there is no writer, so nothing can be sent."""
    cur = read_main_eq(client)
    changes = plan_changes(cur, cur.with_band(replace(cur.band(2), gain_db=-3.0)), POLICY)
    assert [c.path for c in changes] == ["/main/st/eq/2/g"]

    for answer in ("", "n"):
        with pytest.raises(UnsafeWrite):
            authorize(WriteScope.MAIN, changes, tmp_path / "undo.json", answer, POLICY)

    assert client.ping() is not None
    assert emu.writes == []


def test_confirmed_plan_without_undo_file_never_reaches_the_console(
    emu, transport, client, tmp_path
):
    cur = read_main_eq(client)
    changes = plan_changes(cur, cur.with_band(replace(cur.band(2), gain_db=-3.0)), POLICY)
    auth = authorize(WriteScope.MAIN, changes, tmp_path / "never-written.json", "y", POLICY)

    with pytest.raises(UnsafeWrite, match="undo file"):
        _writer(transport, client, auth).apply()

    assert client.ping() is not None
    assert emu.writes == []


def test_apply_writes_only_the_plan_and_the_console_ends_in_the_target_state(
    emu, transport, client, undo_file
):
    cur = read_main_eq(client)
    high_pass_before = dict(emu.eq_x[1])
    target = cur.with_band(replace(cur.band(2), freq_hz=480.0, gain_db=-9.0, q=1.5))
    target = target.with_band(replace(cur.band(6), gain_db=2.0))
    changes = plan_changes(cur, target, POLICY)
    auth = authorize(WriteScope.MAIN, changes, undo_file, "y", POLICY)

    results = _writer(transport, client, auth).apply()

    assert [r.ok for r in results] == [True] * len(changes)
    assert [address for address, _ in emu.writes] == [c.path for c in changes]
    assert emu.foreign_writes() == []
    assert plan_changes(read_main_eq(client), target, POLICY) == []
    assert emu.eq_x[1] == high_pass_before  # the high-pass band is untouched
    assert results[0].node is not None
    assert results[0].node.startswith("/main/st/eq/2 ")


def test_undo_restores_only_changed_params(emu, transport, client, undo_file):
    """Ported: undoing a plan writes back its old values and leaves later manual changes alone."""
    cur = read_main_eq(client)
    changes = plan_changes(cur, cur.with_band(replace(cur.band(2), gain_db=-2.0)), POLICY)
    _writer(transport, client, authorize(WriteScope.MAIN, changes, undo_file, "y", POLICY)).apply()
    # someone changes band 5 by hand on the console afterwards
    emu.eq_x[5]["g"] = P.quantize_gain(P.gain_to_norm(-1.0))

    reverts = [
        Change(c.path, c.new_value, c.old_value, c.new_display, c.old_display) for c in changes
    ]
    _writer(transport, client, authorize(WriteScope.MAIN, reverts, undo_file, "y", POLICY)).apply()

    eq = emu.main_eq()
    assert eq.band(2).gain_db == pytest.approx(cur.band(2).gain_db)
    assert eq.band(5).gain_db == pytest.approx(-1.0)  # untouched by undo


def test_writer_stops_on_readback_mismatch(emu, transport, client, undo_file):
    """Ported: a console that does not answer the readback stops the plan after one write."""
    cur = read_main_eq(client)
    target = cur.with_band(replace(cur.band(2), gain_db=-2.0))
    target = target.with_band(replace(cur.band(5), gain_db=-1.0))
    changes = plan_changes(cur, target, POLICY)
    assert len(changes) == 2
    writer = _writer(transport, client, authorize(WriteScope.MAIN, changes, undo_file, "y", POLICY))
    writer.readback = lambda path: (None, None)

    results = writer.apply()

    assert len(results) == 1
    assert results[0].ok is False
    assert client.ping() is not None
    assert len(emu.writes) == 1


def test_sub_scope_plan_is_written_quantized_and_read_back(emu, transport, client, undo_file):
    old = emu.mtx[3]["fader"]
    new = P.quantize_fader(P.fader_to_norm(-29.3))
    changes = [
        Change("/mtx/03/mix/fader", old, new, "-37.5", "-29.3"),
        Change(
            "/mtx/03/eq/6/type",
            emu.mtx[3]["bands"][6]["type"],
            P.EQ_TYPES.index("LR24"),
            "HShv",
            "LR24",
        ),
        Change(
            "/outputs/main/06/delay/time", 0.0, P.quantize_delay(P.delay_to_norm(4.4)), "0.3", "4.4"
        ),
    ]
    auth = authorize(WriteScope.SUB, changes, undo_file, "y", UNVERIFIED_OK)

    results = _writer(transport, client, auth).apply()

    assert [r.ok for r in results] == [True, True, True]
    assert client.node("mtx/03/mix") == "/mtx/03/mix ON -29.3"
    assert client.node("mtx/03/eq/6").startswith("/mtx/03/eq/6 LR24 ")
    assert emu.foreign_writes(("/mtx/", "/outputs/main/")) == []


def test_every_switch_of_the_sub_scope_is_written_and_read_back(emu, transport, client, undo_file):
    changes = [
        Change("/mtx/03/mix/on", 1, 0, "ON", "OFF"),
        Change("/mtx/03/preamp/invert", 0, 1, "OFF", "ON"),
        Change("/outputs/main/06/delay/on", 0, 1, "OFF", "ON"),
    ]
    auth = authorize(WriteScope.SUB, changes, undo_file, "y", UNVERIFIED_OK)

    results = _writer(transport, client, auth).apply()

    assert [(r.ok, r.readback) for r in results] == [(True, 0), (True, 1), (True, 1)]
    assert client.node("mtx/03/mix").startswith("/mtx/03/mix OFF ")
    assert client.node("mtx/03/preamp") == "/mtx/03/preamp ON"
    assert client.node("outputs/main/06/delay").startswith("/outputs/main/06/delay ON")


def test_unverified_writes_cannot_be_authorized_without_the_opt_in(emu, client, undo_file):
    for change in [
        Change("/mtx/03/preamp/invert", 0, 1, "OFF", "ON"),
        Change("/outputs/main/06/delay/on", 0, 1, "OFF", "ON"),
        Change(
            "/outputs/main/06/delay/time", 0.0, P.quantize_delay(P.delay_to_norm(4.4)), "0.3", "4.4"
        ),
    ]:
        with pytest.raises(UnsafeWrite, match="never verified on hardware"):
            authorize(WriteScope.SUB, [change], undo_file, "y", POLICY)

    assert client.ping() is not None
    assert emu.writes == []


def test_a_forged_authorization_never_reaches_the_console(emu, transport, client, undo_file):
    cur = read_main_eq(client)
    changes = plan_changes(cur, cur.with_band(replace(cur.band(2), gain_db=-3.0)), POLICY)
    auth = authorize(WriteScope.MAIN, changes, undo_file, "y", POLICY)
    unmute_balcony = Change("/mtx/05/mix/on", 0, 1, "OFF", "ON")

    with pytest.raises(UnsafeWrite, match="altered"):
        _writer(transport, client, replace(auth, scope=WriteScope.SUB, changes=(unmute_balcony,)))

    assert client.ping() is not None
    assert emu.writes == []


def test_a_write_outside_every_scope_cannot_be_authorized_or_sent(emu, client, undo_file):
    forbidden = [
        Change("/ch/10/mix/on", 0, 1, "OFF", "ON"),
        Change("/main/st/mix/fader", 0.5, 0.75, "-10", "0"),
        Change("/headamp/041/phantom", 0, 1, "OFF", "ON"),
        Change("/config/routing/CARD", 0, 1, "a", "b"),
        Change("/-action/goscene", 0, 1, "0", "1"),
        Change("/mtx/04/eq/1/g", 0.5, 0.4, "0", "-3"),
    ]
    for change in forbidden:
        for scope in WriteScope:
            with pytest.raises(UnsafeWrite):
                authorize(scope, [change], undo_file, "y", UNVERIFIED_OK)

    assert client.ping() is not None
    assert emu.writes == []
