"""The optimiser's commonness rule judges a band where it acts, not over a fixed wide zone."""

import numpy as np
import pytest
from scipy import signal

from churchtune.analysis.points import PointResponse, analyze_points
from churchtune.audio.fake.room import synthetic_room
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
