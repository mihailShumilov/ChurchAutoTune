import math

import numpy as np
import pytest

from churchtune.analysis.points import PointResponse, analyze_points
from churchtune.analysis.response import GRID_HZ
from churchtune.design.proposal import (
    Action,
    DesignWarning,
    Method,
    ReasonCode,
    SkipCode,
    TargetSpec,
)
from churchtune.design.system_eq import design, design_warnings
from churchtune.design.system_eq_opt import optimize_design
from churchtune.design.target import EqDesignConfig, bandwidth_to_q, target_curve
from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.domain.eq_model import band_sos
from churchtune.mixers.x32.params import X32EqGrid

FS_HZ = 48000.0
GRID = X32EqGrid()
FLAT = SystemEq(True, tuple(Band(i, EqType.PEQ, 1000.0, 0.0, 2.0) for i in range(1, 7)))


def room_with(*filters, n_points=4):
    """Identical points whose response is a chain of known peaking filters."""
    from scipy import signal  # noqa: PLC0415

    ir = np.zeros(48000)
    ir[240] = 1.0
    for freq_hz, gain_db, q in filters:
        ir = signal.sosfilt(band_sos(Band(2, EqType.PEQ, freq_hz, gain_db, q), FS_HZ), ir)
    return analyze_points({f"p{i}": PointResponse(ir, FS_HZ) for i in range(1, n_points + 1)})


NO_TILT = TargetSpec(tilt_db_per_oct=0.0, bass_lift_db=0.0)


# ------------------------------------------------------------ target
def test_default_target_is_flat_to_1_khz_then_minus_1_db_per_octave_with_a_bass_shelf():
    spec = TargetSpec()
    target = target_curve(
        np.array([25.0, 100.0, 400.0, 1000.0, 2000.0, 8000.0]),
        spec.tilt_db_per_oct, spec.tilt_from_hz, spec.bass_lift_db, spec.bass_from_hz,
    )  # fmt: skip

    # Shelf: 3 / (1 + (f/100)^4) -> 2.99 at 25 Hz, exactly half at 100 Hz, 0.01 at 400 Hz.
    np.testing.assert_allclose(target, [2.988, 1.5, 0.0117, 0.0, -1.0, -3.0], atol=2e-3)


def test_target_without_tilt_and_lift_is_zero():
    target = target_curve(GRID_HZ, 0.0, 1000.0, 0.0, 100.0)

    assert np.array_equal(target, np.zeros_like(GRID_HZ))


@pytest.mark.parametrize(("bandwidth_oct", "q"), [(1.0, 1.4142), (2.0, 0.6667), (1 / 3, 4.3185)])
def test_bandwidth_to_q(bandwidth_oct, q):
    # Q = sqrt(2^N) / (2^N - 1).
    assert bandwidth_to_q(bandwidth_oct) == pytest.approx(q, abs=1e-3)


def test_bandwidth_to_q_floors_tiny_bandwidths():
    assert bandwidth_to_q(0.0) == bandwidth_to_q(0.05)
    assert math.isfinite(bandwidth_to_q(-1.0))


# ------------------------------------------------------------ design on known rooms
def test_a_flat_room_needs_no_change():
    proposal = design(room_with(), FLAT, GRID, FS_HZ, target=NO_TILT)

    assert proposal.method is Method.GREEDY
    assert [b.action for b in proposal.bands] == [Action.KEEP] * 6
    assert {b.reason.code for b in proposal.bands} == {ReasonCode.UNCHANGED_INACTIVE}
    assert proposal.to_eq() == FLAT
    assert proposal.skipped == ()
    assert proposal.prediction.rms_dev_before_db == pytest.approx(0.0, abs=0.01)
    assert proposal.n_points == 4


def test_a_common_peak_gets_a_cut_at_its_frequency():
    # 200 Hz lies outside the 500..2000 Hz band the points are normalised in, so the peak
    # keeps its full height in the analysis.
    proposal = design(room_with((200.0, 6.0, 1.5)), FLAT, GRID, FS_HZ, target=NO_TILT)

    changed = [b for b in proposal.bands if b.action is not Action.KEEP]
    assert len(changed) == 1
    band = changed[0]
    assert band.action is Action.NEW
    assert band.reason.code is ReasonCode.CUT
    assert band.reason.params["n"] == 4
    assert band.band.type is EqType.PEQ
    # A +6 dB, Q 1.5 peak at 200 Hz: the cut lands within a sixth of an octave and takes about
    # 85 % of it (the configured share), never more than the peak itself.
    assert abs(math.log2(band.band.freq_hz / 200.0)) < 1 / 6
    assert -6.0 <= band.band.gain_db <= -4.0
    assert proposal.prediction.max_dev_before_db == pytest.approx(6.0, abs=0.5)
    assert proposal.prediction.max_dev_after_db < 1.5


