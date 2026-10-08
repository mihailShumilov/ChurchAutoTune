"""Console state reading (against a dictionary of node lines), roles and the junction plan."""

import math

import numpy as np
import pytest

from churchtune.domain.console import ConsoleState, MatrixState, OutputDelay, Roles
from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.errors import ConsoleUnreachable, LevelError
from churchtune.measure.config import SweepConfig
from churchtune.measure.junction import block_stimulus, mute_plan, split_irs
from churchtune.mixers.x32.nodes import (
    parse_delay_line,
    parse_mix_line,
    read_console_state,
)
from churchtune.mixers.x32.topology import detect_roles

FS_HZ = 48000.0
FLAT = SystemEq(True, tuple(Band(i, EqType.PEQ, 1000.0, 0.0, 2.0) for i in range(1, 7)))


class Nodes:
    """A reader that answers from a dictionary; missing nodes are unanswered."""

    def __init__(self, **overrides):
        self.lines = {"main/st/eq": "/main/st/eq ON"}
        for band in range(1, 7):
            self.lines[f"main/st/eq/{band}"] = f"/main/st/eq/{band} PEQ 1k00 +0.00 2.0"
        for n in range(1, 7):
            prefix = f"mtx/{n:02d}"
            self.lines[f"{prefix}/config"] = f'/{prefix}/config "Mtx {n} " 66 MG'
            self.lines[f"{prefix}/eq"] = f"/{prefix}/eq ON"
            self.lines[f"{prefix}/mix"] = f"/{prefix}/mix ON -10.0"
            self.lines[f"{prefix}/preamp"] = f"/{prefix}/preamp OFF"
            for band in range(1, 7):
                self.lines[f"{prefix}/eq/{band}"] = f"/{prefix}/eq/{band} PEQ 1k00 +0.00 2.0"
        for out in range(1, 17):
            self.lines[f"outputs/main/{out:02d}"] = f"/outputs/main/{out:02d} 0 POST OFF"
            self.lines[f"outputs/main/{out:02d}/delay"] = f"/outputs/main/{out:02d}/delay OFF   0.3"
        for key, line in overrides.items():
            path = key.replace("__", "/")
            if line is None:
                del self.lines[path]
            else:
                self.lines[path] = line

    def node(self, path):
        return self.lines.get(path)


# ------------------------------------------------------------ line parsers
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("/mtx/03/mix ON -37.5", (True, -37.5)),
        ("/mtx/03/mix OFF -oo", (False, -math.inf)),
        ("/main/st/mix ON  +2.0 -2", (True, 2.0)),
    ],
)
def test_parse_mix_line(line, expected):
    assert parse_mix_line(line) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("/outputs/main/06/delay OFF   0.3", (False, 0.3)),
        ("/outputs/main/04/delay ON 12.5", (True, 12.5)),
    ],
)
def test_parse_delay_line(line, expected):
    assert parse_delay_line(line) == expected


@pytest.mark.parametrize("line", ["/mtx/03/mix ON", "/mtx/03/mix", ""])
def test_mix_and_delay_parsers_reject_short_lines(line):
    with pytest.raises(ValueError, match="not a mix line"):
        parse_mix_line(line)
    with pytest.raises(ValueError, match="not a delay line"):
        parse_delay_line(line)


# ------------------------------------------------------------ state
def test_state_maps_main_outputs_to_the_matrices_that_feed_them():
    reader = Nodes(
        outputs__main__06="/outputs/main/06 22 POST OFF",  # source 22 = matrix 3
        outputs__main__08="/outputs/main/08 20 POST OFF",  # source 20 = matrix 1
        outputs__main__09="/outputs/main/09 20 POST OFF",
        outputs__main__01="/outputs/main/01 4 POST OFF",  # not a matrix
        outputs__main__02="/outputs/main/02 26 POST OFF",  # past matrix 6
    )

    state = read_console_state(reader)

    assert state.matrices[3].outputs == (6,)
    assert state.matrices[1].outputs == (8, 9)
    assert all(state.matrices[n].outputs == () for n in (2, 4, 5, 6))


