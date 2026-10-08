"""Response, RT60 and point analysis on synthetic signals with analytically known answers."""

import math

import numpy as np
import pytest
from scipy import signal

from churchtune.analysis.bands import OCTAVES_HZ, octave_band_sos, octave_levels
from churchtune.analysis.points import PointResponse, analyze_points
from churchtune.analysis.response import (
    GRID_HZ,
    band_mean_db,
    calibration_on_grid,
    energy_average,
    interpolate_at,
    log_grid,
    low_cutoff_hz,
    magnitude_db,
    sixth_octave_centres,
    third_octave_centres,
)
from churchtune.analysis.rt60 import rt60_octaves, schroeder_db
from churchtune.domain.eq import Band, EqType
from churchtune.domain.eq_model import band_response_db, band_sos

FS_HZ = 48000.0


def impulse(n=48000, at=240, level=1.0):
    ir = np.zeros(n)
    ir[at] = level
    return ir


def decaying_noise(rt60_s, seconds=3.0, seed=0, noise_floor=0.0, fs_hz=FS_HZ):
    """White noise with an exponential amplitude envelope that falls 60 dB in ``rt60_s``."""
    rng = np.random.default_rng(seed)
    n = int(seconds * fs_hz)
    t = np.arange(n) / fs_hz
    ir = rng.normal(size=n) * np.exp(-math.log(1000.0) * t / rt60_s)
    return ir + noise_floor * rng.normal(size=n)


# ------------------------------------------------------------ grid
def test_grid_is_one_48th_octave_from_20_hz_to_20_khz():
    assert len(GRID_HZ) == 479
    assert GRID_HZ[0] == 20.0
    assert GRID_HZ[-1] == pytest.approx(19896.974, abs=0.001)
    np.testing.assert_allclose(GRID_HZ[1:] / GRID_HZ[:-1], 2 ** (1 / 48))
    assert GRID_HZ[48] == pytest.approx(40.0)


def test_log_grid_with_other_parameters():
    grid = log_grid(100.0, 800.0, points_per_octave=3)

    np.testing.assert_allclose(grid, 100.0 * 2 ** (np.arange(10) / 3))


def test_fractional_octave_centres():
    sixth = sixth_octave_centres()
    third = third_octave_centres()

    assert 1000.0 in sixth
    assert 1000.0 in third
    assert third[0] == pytest.approx(24.8, abs=0.1)
    assert third[-1] == pytest.approx(20159, abs=1)
    np.testing.assert_allclose(third[1:] / third[:-1], 2 ** (1 / 3))
    np.testing.assert_allclose(sixth[1:] / sixth[:-1], 2 ** (1 / 6))
    assert sixth[0] >= 31.5 * 0.99
    assert sixth[-1] <= 16000 * 1.01


# ------------------------------------------------------------ magnitude
@pytest.mark.parametrize("at", [0, 240, 5000])
def test_unit_impulse_has_a_flat_0_db_response_wherever_it_sits_in_the_window(at):
    response = magnitude_db(impulse(at=at), FS_HZ)

    assert response.shape == GRID_HZ.shape
    np.testing.assert_allclose(response, 0.0, atol=1e-9)


def test_impulse_level_scales_the_response():
    response = magnitude_db(impulse(level=0.1), FS_HZ)

    np.testing.assert_allclose(response, -20.0, atol=1e-9)


def test_response_of_a_known_filter_matches_its_model():
    band = Band(2, EqType.PEQ, 1000.0, -6.0, 1.0)
    ir = signal.sosfilt(band_sos(band, FS_HZ), impulse())

    response = magnitude_db(ir, FS_HZ)

    in_range = (GRID_HZ > 40) & (GRID_HZ < 15000)
    # 1/6-octave smoothing of a Q=1 dip changes its depth by less than 0.1 dB.
    np.testing.assert_allclose(
        response[in_range], band_response_db(band, GRID_HZ, FS_HZ)[in_range], atol=0.1
    )


def test_smoothing_spreads_a_pure_tone_over_its_fraction_of_an_octave():
    n = 24000
    tone = np.sin(2 * np.pi * 1000.0 * np.arange(n) / FS_HZ)

    narrow = magnitude_db(tone, FS_HZ, frac=24)
    wide = magnitude_db(tone, FS_HZ, frac=3)

    # The same tone power averaged over an 8 times wider band reads 10*log10(8) = 9 dB lower.
    at_1k = int(np.argmin(np.abs(GRID_HZ - 1000.0)))
    assert narrow[at_1k] - wide[at_1k] == pytest.approx(9.03, abs=0.3)