def test_a_narrow_dip_is_reported_and_never_filled():
    proposal = design(room_with((800.0, -12.0, 8.0)), FLAT, GRID, FS_HZ, target=NO_TILT)

    assert all(b.band.gain_db <= 0 for b in proposal.bands)
    # The notch and its skirt may be listed separately; both are narrow dips.
    assert proposal.skipped
    assert {s.reason.code for s in proposal.skipped} == {SkipCode.NARROW_DIP}
    assert abs(math.log2(proposal.skipped[0].freq_hz / 800.0)) < 1 / 12
    assert proposal.skipped[0].points == 4


def test_cut_filter_bands_are_locked_and_other_bands_do_the_work():
    eq = FLAT.with_band(Band(1, EqType.BU18, 23.0, 0.0, 2.0))

    proposal = optimize_design(room_with((500.0, 6.0, 1.5)), eq, GRID, FS_HZ, target=NO_TILT)

    assert proposal.method is Method.OPTIMIZE
    assert proposal.bands[0].band == eq.band(1)
    assert proposal.bands[0].reason.code is ReasonCode.LOCKED_CUT_FILTER
    cuts = [b for b in proposal.bands if b.band.gain_db < 0]
    assert cuts
    assert all(b.reason.code is ReasonCode.OPTIMIZED for b in cuts)
    assert proposal.prediction.max_dev_after_db < proposal.prediction.max_dev_before_db


def test_every_proposed_value_is_on_the_console_grid():
    proposal = optimize_design(
        room_with((300.0, 5.0, 2.0), (2500.0, 4.0, 1.0)), FLAT, GRID, FS_HZ, target=NO_TILT
    )

    changed = [b for b in proposal.bands if b.action is not Action.KEEP]
    assert changed
    for b in changed:
        assert GRID.snap_freq_hz(b.band.freq_hz) == pytest.approx(b.band.freq_hz)
        assert GRID.snap_gain_db(b.band.gain_db) == pytest.approx(b.band.gain_db)
        assert GRID.snap_q(b.band.q) == pytest.approx(b.band.q)


def test_limits_come_from_the_config():
    cfg = EqDesignConfig(max_cut_db=-3.0)

    proposal = design(room_with((200.0, 9.0, 1.5)), FLAT, GRID, FS_HZ, target=NO_TILT, cfg=cfg)

    assert min(b.band.gain_db for b in proposal.bands) == -3.0


def test_optimizer_without_adjustable_bands_changes_nothing():
    locked = SystemEq(True, tuple(Band(i, EqType.BU12, 100.0, 0.0, 2.0) for i in range(1, 7)))

    proposal = optimize_design(room_with((500.0, 6.0, 1.5)), locked, GRID, FS_HZ, target=NO_TILT)

    assert proposal.to_eq() == locked
    assert [b.action for b in proposal.bands] == [Action.KEEP] * 6


# ------------------------------------------------------------ warnings
def test_design_warnings():
    changed = FLAT.with_band(Band(2, EqType.PEQ, 500.0, -3.0, 2.0))

    assert design_warnings(FLAT, None) == ()
    assert design_warnings(FLAT, FLAT) == ()
    assert design_warnings(FLAT, changed) == (DesignWarning.EQ_CHANGED_SINCE_MEASUREMENT,)
    assert design_warnings(FLAT.with_on(False), None) == (DesignWarning.EQ_WAS_OFF,)


def test_proposal_switches_the_eq_on_and_records_both_eq_states():
    off = FLAT.with_on(False)

    proposal = design(room_with(), off, GRID, FS_HZ, target=NO_TILT, eq_now=FLAT)

    assert proposal.eq_on is True
    assert proposal.to_eq().on is True
    assert proposal.eq_at_measurement is off
    assert proposal.eq_current is FLAT
    assert set(proposal.warnings) == {
        DesignWarning.EQ_CHANGED_SINCE_MEASUREMENT,
        DesignWarning.EQ_WAS_OFF,
    }