def test_state_reads_names_switches_faders_polarity_and_delays():
    reader = Nodes(
        mtx__03__config='/mtx/03/config "Sub buffer " 25 RD',
        mtx__03__mix="/mtx/03/mix OFF -oo",
        mtx__03__preamp="/mtx/03/preamp ON",
        mtx__03__eq="/mtx/03/eq OFF",
        mtx__03__eq__6="/mtx/03/eq/6 LR24 101.4 -15.0 2.0",
        outputs__main__04__delay="/outputs/main/04/delay ON   12.5",
    )

    state = read_console_state(reader)

    sub = state.matrices[3]
    assert sub.name == "Sub buffer"
    assert (sub.on, sub.fader_db, sub.invert, sub.eq.on) == (False, -math.inf, True, False)
    assert sub.eq.band(6) == Band(6, EqType.LR24, 101.4, -15.0, 2.0)
    assert state.delays[4] == OutputDelay(4, True, 12.5)
    assert state.delays[4].effective_ms == 12.5
    assert state.delays[6].effective_ms == 0.0


def test_state_tolerates_unanswered_optional_nodes():
    reader = Nodes(
        mtx__02__config=None,
        mtx__02__eq=None,
        mtx__02__preamp=None,
        outputs__main__05=None,
        outputs__main__05__delay=None,
    )

    state = read_console_state(reader)

    assert state.matrices[2].name == ""
    assert state.matrices[2].eq.on is True
    assert state.matrices[2].invert is False
    assert 5 not in state.delays


@pytest.mark.parametrize("missing", ["mtx__03__mix", "mtx__03__eq__4", "main__st__eq"])
def test_state_needs_the_band_mix_and_main_eq_nodes(missing):
    with pytest.raises(ConsoleUnreachable):
        read_console_state(Nodes(**{missing: None}))


# ------------------------------------------------------------ roles
def _state(names_and_outputs):
    matrices = {
        n: MatrixState(n, name, FLAT, True, 0.0, False, outputs)
        for n, (name, outputs) in names_and_outputs.items()
    }
    return ConsoleState(FLAT, matrices, {})


REFERENCE = {
    1: ("Main L", (8,)),
    2: ("Main R", (7,)),
    3: ("Sub buffer", (6,)),
    4: ("", ()),
    5: ("Balcon L", (4,)),
    6: ("Balcon R", (3,)),
}


def test_roles_come_from_names_and_outputs():
    assert detect_roles(_state(REFERENCE)) == Roles(sub=(3,), mains=(1, 2), others=(5, 6))


@pytest.mark.parametrize("name", ["SUB", "Subs L+R", "саб", "Сабвуфер"])
def test_sub_is_recognised_in_several_spellings(name):
    names = {**REFERENCE, 3: (name, (6,))}

    assert detect_roles(_state(names)).sub == (3,)


def test_a_matrix_without_an_output_has_no_role():
    names = {**REFERENCE, 4: ("Sub spare", ()), 5: ("Main spare", ())}

    roles = detect_roles(_state(names))

    assert roles == Roles(sub=(3,), mains=(1, 2), others=(6,))


def test_explicit_roles_override_the_names():
    roles = detect_roles(_state(REFERENCE), sub=[5], mains=[1], others=[])

    assert roles == Roles(sub=(5,), mains=(1,), others=())
    assert roles.all == (5, 1)


def test_partial_override_keeps_detecting_the_rest():
    roles = detect_roles(_state(REFERENCE), sub=[6])

    assert roles == Roles(sub=(6,), mains=(1, 2), others=(3, 5))


