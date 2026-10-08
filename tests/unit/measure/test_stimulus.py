"""Test signals. ``test_sweep_levels_and_hard_limit`` is ported from the original tool."""

import math

import numpy as np
import pytest
from scipy import signal

from churchtune.domain.levels import HARD_LIMIT_DBFS, peak_dbfs, rms_dbfs
from churchtune.errors import LevelError
from churchtune.measure.config import SweepConfig
from churchtune.measure.stimulus import (
    build_stimulus,
    check_level,
    half_cos_fade,
    make_sweep,
    pink_noise,
)

FS_HZ = 48000.0
SWEEP = SweepConfig()


# ------------------------------------------------------------ ported from the original tool
def test_sweep_levels_and_hard_limit():
    sweep = make_sweep(FS_HZ, SWEEP, level_dbfs=-24.0)
    assert peak_dbfs(sweep) == pytest.approx(-24.0, abs=0.05)
    assert abs(sweep[0]) < 1e-6  # fade-in
    assert abs(sweep[-1]) < 1e-3  # fade-out
    with pytest.raises(LevelError):
        make_sweep(FS_HZ, SWEEP, level_dbfs=-3.0)

    noise = pink_noise(2.0, FS_HZ, np.random.default_rng(1), rms_level_dbfs=-27.0)
    assert peak_dbfs(noise) <= HARD_LIMIT_DBFS + 1e-6
    with pytest.raises(LevelError):
        pink_noise(1.0, FS_HZ, np.random.default_rng(1), rms_level_dbfs=-5.0)


# ------------------------------------------------------------ levels
@pytest.mark.parametrize("level_dbfs", [-6.0, -6.01, -24.0, -120.0, -math.inf])
def test_check_level_accepts_levels_up_to_the_hard_limit(level_dbfs):
    check_level(level_dbfs)


@pytest.mark.parametrize("level_dbfs", [-5.99, 0.0, 6.0, math.inf, math.nan])
def test_check_level_refuses_levels_above_the_hard_limit_and_nan(level_dbfs):
    with pytest.raises(LevelError) as excinfo:
        check_level(level_dbfs)

    assert excinfo.value.context["limit_dbfs"] == HARD_LIMIT_DBFS
    assert isinstance(excinfo.value, ValueError)


def test_hard_limit_is_minus_six_dbfs():
    assert HARD_LIMIT_DBFS == -6.0


# ------------------------------------------------------------ sweep
def test_sweep_has_the_configured_length_and_default_level():
    sweep = make_sweep(FS_HZ, SWEEP)

    assert sweep.dtype == np.float64
    assert len(sweep) == 6 * 48000
    assert peak_dbfs(sweep) == pytest.approx(SWEEP.level_dbfs, abs=0.05)


def test_sweep_instantaneous_frequency_is_exponential_from_f1_to_f2():
    cfg = SweepConfig(f1_hz=100.0, f2_hz=3200.0, duration_s=2.0, fade_in_s=0.0, fade_out_s=0.0)
    sweep = make_sweep(FS_HZ, cfg, level_dbfs=-20.0)

    # Count zero crossings in short windows: frequency = crossings / 2 / window.
    def freq_at(t_s, window_s=0.02):
        seg = sweep[int(t_s * FS_HZ) : int((t_s + window_s) * FS_HZ)]
        return np.sum(np.diff(np.signbit(seg)) != 0) / 2 / window_s

    # After a fraction x of the sweep the frequency is f1 * (f2/f1)**x: 5 octaves in 2 s.
    for t_s, expected_hz in [(0.4, 200.0), (0.8, 400.0), (1.2, 800.0), (1.6, 1600.0)]:
        # A 20 ms window spans about 3.5 % of frequency change; allow 6 %.
        assert freq_at(t_s) == pytest.approx(expected_hz * 2 ** (0.01 / 0.4), rel=0.06)


def test_sweep_spectrum_falls_3_db_per_octave():
    sweep = make_sweep(FS_HZ, SWEEP, level_dbfs=-20.0)
    # One FFT of the whole sweep: each frequency is visited once, so no segment averaging.
    freqs = np.fft.rfftfreq(len(sweep), 1 / FS_HZ)
    power = np.abs(np.fft.rfft(sweep)) ** 2

    def band_db(lo, hi):
        sel = (freqs >= lo) & (freqs < hi)
        return 10 * np.log10(np.mean(power[sel]))

    # Equal energy per octave means power density falls 3.01 dB per octave (pink).
    assert band_db(400, 500) - band_db(800, 1000) == pytest.approx(3.01, abs=0.3)
    assert band_db(1000, 1250) - band_db(4000, 5000) == pytest.approx(6.02, abs=0.3)


