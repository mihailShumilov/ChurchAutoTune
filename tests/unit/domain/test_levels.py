import numpy as np
import pytest

from churchtune.domain.levels import (
    HARD_LIMIT_DBFS,
    SILENCE_DBFS,
    amplitude_db,
    db_to_lin,
    peak_dbfs,
    rms_dbfs,
)


@pytest.mark.parametrize(("level_db", "factor"), [(0.0, 1.0), (-6.0, 0.5012), (-20.0, 0.1)])
def test_db_to_lin(level_db, factor):
    assert db_to_lin(level_db) == pytest.approx(factor, rel=1e-3)


def test_hard_limit_factor_is_about_half_scale():
    assert db_to_lin(HARD_LIMIT_DBFS) == pytest.approx(10 ** (-6 / 20))


def test_amplitude_db_is_elementwise_and_floored():
    result = amplitude_db(np.array([1.0, -0.1, 0.0]))

    np.testing.assert_allclose(result, [0.0, -20.0, -240.0], atol=1e-9)


def test_rms_of_a_full_scale_sine_is_minus_3_dbfs():
    sine = np.sin(2 * np.pi * 1000 * np.arange(48000) / 48000)

    # RMS of a sine is 1/sqrt(2) of its peak: -3.01 dB.
    assert rms_dbfs(sine) == pytest.approx(-3.01, abs=0.01)
    assert peak_dbfs(sine) == pytest.approx(0.0, abs=0.01)


def test_rms_of_dc_equals_its_level():
    assert rms_dbfs(np.full(100, 0.1)) == pytest.approx(-20.0)


@pytest.mark.parametrize("func", [rms_dbfs, peak_dbfs])
def test_empty_signal_is_silence(func):
    assert func(np.zeros(0)) == SILENCE_DBFS


def test_all_zero_signal_is_floored_not_minus_infinity():
    assert rms_dbfs(np.zeros(10)) == SILENCE_DBFS
    assert peak_dbfs(np.zeros(10)) == -240.0
