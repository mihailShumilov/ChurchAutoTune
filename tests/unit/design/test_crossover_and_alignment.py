"""Crossover plan and alignment search on synthetic states and impulse responses."""

import numpy as np
import pytest

from churchtune.design.alignment import (
    AlignmentResult,
    JunctionPoint,
    analyze_alignment,
    recommend_level,
)
from churchtune.design.crossover import NoteCode, plan_crossover
from churchtune.domain.console import ConsoleState, MatrixState, Roles
from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.mixers.x32.params import X32EqGrid

FS_HZ = 48000.0
GRID = X32EqGrid()
ROLES = Roles(sub=(3,), mains=(1, 2), others=(5,))


def flat_bands(**overrides):
    bands = [Band(i, EqType.PEQ, 1000.0, 0.0, 2.0) for i in range(1, 7)]
    for number, band in overrides.items():
        bands[int(number[1:]) - 1] = band
    return tuple(bands)


def state(sub_bands=None, top_bands=None):
    def matrix(n, bands):
        return MatrixState(
            n, f"m{n}", SystemEq(True, bands or flat_bands()), True, 0.0, False, (n,)
        )

    matrices = {1: matrix(1, top_bands), 2: matrix(2, top_bands), 3: matrix(3, sub_bands)}
    matrices[5] = matrix(5, None)
    return ConsoleState(SystemEq(True, flat_bands()), matrices, {})


# ------------------------------------------------------------ crossover
def test_crossover_puts_a_low_pass_on_the_sub_and_a_high_pass_on_the_tops():
    plan = plan_crossover(state(), ROLES, GRID, FS_HZ)

    assert plan.fc_hz == pytest.approx(101.4, abs=0.05)  # 100 Hz snapped to the console grid
    assert plan.slope is EqType.LR24
    assert sorted(plan.targets) == [1, 2, 3]  # the "other" matrix is not filtered
    assert plan.targets[3][5] == Band(6, EqType.LR24, plan.fc_hz, 0.0, 2.0)
    assert plan.targets[1][0] == Band(1, EqType.LR24, plan.fc_hz, 0.0, 2.0)
    assert plan.targets[3][:5] == flat_bands()[:5]
    assert plan.targets[2][1:] == flat_bands()[1:]
    assert [n.code for n in plan.notes] == [
        NoteCode.SUB_LOW_PASS,
        NoteCode.TOP_HIGH_PASS,
        NoteCode.TOP_HIGH_PASS,
    ]


def test_crossover_cleans_up_the_sub_eq_around_and_above_the_crossover():
    sub = flat_bands(
        b1=Band(1, EqType.PEQ, 40.0, 4.5, 2.0),  # voicing below fc/2: kept
        b2=Band(2, EqType.PEQ, 120.0, 3.0, 2.0),  # boost in the zone: removed
        b3=Band(3, EqType.PEQ, 90.0, -3.0, 2.0),  # cut in the zone: kept
        b4=Band(4, EqType.PEQ, 1390.0, -15.0, 2.0),  # pseudo low-pass: removed
        b5=Band(5, EqType.HSHV, 3000.0, 2.0, 2.0),  # boost above the sub's range: removed
        b6=Band(6, EqType.HSHV, 751.7, -15.0, 2.0),  # becomes the real low-pass
    )

    plan = plan_crossover(state(sub_bands=sub), ROLES, GRID, FS_HZ)

    target = plan.targets[3]
    assert [b.gain_db for b in target] == [4.5, 0.0, -3.0, 0.0, 0.0, -15.0]
    assert target[5].type is EqType.LR24
    assert target[5].freq_hz == plan.fc_hz
    sub_notes = [(n.code, n.band.band) for n in plan.notes if n.matrix == 3]
    assert sub_notes == [
        (NoteCode.BOOST_IN_ZONE_REMOVED, 2),
        (NoteCode.FAKE_LOW_PASS_REMOVED, 4),
        (NoteCode.BOOST_ABOVE_SUB_REMOVED, 5),
        (NoteCode.SUB_LOW_PASS, 6),
    ]