@pytest.mark.parametrize("fs_hz", [0.0, -1.0, math.nan, math.inf])
def test_sweep_rejects_invalid_sample_rate(fs_hz):
    with pytest.raises(ValueError, match="fs_hz"):
        make_sweep(fs_hz, SWEEP)


@pytest.mark.parametrize(("f1", "f2"), [(0.0, 100.0), (200.0, 100.0), (100.0, 100.0)])
def test_sweep_rejects_invalid_range(f1, f2):
    with pytest.raises(ValueError, match="sweep range"):
        make_sweep(FS_HZ, SweepConfig(f1_hz=f1, f2_hz=f2))


def test_sweep_works_at_another_sample_rate():
    sweep = make_sweep(44100.0, SweepConfig(duration_s=1.0), level_dbfs=-30.0)

    assert len(sweep) == 44100
    assert peak_dbfs(sweep) == pytest.approx(-30.0, abs=0.05)


# ------------------------------------------------------------ fades and buffers
def test_half_cos_fade_rises_from_zero_to_one():
    fade = half_cos_fade(101)

    assert fade[0] == pytest.approx(0.0)
    assert fade[50] == pytest.approx(0.5)
    assert fade[-1] == pytest.approx(1.0)
    assert np.all(np.diff(fade) > 0)


@pytest.mark.parametrize("n", [0, -5])
def test_half_cos_fade_is_empty_for_non_positive_length(n):
    assert half_cos_fade(n).size == 0


def test_build_stimulus_wraps_the_signal_in_silence_on_two_identical_channels():
    mono = np.full(1000, 0.1)

    stimulus = build_stimulus(mono, FS_HZ, preroll_s=0.5, tail_s=3.5)

    assert stimulus.dtype == np.float32
    assert stimulus.shape == (24000 + 1000 + 168000, 2)
    assert np.all(stimulus[:24000] == 0)
    assert np.all(stimulus[25000:] == 0)
    np.testing.assert_allclose(stimulus[24000:25000, 0], 0.1, rtol=1e-6)
    np.testing.assert_array_equal(stimulus[:, 0], stimulus[:, 1])


# ------------------------------------------------------------ pink noise
def test_pink_noise_has_the_requested_rms_when_the_peak_allows_it():
    noise = pink_noise(4.0, FS_HZ, np.random.default_rng(7), rms_level_dbfs=-40.0)

    assert noise.dtype == np.float64
    assert len(noise) == 4 * 48000
    assert rms_dbfs(noise) == pytest.approx(-40.0, abs=0.01)


def test_pink_noise_is_scaled_down_when_its_peak_would_pass_the_limit():
    noise = pink_noise(4.0, FS_HZ, np.random.default_rng(7), rms_level_dbfs=-10.0)

    assert peak_dbfs(noise) == pytest.approx(HARD_LIMIT_DBFS, abs=1e-6)
    assert rms_dbfs(noise) < -10.0


def test_pink_noise_spectrum_falls_3_db_per_octave_and_is_band_limited():
    noise = pink_noise(20.0, FS_HZ, np.random.default_rng(3), rms_level_dbfs=-30.0)
    freqs, power = signal.welch(noise, FS_HZ, nperseg=1 << 14)

    def band_db(lo, hi):
        sel = (freqs >= lo) & (freqs < hi)
        return 10 * np.log10(np.mean(power[sel]))

    # 1/f power: -3.01 dB per octave; averaged over a 20 s realisation it holds within 0.5 dB.
    assert band_db(250, 500) - band_db(500, 1000) == pytest.approx(3.01, abs=0.5)
    assert band_db(1000, 2000) - band_db(4000, 8000) == pytest.approx(6.02, abs=0.5)
    assert band_db(21000, 23000) < band_db(10000, 20000) - 60


def test_pink_noise_is_reproducible_with_a_seeded_generator_and_loops_seamlessly():
    a = pink_noise(1.0, FS_HZ, np.random.default_rng(5))
    b = pink_noise(1.0, FS_HZ, np.random.default_rng(5))

    np.testing.assert_array_equal(a, b)
    # Built from whole-period sinusoids: the step across the loop point is as small as inside.
    assert abs(a[0] - a[-1]) < 4 * np.max(np.abs(np.diff(a)))


def test_pink_noise_refuses_a_peak_limit_above_the_hard_limit():
    with pytest.raises(LevelError):
        pink_noise(1.0, FS_HZ, np.random.default_rng(1), peak_limit_dbfs=-3.0)
