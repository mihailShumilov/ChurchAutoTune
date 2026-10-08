"""Sub / top crossover and alignment against the emulator and the simulated system.

``test_roles_and_crossover_plan`` is ported from the original tool's ``test_sub.py``. The flow
test follows the original ``test_sub_full_flow_improves_crossover`` at library level; the same
flow through the CLI commands is tested with the CLI.
"""

import numpy as np
import pytest

from churchtune.audio.fake.backend import ConsoleRoomBackend
from churchtune.audio.fake.room import speaker_rooms
from churchtune.design.alignment import JunctionPoint, analyze_alignment, recommend_level
from churchtune.design.crossover import plan_crossover
from churchtune.domain.changes import WriteScope
from churchtune.domain.console import Roles
from churchtune.domain.eq import EqType
from churchtune.errors import ConsoleUnreachable
from churchtune.measure.config import SweepConfig
from churchtune.measure.junction import block_stimulus, mute_plan, split_irs
from churchtune.mixers.x32 import params as P  # noqa: N812 - short alias kept from the original
from churchtune.mixers.x32.nodes import read_console_state
from churchtune.mixers.x32.topology import detect_roles
from churchtune.safety.authorize import authorize
from churchtune.safety.sub_plan import alignment_changes, crossover_changes
from tests.fixtures.venues import SUB_MATRIX, reference_policy

FS_HZ = 48000.0
SWEEP = SweepConfig()
POLICY = reference_policy()
UNVERIFIED_OK = reference_policy(allow_unverified_writes=True)
GRID = P.X32EqGrid()
JUNCTION_LEVEL_DBFS = -17.0


def apply(transport, client, changes, undo_file, policy=POLICY):
    from churchtune.mixers.x32.writer import ScopedWriter  # noqa: PLC0415

    results = ScopedWriter(
        transport, client, authorize(WriteScope.SUB, changes, undo_file, "y", policy)
    ).apply()
    assert all(r.ok for r in results), results


@pytest.fixture
def undo_file(tmp_path):
    path = tmp_path / "undo.json"
    path.write_text("{}", encoding="utf-8")
    return path


# ------------------------------------------------------------ ported from the original tool
def test_roles_and_crossover_plan(client):
    state = read_console_state(client)
    roles = detect_roles(state)
    assert roles == Roles(sub=(3,), mains=(1, 2), others=(5, 6))
    assert state.matrices[3].outputs == (6,)
    assert state.matrices[1].outputs == (8,)

    plan = plan_crossover(state, roles, GRID, FS_HZ, fc_hz=100)

    assert plan.targets[3][5].type is EqType.LR24
    assert plan.targets[1][0].type is EqType.LR24
    assert plan.targets[3][0].gain_db == pytest.approx(4.5)  # sub voicing below fc/2 kept
    assert plan.targets[3][1].gain_db == 0  # 120 Hz boost removed
    assert plan.targets[3][3].gain_db == 0  # 1k39 pseudo low-pass removed
    changes = crossover_changes(state, plan.targets, POLICY)
    paths = [c.path for c in changes]
    last_filter = max(i for i, p in enumerate(paths) if p.endswith(("/type", "/f", "/q")))
    # Gains that go up (old cuts removed) are written only after the filters are in.
    by_path = {c.path: c for c in changes}
    for i, path in enumerate(paths):
        if path.endswith("/g") and by_path[path].new_value > by_path[path].old_value:
            assert i > last_filter
    for change in changes:
        POLICY.validate(change.path, change.new_value, WriteScope.SUB)


# ------------------------------------------------------------ state reading
def test_console_state_matches_the_dump(client):
    state = read_console_state(client)

    sub = state.matrices[3]
    assert sub.name == "Sub buffer"
    assert (sub.on, sub.fader_db, sub.invert) == (True, -37.5, False)
    assert sub.eq.on is True
    assert sub.eq.band(6).type is EqType.HSHV
    assert sub.eq.band(6).freq_hz == pytest.approx(751.7)
    assert sorted(state.matrices) == [1, 2, 3, 4, 5, 6]
    assert state.matrices[4].outputs == ()
    assert state.matrices[5].outputs == (4,)
    assert sorted(state.delays) == list(range(1, 17))
    assert state.delays[6].effective_ms == 0.0
    assert state.main_eq.band(1).type is EqType.BU18


def test_console_state_reading_never_writes_and_fails_loudly_when_the_console_is_silent(
    emu, client
):
    read_console_state(client)
    assert emu.writes == []
    assert emu.unknown == []

    emu.drop_next = 10**6

    class Impatient:
        def node(self, path):
            return client.node(path, timeout_s=0.01)

    with pytest.raises(ConsoleUnreachable):
        read_console_state(Impatient())