def test_only_the_first_window_of_the_response_is_analysed():
    early = impulse(n=96000, at=240)
    with_late_echo = early.copy()
    with_late_echo[40000] = 1.0  # 0.83 s, outside the 0.5 s window

    np.testing.assert_array_equal(magnitude_db(early, FS_HZ), magnitude_db(with_late_echo, FS_HZ))


def test_response_does_not_depend_on_the_sample_rate():
    np.testing.assert_allclose(magnitude_db(impulse(n=44100), 44100.0), 0.0, atol=1e-9)


def test_magnitude_rejects_empty_input_and_bad_sample_rate():
    with pytest.raises(ValueError, match="non-empty"):
        magnitude_db(np.zeros(0), FS_HZ)
    with pytest.raises(ValueError, match="fs_hz"):
        magnitude_db(impulse(), 0.0)


def test_silence_is_floored_not_minus_infinity():
    assert np.all(np.isfinite(magnitude_db(np.zeros(1000), FS_HZ)))


# ------------------------------------------------------------ curve helpers
def test_band_mean_is_a_power_mean():
    curve = np.where(GRID_HZ < 1000.0, 0.0, -20.0)
    half_and_half = band_mean_db(curve, 500.0, 2000.0)

    # Half the points at 0 dB, half at -20 dB: 10*log10((1 + 0.01) / 2) = -2.97 dB.
    assert half_and_half == pytest.approx(-2.97, abs=0.05)
    assert band_mean_db(np.full_like(GRID_HZ, -7.0), 500.0, 2000.0) == pytest.approx(-7.0)


def test_band_mean_rejects_an_empty_band():
    with pytest.raises(ValueError, match="no grid point"):
        band_mean_db(np.zeros_like(GRID_HZ), 5.0, 10.0)


def test_energy_average_of_0_and_minus_infinity_like_curves():
    curves = np.array([[0.0, 0.0, -6.0], [0.0, -100.0, -6.0]])

    np.testing.assert_allclose(energy_average(curves), [0.0, -3.0103, -6.0], atol=1e-4)


def test_interpolate_at_is_linear_over_log_frequency():
    curve = 10 * np.log2(GRID_HZ / 1000.0)  # +10 dB per octave

    np.testing.assert_allclose(
        interpolate_at(np.array([500.0, 1000.0, 3000.0]), curve),
        [-10.0, 0.0, 10 * math.log2(3)],
        atol=1e-6,
    )


def test_calibration_is_interpolated_onto_the_grid():
    cal = calibration_on_grid(np.array([20.0, 2000.0, 20000.0]), np.array([0.0, 0.0, 3.0]))

    assert cal[0] == 0.0
    assert cal[int(np.argmin(np.abs(GRID_HZ - 6324.6)))] == pytest.approx(1.5, abs=0.02)
    assert cal[-1] == pytest.approx(3.0, abs=0.01)


def test_low_cutoff_is_the_first_frequency_that_stays_above_the_drop():
    avg = np.where(GRID_HZ < 45.0, -20.0, 0.0)

    cutoff = low_cutoff_hz(avg)

    assert 45.0 <= cutoff < 45.0 * 2 ** (1 / 48)


def test_low_cutoff_without_a_roll_off_is_the_start_of_the_search_band():
    assert low_cutoff_hz(np.zeros_like(GRID_HZ)) == pytest.approx(25.0, rel=0.02)


def test_low_cutoff_ignores_dips_above_200_hz():
    avg = np.where((GRID_HZ > 300) & (GRID_HZ < 400), -30.0, 0.0)

    assert low_cutoff_hz(avg) == pytest.approx(25.0, rel=0.02)


# ------------------------------------------------------------ octave bands
@pytest.mark.parametrize("centre", OCTAVES_HZ)
def test_octave_band_filter_passes_its_centre_and_is_3_db_down_at_the_edges(centre):
    sos = octave_band_sos(centre, FS_HZ)
    edges = [centre / math.sqrt(2), centre, min(centre * math.sqrt(2), 0.45 * FS_HZ)]

    _, h = signal.sosfreqz(sos, worN=edges, fs=FS_HZ)

    level = 20 * np.log10(np.abs(h))
    assert level[1] == pytest.approx(0.0, abs=0.1)
    np.testing.assert_allclose(level[[0, 2]], -3.01, atol=0.05)


def test_octave_band_that_does_not_fit_the_sample_rate_is_rejected():
    with pytest.raises(ValueError, match="does not fit"):
        octave_band_sos(8000, 8000.0)


