"""The optimiser's commonness rule judges a band where it acts, not over a fixed wide zone."""

import numpy as np
import pytest
from scipy import signal

from churchtune.analysis.points import PointResponse, analyze_points
from churchtune.audio.fake.room import synthetic_room
from churchtune.design import system_eq_opt
from churchtune.design.system_eq_opt import band_half_oct, optimize_design
from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.domain.eq_model import band_sos
from churchtune.mixers.x32.params import X32EqGrid

FS = 48000.0
FLAT = SystemEq(True, tuple(Band(n, EqType.PEQ, 1000.0, 0.0, 1.0) for n in range(1, 7)))
RESONANCE = Band(2, EqType.PEQ, 400.0, 9.0, 6.0)


def hall(with_resonance: int) -> dict[str, PointResponse]:
    sos = band_sos(RESONANCE, FS)
    points = {}
    for i in range(8):
        room = synthetic_room(np.random.default_rng(300 + i), FS, length_s=1.0, drr_db=14.0)
        if i < with_resonance and sos is not None:
            room = signal.sosfilt(sos, room)
        points[f"p{i + 1}"] = PointResponse(np.asarray(room), FS)
    return points


def cut_near(proposal, freq_hz: float) -> bool:
    return any(
        b.band.gain_db < -0.5 and abs(np.log2(b.band.freq_hz / freq_hz)) < 1 / 3
        for b in proposal.bands
    )


def test_a_narrow_resonance_in_every_seat_is_cut():
    proposal = optimize_design(analyze_points(hall(8)), FLAT, X32EqGrid(), FS)
    assert cut_near(proposal, 400.0)


def test_the_same_resonance_in_three_seats_of_eight_is_left_alone():
    proposal = optimize_design(analyze_points(hall(3)), FLAT, X32EqGrid(), FS)
    assert not cut_near(proposal, 400.0)


@pytest.mark.parametrize(("q", "half"), [(0.5, 1 / 3), (1.5, 1 / 3), (6.0, 1 / 12), (3.0, 1 / 6)])
def test_the_zone_follows_the_band_width(q, half):
    assert band_half_oct(q) == pytest.approx(half)


def test_a_single_resonance_gets_a_single_cut():
    """Splitting one cut into stacked halves must not win (DSP review of the reseed step)."""
    proposal = optimize_design(analyze_points(hall(8)), FLAT, X32EqGrid(), FS)
    near = [b.band for b in proposal.bands if b.band.gain_db < -0.5]
    near = [b for b in near if abs(np.log2(b.freq_hz / 400.0)) < 1 / 3]
    assert len(near) == 1
    assert near[0].gain_db >= -9.0


@pytest.mark.parametrize("seed", range(4))
def test_putting_spare_bands_to_work_never_makes_the_proposal_worse(seed, monkeypatch):
    rng = np.random.default_rng(900 + seed)
    resonance = Band(2, EqType.PEQ, float(rng.uniform(150, 2000)), float(rng.uniform(4, 9)), 4.0)
    sos = band_sos(resonance, FS)
    points = {}
    for i in range(6):
        room = synthetic_room(
            np.random.default_rng(500 + 10 * seed + i), FS, length_s=1.0, drr_db=14.0
        )
        points[f"p{i + 1}"] = PointResponse(np.asarray(signal.sosfilt(sos, room)), FS)
    analysis = analyze_points(points)
    with_reseed = optimize_design(analysis, FLAT, X32EqGrid(), FS).prediction
    monkeypatch.setattr(system_eq_opt, "RESEED_ROUNDS", 0)
    plain = optimize_design(analysis, FLAT, X32EqGrid(), FS).prediction
    better_peak = with_reseed.max_dev_after_db <= plain.max_dev_after_db - 1.0
    assert with_reseed.rms_dev_after_db <= plain.rms_dev_after_db + 0.01 or better_peak


def test_the_optimisation_is_deterministic():
    analysis = analyze_points(hall(8))
    first = optimize_design(analysis, FLAT, X32EqGrid(), FS)
    assert optimize_design(analysis, FLAT, X32EqGrid(), FS) == first