def test_crossover_ignores_inactive_and_cut_filter_bands_of_the_sub():
    sub = flat_bands(
        b2=Band(2, EqType.PEQ, 120.0, 0.2, 2.0),  # below the "in use" threshold
        b3=Band(3, EqType.BU12, 3000.0, 0.0, 2.0),  # a cut filter is not touched
    )

    plan = plan_crossover(state(sub_bands=sub), ROLES, GRID, FS_HZ)

    assert plan.targets[3][1:3] == sub[1:3]


def test_crossover_removes_cuts_that_imitated_a_high_pass_on_the_tops():
    tops = flat_bands(
        b1=Band(1, EqType.LCUT, 112.5, 0.0, 2.0),
        b2=Band(2, EqType.VEQ, 90.0, -14.7, 2.0),  # below 1.5 fc: removed
        b3=Band(3, EqType.PEQ, 400.0, -4.0, 2.0),  # above: kept
        b4=Band(4, EqType.PEQ, 80.0, 2.0, 2.0),  # a boost is not an imitation of a high-pass
    )

    plan = plan_crossover(state(top_bands=tops), ROLES, GRID, FS_HZ)

    target = plan.targets[1]
    assert target[0].type is EqType.LR24
    assert [b.gain_db for b in target[1:4]] == [0.0, -4.0, 2.0]
    assert (NoteCode.FAKE_HIGH_PASS_REMOVED, 2) in [(n.code, n.band.band) for n in plan.notes]


def test_crossover_warns_when_the_new_high_pass_lets_more_lows_into_the_tops():
    # The old filter is steeper and higher than the new one: at fc/2 it was much further down.
    tops = flat_bands(b1=Band(1, EqType.BU24, 200.0, 0.0, 2.0))

    plan = plan_crossover(state(top_bands=tops), ROLES, GRID, FS_HZ, slope=EqType.BU12)

    warning = next(n for n in plan.notes if n.code is NoteCode.MORE_LOWS_TO_TOPS)
    # fc/2 = 50.7 Hz is 1.98 octaves below 200 Hz: BU24 is 24 * 1.98 = 47.5 dB down there;
    # BU12 at 101.4 Hz is about 12 dB down one octave below.
    assert warning.params["old_db"] == pytest.approx(-47.5, abs=0.5)
    assert warning.params["new_db"] == pytest.approx(-12.3, abs=0.5)
    assert warning.params["at_hz"] == pytest.approx(plan.fc_hz / 2)


def test_crossover_does_not_warn_when_the_new_high_pass_is_tighter():
    tops = flat_bands(b1=Band(1, EqType.BU12, 60.0, 0.0, 2.0))

    plan = plan_crossover(state(top_bands=tops), ROLES, GRID, FS_HZ)

    assert NoteCode.MORE_LOWS_TO_TOPS not in [n.code for n in plan.notes]


def test_crossover_frequency_and_slope_are_configurable_and_checked():
    plan = plan_crossover(state(), ROLES, GRID, FS_HZ, fc_hz=80.0, slope=EqType.BU18)

    assert plan.fc_hz == pytest.approx(GRID.snap_freq_hz(80.0))
    assert plan.targets[3][5].type is EqType.BU18
    with pytest.raises(ValueError, match="not a crossover slope"):
        plan_crossover(state(), ROLES, GRID, FS_HZ, slope=EqType.PEQ)


# ------------------------------------------------------------ alignment
def click(at_ms, level=1.0, n=1 << 15):
    ir = np.zeros(n)
    ir[round(at_ms * 1e-3 * FS_HZ)] = level
    return ir


def point(sub_at_ms, tops_at_ms=100.0, sub_level=1.0, both=None):
    sub, tops = click(sub_at_ms, sub_level), click(tops_at_ms)
    return JunctionPoint(sub, tops, sub + tops if both is None else both)


@pytest.mark.parametrize(
    ("sub_at_ms", "expected_delay_ms"),
    [(103.0, -3.0), (98.0, 2.0), (100.0, 0.0), (107.5, -7.5)],
    ids=["sub-late", "sub-early", "aligned", "sub-very-late"],
)
def test_alignment_finds_the_arrival_time_difference(sub_at_ms, expected_delay_ms):
    """Two identical clicks sum best when they coincide: the earlier path is delayed."""
    result = analyze_alignment({"p1": point(sub_at_ms)}, 100.0, FS_HZ)

    assert result.delay_ms == pytest.approx(expected_delay_ms, abs=0.1)
    assert result.polarity == 1
    # Coincident equal clicks sum to +6.02 dB over either one, flat across the zone.
    assert result.score_best_db == pytest.approx(6.02, abs=0.05)
    np.testing.assert_allclose(result.points["p1"].sum_best_db, 6.02, atol=0.05)