# ------------------------------------------------------------ full flow
def measure_junction(emu, client, backend):
    """Three blocks in one stream: sub only, tops only, everything; mutes switched in between."""
    state = read_console_state(client)
    roles = detect_roles(state)
    plan = mute_plan(state, roles)
    stimulus = block_stimulus(FS_HZ, SWEEP, JUNCTION_LEVEL_DBFS)

    def switch(states):
        def action():
            for matrix, on in states.items():
                emu.mtx[matrix]["on"] = int(on)

        return action

    switch(plan.sub_only)()
    try:
        rec, _ = backend.playrec(
            stimulus.out,
            events=[
                (stimulus.block_len, switch(plan.tops_only)),
                (2 * stimulus.block_len, switch(plan.original)),
            ],
        )
    finally:
        switch(plan.original)()
    sub, tops, both = split_irs(rec.astype(np.float64), stimulus, FS_HZ, SWEEP)
    return JunctionPoint(sub, tops, both)


def test_sub_full_flow_improves_crossover(emu, transport, client, undo_file):
    # A dry-ish hall (direct sound dominates) so the alignment problem has a clear answer; the
    # sub arrives together with the tops, so LR24 leaves a phase mismatch for delay or polarity.
    tops, sub = speaker_rooms(FS_HZ, drr_db=20.0, sub_extra_ms=0.0)
    backend = ConsoleRoomBackend(
        lambda: read_console_state(client), FS_HZ, sub_matrix=SUB_MATRIX, tops=tops, sub=sub
    )
    original_mutes = {n: emu.mtx[n]["on"] for n in emu.mtx}

    state = read_console_state(client)
    roles = detect_roles(state)
    crossover = plan_crossover(state, roles, GRID, FS_HZ)
    apply(transport, client, crossover_changes(state, crossover.targets, POLICY), undo_file)
    assert emu.mtx[3]["bands"][6]["type"] == P.EQ_TYPES.index(EqType.LR24)

    before = analyze_alignment({"p1": measure_junction(emu, client, backend)}, 100.0, FS_HZ)
    assert {n: emu.mtx[n]["on"] for n in emu.mtx} == original_mutes, "mutes not restored"

    state = read_console_state(client)
    changes = alignment_changes(
        state, roles, UNVERIFIED_OK, delay_ms=before.delay_ms, polarity=before.polarity
    )
    assert changes, "the simulated junction needs a correction"
    apply(transport, client, changes, undo_file, UNVERIFIED_OK)

    after = analyze_alignment({"p1": measure_junction(emu, client, backend)}, 100.0, FS_HZ)

    p0, p1 = before.points["p1"], after.points["p1"]
    assert p1.all_measured_db.min() > p0.all_measured_db.min() + 6
    # The measured result matches the prediction made before applying.
    assert np.sqrt(np.mean((p1.all_measured_db - p0.sum_best_db) ** 2)) < 1.5
    assert emu.writes
    assert emu.foreign_writes(("/mtx/", "/outputs/main/")) == []
    assert p0.check_mismatch_db < 1.0  # the "both" block agrees with sub + tops


def test_level_recommendation_closes_the_gap_between_sub_and_tops(
    emu, transport, client, undo_file
):
    tops, sub = speaker_rooms(FS_HZ, drr_db=20.0, sub_extra_ms=0.0)
    backend = ConsoleRoomBackend(
        lambda: read_console_state(client), FS_HZ, sub_matrix=SUB_MATRIX, tops=tops, sub=sub
    )
    state = read_console_state(client)
    roles = detect_roles(state)
    crossover = plan_crossover(state, roles, GRID, FS_HZ)
    apply(transport, client, crossover_changes(state, crossover.targets, POLICY), undo_file)

    first = analyze_alignment({"p1": measure_junction(emu, client, backend)}, 100.0, FS_HZ)
    change_db = recommend_level(first)
    assert change_db != 0.0
    state = read_console_state(client)
    changes = alignment_changes(
        state, roles, POLICY, delay_ms=0.0, polarity=1, fader_change_db=change_db
    )
    assert [c.path for c in changes] == ["/mtx/03/mix/fader"]
    apply(transport, client, changes, undo_file)

    second = analyze_alignment({"p1": measure_junction(emu, client, backend)}, 100.0, FS_HZ)

    assert abs(second.sub_minus_tops_at_fc_db) < abs(first.sub_minus_tops_at_fc_db)
    assert second.sub_minus_tops_at_fc_db == pytest.approx(
        first.sub_minus_tops_at_fc_db + change_db, abs=0.3
    )
