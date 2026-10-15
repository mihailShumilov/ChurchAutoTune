"""The findings and the decay numbers have exact answers on synthetic rooms (DSP review of M6)."""

import numpy as np
import pytest

from churchtune.analysis.points import Analysis, DecayStat, RoomDecay
from churchtune.analysis.response import GRID_HZ
from churchtune.analysis.rt60 import BandDecay
from churchtune.report import findings as f
from churchtune.report.session_json import THIRDS_HZ, thirds_db


def stat(value: float | None) -> DecayStat:
    return DecayStat(mean_s=value, n_points=0 if value is None else 1, sd_s=None)


def room(edt: float | None, t20: float | None) -> RoomDecay:
    return RoomDecay(edt=stat(edt), t20=stat(t20), t30=stat(None))


def band(edt: float | None, t20: float | None) -> BandDecay:
    return BandDecay(edt_s=edt, t20_s=t20, t30_s=None, curvature_pct=None, dynamic_db=60.0)


def analysis(avg_db=None, *, spread_db=None, rt60=None, rt60_points=None, f_low_hz=60.0):
    avg = np.zeros_like(GRID_HZ) if avg_db is None else avg_db
    spread = np.zeros_like(GRID_HZ) if spread_db is None else spread_db
    return Analysis(grid_hz=GRID_HZ, raw_db={"A": avg}, norm_db={"A": avg}, avg_db=avg,
                    spread_db=spread, min_db=avg, max_db=avg, rt60_points=rt60_points or {},
                    rt60=rt60 or {}, f_low_hz=f_low_hz)  # fmt: skip


def gaussian(centre_hz: float, gain_db: float, octaves: float = 0.15):
    return gain_db * np.exp(-0.5 * (np.log2(GRID_HZ / centre_hz) / octaves) ** 2)


def humps(an: Analysis, target_db=None):
    return [m for m in f.findings(an, target_db) if m.key.startswith("finding-hump")]


def test_a_flat_response_has_no_hump():
    assert humps(analysis()) == []


def test_a_gentle_shelf_at_the_top_edge_is_not_a_hump():
    shelf = np.where(GRID_HZ > 2500, 4.0, 0.0)
    assert humps(analysis(shelf)) == []


def test_a_level_offset_against_the_target_is_not_a_hump():
    assert humps(analysis(np.full_like(GRID_HZ, 5.0))) == []


def test_a_bump_at_500_hz_is_boxy_with_its_height():
    [hump] = humps(analysis(gaussian(500, 6.0)))
    assert hump.key == "finding-hump-boxy"
    assert hump.args["freq_hz"] == pytest.approx(500, rel=0.03)
    # The bump also lifts the 500 Hz - 2 kHz mean the curve is aligned to.
    assert hump.args["gain_db"] == pytest.approx(6.0, abs=1.0)


def test_a_bump_where_the_places_disagree_is_not_reported():
    spread = np.where((GRID_HZ > 350) & (GRID_HZ < 700), 4.0, 0.0)
    assert humps(analysis(gaussian(500, 6.0), spread_db=spread)) == []


def test_the_shoulder_of_a_top_where_the_places_disagree_is_not_reported():
    spread = np.where(np.abs(np.log2(GRID_HZ / 500)) <= 1 / 6, 5.0, 1.0)
    assert humps(analysis(gaussian(500, 6.0), spread_db=spread)) == []


def test_without_any_level_in_the_mids_there_is_no_hump():
    curve = gaussian(300, 6.0)
    curve[(GRID_HZ >= 500) & (GRID_HZ <= 2000)] = np.nan
    assert humps(analysis(curve)) == []


def test_a_bump_below_the_speakers_range_is_not_reported():
    assert humps(analysis(gaussian(100, 8.0), f_low_hz=150.0)) == []


def test_nan_in_the_curve_does_not_break_the_search():
    curve = gaussian(1500, 6.0)
    curve[GRID_HZ < 90] = np.nan
    [hump] = humps(analysis(curve))
    assert hump.args["freq_hz"] == pytest.approx(1500, rel=0.03)


@pytest.mark.parametrize(
    ("freq_hz", "key"),
    [(299.9, "finding-hump"), (300.0, "finding-hump-boxy"), (1999.9, "finding-hump-boxy"),
     (2000.0, "finding-hump-sharp")],
)  # fmt: skip
def test_hump_names_change_at_the_boundaries(freq_hz, key):
    assert f._hump_key(freq_hz) == key


def test_the_spread_uses_100_hz_to_4_khz_only():
    spread = np.where((GRID_HZ >= 100) & (GRID_HZ <= 4000), 2.0, 10.0)
    assert f.mid_spread_db(analysis(spread_db=spread)) == pytest.approx(2.0)


def test_the_bass_ratio_is_exact():
    rt60 = {125: room(None, 2.0), 250: room(None, 2.0), 500: room(None, 1.0), 1000: room(None, 1.0)}
    assert f.bass_ratio(analysis(rt60=rt60)) == pytest.approx(2.0)


def test_the_bass_ratio_needs_all_four_octaves():
    rt60 = {125: room(None, 2.0), 500: room(None, 1.0), 1000: room(None, 1.0)}
    assert f.bass_ratio(analysis(rt60=rt60)) is None
    assert f.bass_ratio(analysis()) is None


def test_the_edt_ratio_needs_a_pair_at_one_point():
    assert f.edt_ratio(analysis()) is None
    points = {"A": {500: band(None, 1.0), 1000: band(0.9, None)}}
    assert f.edt_ratio(analysis(rt60_points=points)) is None


def test_the_edt_ratio_is_taken_point_by_point():
    points = {"A": {500: band(0.5, 1.0)}, "B": {500: band(3.0, 2.0)}}
    assert f.edt_ratio(analysis(rt60_points=points)) == pytest.approx((0.5 + 1.5) / 2)


def test_the_edt_shown_is_the_measured_one():
    rt60 = {500: room(None, 1.0), 1000: room(1.2, 2.0)}
    assert f.mid_edt_s(analysis(rt60=rt60)) == pytest.approx(1.2)


def test_the_rt60_verdict_agrees_with_the_number_shown():
    rt60 = {500: room(None, 0.7996), 1000: room(None, 0.7996)}
    an = analysis(rt60=rt60)
    assert f.mid_rt60_s(an) == 0.8
    [msg] = [m for m in f.findings(an) if m.key.startswith("finding-rt60")]
    assert msg.key == "finding-rt60-good"
    assert msg.args["rt60_s"] == 0.8


def test_thirds_of_a_constant_are_that_constant_where_measured():
    values = thirds_db(GRID_HZ, np.full_like(GRID_HZ, -3.0))
    assert len(values) == len(THIRDS_HZ)
    assert all(v == pytest.approx(-3.0) for v in values if v is not None)
    assert values[THIRDS_HZ.index(1000)] is not None


def test_thirds_average_levels_as_power_and_spreads_as_numbers():
    curve = np.where(np.arange(GRID_HZ.size) % 2 == 0, 0.0, -100.0)
    k = THIRDS_HZ.index(1000)
    assert thirds_db(GRID_HZ, curve)[k] == pytest.approx(10 * np.log10(0.5), abs=0.1)
    assert thirds_db(GRID_HZ, curve, energy=False)[k] == pytest.approx(-50.0, abs=7.0)
