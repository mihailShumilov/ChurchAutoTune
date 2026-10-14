"""Temporary matrix mutes of the junction measurement against the emulator (SPEC S10, §9.2).

Never weaken, skip or xfail these tests: they are part of the safety suite.
"""

import pytest

from churchtune.cli.context import Console
from churchtune.cli.mute_writes import (
    clear_settled_mutes,
    plan_pending_restore,
    restore_pending,
    start_junction_mutes,
)
from churchtune.errors import UnsafeWrite
from churchtune.measure.junction import JunctionBlock
from churchtune.safety.authorize import TypedWord
from churchtune.storage import files
from churchtune.storage.paths import Workspace

WORD = TypedWord("заглушить", "заглушить")
SUB, TOPS = (3,), (1, 2)


@pytest.fixture
def console(client, transport):
    return Console(transport, client)


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path)


def on(emu, matrix):
    return bool(emu.mtx[matrix]["on"])


def test_blocks_mute_the_other_side_and_restore_puts_everything_back(emu, console, workspace):
    session = start_junction_mutes(console, workspace, "now", None, SUB, TOPS, WORD)
    assert files.read_pending_mutes(workspace) is not None  # before the first mute
    assert emu.writes == []
    session.switch(JunctionBlock.SUB_ONLY)
    assert (on(emu, 1), on(emu, 2), on(emu, 3)) == (False, False, True)
    session.switch(JunctionBlock.TOPS_ONLY)
    assert (on(emu, 1), on(emu, 2), on(emu, 3)) == (True, True, False)
    assert session.restore()
    assert (on(emu, 1), on(emu, 2), on(emu, 3)) == (True, True, True)
    assert files.read_pending_mutes(workspace) is None
    assert all(address.endswith("/mix/on") and address.startswith("/mtx/0")
               for address, _ in emu.writes)  # fmt: skip


def test_a_matrix_that_was_muted_stays_muted(emu, console, workspace):
    emu.hand_change("/mtx/02/mix/on", 0)
    session = start_junction_mutes(console, workspace, "now", None, SUB, TOPS, WORD)
    session.switch(JunctionBlock.TOPS_ONLY)
    assert not on(emu, 2)
    assert session.restore()
    assert not on(emu, 2)  # never switched on, not even "back"


def test_nothing_starts_while_earlier_mutes_are_pending(emu, console, workspace):
    session = start_junction_mutes(console, workspace, "now", None, SUB, TOPS, WORD)
    session.switch(JunctionBlock.SUB_ONLY)  # and then the program dies
    with pytest.raises(UnsafeWrite, match="still pending"):
        start_junction_mutes(console, workspace, "later", None, SUB, TOPS, WORD)


def test_pending_mutes_go_back_after_a_fresh_read_and_the_word(emu, console, workspace):
    session = start_junction_mutes(console, workspace, "now", None, SUB, TOPS, WORD)
    session.switch(JunctionBlock.SUB_ONLY)
    plan = plan_pending_restore(console, workspace)
    assert plan is not None
    assert [w.path for w in plan.writes] == ["/mtx/01/mix/on", "/mtx/02/mix/on"]
    with pytest.raises(UnsafeWrite, match="word"):
        restore_pending(console, workspace, plan, TypedWord("y", "заглушить"))
    restore_pending(console, workspace, plan, WORD)
    assert on(emu, 1)
    assert on(emu, 2)
    assert plan_pending_restore(console, workspace) is None


def test_a_console_changed_while_the_list_was_shown_is_not_overwritten(emu, console, workspace):
    session = start_junction_mutes(console, workspace, "now", None, SUB, TOPS, WORD)
    session.switch(JunctionBlock.SUB_ONLY)
    plan = plan_pending_restore(console, workspace)
    emu.hand_change("/mtx/01/mix/on", 1)  # someone unmuted it by hand meanwhile
    with pytest.raises(UnsafeWrite, match="changed"):
        restore_pending(console, workspace, plan, WORD)
    assert files.read_pending_mutes(workspace) is not None


@pytest.mark.parametrize(
    ("sub", "tops"), [((), (1,)), ((3,), ()), ((3,), (3,)), ((7,), (1,)), ((3,), (0,))]
)
def test_only_distinct_existing_speaker_matrices(emu, console, workspace, sub, tops):
    with pytest.raises(UnsafeWrite):
        start_junction_mutes(console, workspace, "now", None, sub, tops, WORD)
    assert files.read_pending_mutes(workspace) is None
    assert emu.writes == []


def test_a_wrong_word_mutes_nothing(emu, console, workspace):
    with pytest.raises(UnsafeWrite, match="word"):
        start_junction_mutes(
            console, workspace, "now", None, SUB, TOPS, TypedWord("x", "заглушить")
        )
    assert files.read_pending_mutes(workspace) is None


def test_a_mute_that_does_not_read_back_is_an_error(emu, console, workspace):
    session = start_junction_mutes(console, workspace, "now", None, SUB, TOPS, WORD)
    emu.ignored.add("/mtx/01/mix/on")
    with pytest.raises(UnsafeWrite, match="read back"):
        session.switch(JunctionBlock.SUB_ONLY)
    emu.ignored.clear()
    assert session.restore()


def test_a_half_failed_switch_still_comes_back(emu, console, workspace):
    """Matrix 1 muted, matrix 2 did not confirm: the restore reads the console, not its memory."""
    session = start_junction_mutes(console, workspace, "now", None, SUB, TOPS, WORD)
    emu.ignored.add("/mtx/02/mix/on")
    with pytest.raises(UnsafeWrite):
        session.switch(JunctionBlock.SUB_ONLY)
    assert not on(emu, 1)
    emu.ignored.clear()
    assert session.restore()
    assert on(emu, 1)
    assert files.read_pending_mutes(workspace) is None


def test_a_restore_that_does_not_confirm_keeps_the_record(emu, console, workspace):
    session = start_junction_mutes(console, workspace, "now", None, SUB, TOPS, WORD)
    session.switch(JunctionBlock.SUB_ONLY)
    emu.ignored.add("/mtx/01/mix/on")
    assert not session.restore()
    assert files.read_pending_mutes(workspace) is not None


def test_a_record_whose_switches_are_already_back_is_cleared_without_a_word(
    emu, console, workspace
):
    session = start_junction_mutes(console, workspace, "now", None, SUB, TOPS, WORD)
    session.switch(JunctionBlock.SUB_ONLY)
    assert not clear_settled_mutes(console, workspace)  # matrices 1 and 2 are still muted
    emu.hand_change("/mtx/01/mix/on", 1)
    emu.hand_change("/mtx/02/mix/on", 1)  # someone switched them back by hand
    assert clear_settled_mutes(console, workspace)
    assert files.read_pending_mutes(workspace) is None
    assert clear_settled_mutes(console, workspace)  # nothing pending: nothing to do