def test_octave_levels_find_a_tone_in_its_band():
    tone = 0.1 * np.sin(2 * np.pi * 1000.0 * np.arange(48000) / FS_HZ)

    levels = octave_levels(tone, FS_HZ)

    # 0.1 peak sine: -23.01 dBFS RMS in its own band; a third-order band-pass two octaves away
    # is about 40 dB down.
    assert levels[1000] == pytest.approx(-23.01, abs=0.1)
    assert levels[250] < levels[1000] - 35
    assert sorted(levels) == list(OCTAVES_HZ)


# ------------------------------------------------------------ RT60
@pytest.mark.parametrize("rt60_s", [0.5, 1.0, 1.6])
def test_rt60_of_exponentially_decaying_noise(rt60_s):
    decays = rt60_octaves(decaying_noise(rt60_s, seconds=3 * rt60_s + 1), FS_HZ)

    for centre in (1000, 2000, 4000):
        band = decays[centre]
        # One realisation of band-limited noise wobbles: 10 % for the fits over 20-30 dB, more
        # for the 10 dB of EDT. The next test shows that the mean over realisations is exact.
        assert band.t20_s == pytest.approx(rt60_s, rel=0.1), centre
        assert band.t30_s == pytest.approx(rt60_s, rel=0.1), centre
        assert band.edt_s == pytest.approx(rt60_s, rel=0.25), centre
        assert band.curvature_pct == pytest.approx(0.0, abs=12.0)
        assert band.dynamic_db > 45


@pytest.mark.parametrize("centre", [125, 250, 500, 1000, 4000])
def test_rt60_estimate_is_unbiased_over_realisations(centre):
    """A single realisation wobbles by 10 % and more in a narrow band; the mean does not."""
    estimates = [
        rt60_octaves(decaying_noise(1.0, seed=seed), FS_HZ, bands_hz=(centre,))[centre]
        for seed in range(12)
    ]

    assert all(e.t20_s is not None and e.t30_s is not None for e in estimates)
    assert np.mean([e.t20_s for e in estimates]) == pytest.approx(1.0, rel=0.05)
    assert np.mean([e.t30_s for e in estimates]) == pytest.approx(1.0, rel=0.05)


def test_rt60_survives_a_noise_floor_thanks_to_truncation_and_compensation():
    clean = rt60_octaves(decaying_noise(1.0), FS_HZ)[1000]
    # Noise 50 dB below the start of the decay.
    noisy = rt60_octaves(decaying_noise(1.0, noise_floor=10 ** (-50 / 20)), FS_HZ)[1000]

    assert noisy.t20_s == pytest.approx(clean.t20_s, rel=0.05)
    assert noisy.t30_s == pytest.approx(1.0, rel=0.1)


def test_t30_needs_45_db_and_t20_needs_35_db_of_dynamic_range():
    # Noise 40 dB below the start: enough for T20, not for T30.
    medium = rt60_octaves(decaying_noise(1.0, noise_floor=10 ** (-40 / 20)), FS_HZ)[1000]
    # Noise 28 dB below the start: neither.
    poor = rt60_octaves(decaying_noise(1.0, noise_floor=10 ** (-28 / 20)), FS_HZ)[1000]

    assert 35 <= medium.dynamic_db < 45
    assert medium.t20_s == pytest.approx(1.0, rel=0.1)
    assert medium.t30_s is None
    assert poor.dynamic_db < 35
    assert poor.t20_s is None
    assert poor.t30_s is None


def test_rt60_of_a_too_short_response_is_not_available():
    decays = rt60_octaves(decaying_noise(0.05, seconds=0.1), FS_HZ)

    assert all(band.t20_s is None and band.edt_s is None for band in decays.values())
    assert decays[1000].dynamic_db == 0.0


def test_schroeder_curve_starts_at_0_db_and_falls_monotonically():
    band = signal.sosfilt(octave_band_sos(1000, FS_HZ), decaying_noise(1.0))

    decay_db, dynamic_db = schroeder_db(band, FS_HZ)

    assert decay_db is not None
    assert decay_db[0] == pytest.approx(0.0)
    assert np.all(np.diff(decay_db) <= 1e-12)
    assert dynamic_db > 60
    # Halfway through a 1 s RT60 the integrated energy is 30 dB down.
    assert decay_db[int(0.5 * FS_HZ)] == pytest.approx(-30.0, abs=1.5)


