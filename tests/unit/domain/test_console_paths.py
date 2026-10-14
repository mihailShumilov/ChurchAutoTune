"""Snapshot to design paths: the same state as the M0 reader, roles only for matrix layouts."""

import pytest

from churchtune.domain.console_paths import console_state, matrix_roles
from churchtune.domain.topology import SpeakerRole, with_roles
from churchtune.mixers.x32.nodes import read_console_state
from churchtune.mixers.x32.topology import infer_topology
from tests.fixtures.topologies import TOPOLOGIES, all_fixtures, load


class DumpReader:
    def __init__(self, raw):
        self.raw = raw

    def node(self, path):
        return self.raw.get(path)


@pytest.mark.parametrize("fixture", all_fixtures(), ids=lambda f: f.name)
def test_state_from_a_snapshot_equals_the_m0_reader(fixture):
    from_snapshot = console_state(fixture.snapshot())
    from_nodes = read_console_state(DumpReader(fixture.raw))
    assert from_snapshot.main_eq == from_nodes.main_eq
    assert from_snapshot.delays == from_nodes.delays
    for number, matrix in from_nodes.matrices.items():
        assert from_snapshot.matrices[number] == matrix


def test_roles_of_the_reference_hall():
    fixture = load(TOPOLOGIES / "hall-2026-10-02.toml")
    roles = matrix_roles(infer_topology(fixture.snapshot()))
    assert roles is not None
    assert roles.sub == (3,)
    assert roles.mains == (1, 2)


@pytest.mark.parametrize("name", ["sub-on-bus", "sub-on-mono", "no-sub", "tops-on-main"])
def test_no_matrix_roles_when_a_path_is_not_a_matrix(name):
    fixture = load(TOPOLOGIES / f"{name}.toml")
    assert matrix_roles(infer_topology(fixture.snapshot())) is None


def test_confirmed_roles_replace_the_guess():
    fixture = load(TOPOLOGIES / "hall-2026-10-02.toml")
    guess = infer_topology(fixture.snapshot())
    sub_output = next(g for g in guess.outputs if g.role is SpeakerRole.SUB)
    key = f"{sub_output.kind.value} {sub_output.number}"
    confirmed = with_roles(guess, {key: "unused"})
    assert confirmed.subs == ()
    assert confirmed.tops == guess.tops
    assert next(g for g in confirmed.outputs if g.number == sub_output.number).confidence == 1.0
    assert with_roles(guess, {}).subs == guess.subs