@pytest.mark.parametrize(
    "names",
    [
        {1: ("Left", (8,)), 3: ("Sub", (6,))},
        {1: ("Main L", (8,)), 3: ("Low", (6,))},
        {1: ("Main L", ()), 3: ("Sub", (6,))},
    ],
    ids=["no-mains", "no-sub", "mains-without-output"],
)
def test_roles_that_cannot_be_told_are_an_error(names):
    with pytest.raises(ValueError, match="name them explicitly"):
        detect_roles(_state(names))


# ------------------------------------------------------------ junction measurement
def test_mute_plan_isolates_sub_then_tops_and_mutes_the_others_in_both():
    state = _state(REFERENCE)

    plan = mute_plan(state, Roles(sub=(3,), mains=(1, 2), others=(5, 6)))

    assert plan.original == {3: True, 1: True, 2: True, 5: True, 6: True}
    assert plan.sub_only == {3: True, 1: False, 2: False, 5: False, 6: False}
    assert plan.tops_only == {3: False, 1: True, 2: True, 5: False, 6: False}


def test_mute_plan_never_unmutes_a_matrix_that_was_muted():
    state = _state(REFERENCE)
    muted = {**state.matrices, 2: MatrixState(2, "Main R", FLAT, False, 0.0, False, (7,))}

    plan = mute_plan(ConsoleState(FLAT, muted, {}), Roles(sub=(3,), mains=(1, 2)))

    assert plan.original[2] is False
    assert plan.tops_only[2] is False
    assert plan.sub_only[2] is False


def test_block_stimulus_repeats_one_block_and_keeps_its_reference():
    sweep = SweepConfig(duration_s=1.0, tail_s=0.5)

    stimulus = block_stimulus(FS_HZ, sweep, -17.0)

    assert stimulus.block_len == int(2.0 * FS_HZ)
    assert stimulus.out.shape == (3 * stimulus.block_len, 2)
    assert stimulus.out.dtype == np.float32
    for i in (1, 2):
        np.testing.assert_array_equal(
            stimulus.out[: stimulus.block_len],
            stimulus.out[i * stimulus.block_len : (i + 1) * stimulus.block_len],
        )
    np.testing.assert_array_equal(stimulus.ref, stimulus.out[: stimulus.block_len, 0])
    assert np.max(np.abs(stimulus.out)) == pytest.approx(10 ** (-17 / 20), rel=1e-3)


def test_block_stimulus_refuses_a_level_above_the_hard_limit():
    with pytest.raises(LevelError):
        block_stimulus(FS_HZ, SweepConfig(), -3.0)


def test_split_irs_gives_one_response_per_block_on_the_blocks_own_time_base():
    sweep = SweepConfig(duration_s=1.0, tail_s=0.5)
    stimulus = block_stimulus(FS_HZ, sweep, -17.0)
    # Block A delayed by 100 samples, block B by 300 samples and inverted, block C silent.
    rec = np.zeros(3 * stimulus.block_len + 77)
    rec[100 : 100 + stimulus.block_len] += stimulus.ref
    rec[stimulus.block_len + 300 : 2 * stimulus.block_len] -= stimulus.ref[
        : stimulus.block_len - 300
    ]

    irs = split_irs(rec, stimulus, FS_HZ, sweep)

    assert len(irs) == 3  # the 77 extra samples are not a block
    assert int(np.argmax(np.abs(irs[0]))) == 100
    assert int(np.argmax(np.abs(irs[1]))) == 300
    assert irs[1][300] < 0 < irs[0][100]
    assert np.max(np.abs(irs[2])) < 1e-9


@pytest.mark.parametrize(
    "override",
    [{"sub": [4]}, {"sub": [7]}, {"mains": [0, 1]}, {"others": [4]}],
    ids=["no-output", "no-such-matrix", "zero", "other-without-output"],
)
def test_a_role_cannot_be_given_to_a_matrix_that_feeds_no_output(override):
    """An override names what the plan may write, so it must name a real, connected matrix."""
    with pytest.raises(ValueError, match="feed no physical output"):
        detect_roles(_state(REFERENCE), **override)