def test_alignment_detects_inverted_polarity():
    result = analyze_alignment({"p1": point(100.0, sub_level=-1.0)}, 100.0, FS_HZ)

    assert result.polarity == -1
    assert result.delay_ms == pytest.approx(0.0, abs=0.1)
    assert result.gain_db > 30  # from full cancellation to +6 dB
    assert result.points["p1"].min_now_db < -30


def test_alignment_prefers_no_change_when_nothing_is_gained():
    result = analyze_alignment({"p1": point(100.0)}, 100.0, FS_HZ)

    assert (result.delay_ms, result.polarity) == (0.0, 1)
    assert result.gain_db == pytest.approx(0.0, abs=1e-9)
    assert result.score_now_db == pytest.approx(result.score_best_db)


def test_alignment_averages_the_score_over_points():
    result = analyze_alignment(
        {"a": point(103.0), "b": point(103.0), "c": point(103.0)}, 100.0, FS_HZ
    )

    assert result.delay_ms == pytest.approx(-3.0, abs=0.1)
    assert sorted(result.points) == ["a", "b", "c"]


def test_alignment_reports_levels_relative_to_the_louder_path_and_the_difference_at_fc():
    result = analyze_alignment({"p1": point(100.0, sub_level=0.5)}, 100.0, FS_HZ)

    p = result.points["p1"]
    np.testing.assert_allclose(p.tops_db, 0.0, atol=1e-6)
    np.testing.assert_allclose(p.sub_db, -6.02, atol=0.01)
    assert result.sub_minus_tops_at_fc_db == -6.0
    assert len(result.centres_hz) == 13
    assert result.centres_hz[0] == pytest.approx(50.0)
    assert result.centres_hz[6] == pytest.approx(100.0)
    assert result.centres_hz[-1] == pytest.approx(200.0)


def test_alignment_check_compares_the_measured_sum_with_the_computed_one():
    consistent = analyze_alignment({"p1": point(100.0)}, 100.0, FS_HZ)
    # The "both" block shows only the tops: the sub mute did not switch back.
    stuck = analyze_alignment({"p1": point(100.0, both=click(100.0))}, 100.0, FS_HZ)

    assert consistent.points["p1"].check_mismatch_db == 0.0
    assert stuck.points["p1"].check_mismatch_db == pytest.approx(6.02, abs=0.05)


def test_alignment_search_range_and_step_are_configurable():
    result = analyze_alignment({"p1": point(103.0)}, 100.0, FS_HZ, max_delay_ms=5.0, step_ms=0.5)

    assert len(result.delays_ms) == 21
    assert result.delays_ms[0] == -5.0
    assert result.score_normal_db.shape == result.score_inverted_db.shape == (21,)
    assert result.delay_ms == -3.0


def test_alignment_needs_points():
    with pytest.raises(ValueError, match="no junction points"):
        analyze_alignment({}, 100.0, FS_HZ)


def _result(diff_db):
    empty = np.zeros(0)
    return AlignmentResult(100.0, empty, 0.0, 1, 0.0, 0.0, empty, empty, empty, {}, diff_db)


@pytest.mark.parametrize(
    ("diff_db", "change_db"),
    [(-8.6, 6.0), (-2.2, 2.2), (-0.9, 0.0), (0.9, 0.0), (3.0, -3.0), (9.0, -6.0), (-1.0, 1.0)],
)
def test_recommend_level_brings_the_sub_to_the_tops_within_a_cap(diff_db, change_db):
    assert recommend_level(_result(diff_db)) == pytest.approx(change_db)


def test_recommend_level_threshold_and_cap_are_configurable():
    assert recommend_level(_result(-8.6), cap_db=10.0) == pytest.approx(8.6)
    assert recommend_level(_result(-2.0), threshold_db=3.0) == 0.0