def test_double_slope_decay_drops_t30_when_curvature_is_out_of_range():
    t = np.arange(int(4 * FS_HZ)) / FS_HZ
    rng = np.random.default_rng(5)
    fast = np.exp(-math.log(1000.0) * t / 0.3)
    # The slow tail holds 10 times the decay time but 37 dB less amplitude, so its integrated
    # energy takes over 27 dB down: inside the T30 range, below the T20 range.
    slow = 10 ** (-37 / 20) * np.exp(-math.log(1000.0) * t / 3.0)
    ir = rng.normal(size=len(t)) * (fast + slow)

    band = rt60_octaves(ir, FS_HZ)[1000]

    assert band.curvature_pct is not None
    assert band.curvature_pct > 50
    assert band.t30_s is None
    assert band.t20_s == pytest.approx(0.3, rel=0.25)


# ------------------------------------------------------------ points
def test_analyze_points_normalises_each_point_and_averages_power():
    quiet, loud = impulse(level=0.1), impulse(level=1.0)

    analysis = analyze_points({"p1": PointResponse(quiet, FS_HZ), "p2": PointResponse(loud, FS_HZ)})

    assert analysis.point_names == ("p1", "p2")
    np.testing.assert_allclose(analysis.raw_db["p1"], -20.0, atol=1e-9)
    np.testing.assert_allclose(analysis.norm_db["p1"], 0.0, atol=1e-9)
    np.testing.assert_allclose(analysis.avg_db, 0.0, atol=1e-9)
    np.testing.assert_allclose(analysis.spread_db, 0.0, atol=1e-9)
    assert analysis.grid_hz is GRID_HZ
    assert analysis.f_low_hz == pytest.approx(25.0, rel=0.02)


def test_analyze_points_reports_spread_min_and_max_between_points():
    flat = impulse()
    dipped = signal.sosfilt(band_sos(Band(2, EqType.PEQ, 4000.0, -10.0, 2.0), FS_HZ), impulse())

    analysis = analyze_points(
        {"flat": PointResponse(flat, FS_HZ), "dip": PointResponse(dipped, FS_HZ)}
    )

    at_4k = int(np.argmin(np.abs(GRID_HZ - 4000.0)))
    at_200 = int(np.argmin(np.abs(GRID_HZ - 200.0)))
    # Two values 10 dB apart: standard deviation 5 dB (the smoothing takes a little off the dip).
    assert analysis.spread_db[at_4k] == pytest.approx(5.0, abs=0.4)
    assert analysis.max_db[at_4k] - analysis.min_db[at_4k] == pytest.approx(10.0, abs=0.8)
    # Far from the dip only the normalisation offset between the two points is left.
    assert analysis.spread_db[at_200] < 0.3
    # Power average of 0 dB and -10 dB is -2.6 dB.
    assert analysis.avg_db[at_4k] == pytest.approx(-2.6, abs=0.3)


def test_analyze_points_subtracts_the_mic_calibration():
    cal = np.full_like(GRID_HZ, 2.0)

    analysis = analyze_points({"p1": PointResponse(impulse(), FS_HZ)}, mic_cal_db=cal)

    np.testing.assert_allclose(analysis.raw_db["p1"], -2.0, atol=1e-9)


def test_analyze_points_averages_decay_times_over_the_points_that_have_them():
    points = {
        "a": PointResponse(decaying_noise(1.0, seed=1), FS_HZ),
        "b": PointResponse(decaying_noise(1.2, seed=2), FS_HZ),
        "short": PointResponse(impulse(n=4800), FS_HZ),
    }

    analysis = analyze_points(points)

    t20 = analysis.rt60[1000].t20
    assert t20.n_points == 2
    assert t20.mean_s == pytest.approx(1.1, rel=0.06)
    assert t20.sd_s == pytest.approx(0.1, abs=0.05)
    assert analysis.rt60_points["short"][1000].t20_s is None
    assert sorted(analysis.rt60) == list(OCTAVES_HZ)


def test_decay_stat_of_a_single_point_has_no_deviation_and_of_none_has_no_mean():
    analysis = analyze_points({"short": PointResponse(impulse(n=4800), FS_HZ)})
    one = analyze_points({"a": PointResponse(decaying_noise(1.0), FS_HZ)})

    assert analysis.rt60[1000].t20.mean_s is None
    assert analysis.rt60[1000].t20.n_points == 0
    assert one.rt60[1000].t30.n_points == 1
    assert one.rt60[1000].t30.sd_s is None


def test_analyze_points_needs_points():
    with pytest.raises(ValueError, match="no measured points"):
        analyze_points({})
