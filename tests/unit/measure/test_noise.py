"""Background noise analysis: levels, hum families, rumble."""

import numpy as np
import pytest
from scipy import signal

from churchtune.audio.fake.hall import FakeHall, FakeMic
from churchtune.measure.noise import NoiseHint, analyse_noise, hum_frequency, measure_background

FS = 48000.0
RNG = np.random.default_rng(3)
WHITE = RNG.normal(scale=1e-4, size=int(3 * FS))
T = np.arange(len(WHITE)) / FS


def _hum(mains_hz):
    return sum(3e-3 * np.sin(2 * np.pi * mains_hz * k * T) for k in (2, 4, 6))


@pytest.mark.parametrize("mains_hz", [50.0, 60.0])
def test_dimmer_hum_is_recognised(mains_hz):
    assert hum_frequency(WHITE + _hum(mains_hz), FS) == mains_hz
    result = analyse_noise(WHITE + _hum(mains_hz), FS, 10)
    assert NoiseHint.HUM in result.hints
    assert result.hum_hz == mains_hz


def test_quiet_white_noise_has_no_hint():
    result = analyse_noise(WHITE, FS, 10)
    assert result.hints == ()
    assert result.hum_hz is None
    assert result.rms_dbfs == pytest.approx(-80, abs=1)
    assert set(result.octaves_dbfs) >= {125, 1000}


def test_low_rumble_and_loud_room():
    sos = signal.butter(4, 120, "lowpass", fs=FS, output="sos")
    rumble = signal.sosfilt(sos, RNG.normal(scale=0.05, size=len(WHITE)))
    result = analyse_noise(WHITE + rumble, FS, 10)
    assert NoiseHint.RUMBLE in result.hints
    assert NoiseHint.LOUD in result.hints


def test_one_line_is_not_a_hum():
    assert hum_frequency(WHITE + 3e-3 * np.sin(2 * np.pi * 100 * T), FS) is None


def test_background_on_the_hall():
    hall = FakeHall(FS, {10: FakeMic(noise_dbfs=-70.0), 11: FakeMic()})
    result = measure_background(hall, [10, 11], FS, duration_s=1.0)
    assert [m.usb_input for m in result] == [10, 11]
    assert result[0].rms_dbfs == pytest.approx(-70, abs=1)
    assert result[1].rms_dbfs < result[0].rms_dbfs
