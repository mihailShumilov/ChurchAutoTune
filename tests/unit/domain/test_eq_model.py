import numpy as np
import pytest
from scipy import signal

from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.domain.eq_model import band_response_db, band_sos, eq_response_db

FS_HZ = 48000.0


def _flat(n, eq_type=EqType.PEQ):
    return Band(n, eq_type, 1000.0, 0.0, 2.0)


def test_peq_response_equals_gain_at_centre():
    band = Band(2, EqType.PEQ, 1000.0, -6.0, 2.0)

    response = band_response_db(band, np.array([1000.0]), FS_HZ)

    # RBJ peaking filter has exactly its gain at f0.
    np.testing.assert_allclose(response, [-6.0], atol=1e-9)


def test_peq_q_follows_the_analog_peaking_prototype():
    """Pins the Q definition: H(s) = (s^2 + s*A/Q + 1) / (s^2 + s/(A*Q) + 1), A = 10^(g/40)."""
    f0_hz, gain_db, q = 100.0, -9.0, 1.5
    freqs = np.geomspace(30.0, 400.0, 60)
    amp = 10 ** (gain_db / 40)
    s = 1j * freqs / f0_hz
    prototype = 20 * np.log10(np.abs((s**2 + s * amp / q + 1) / (s**2 + s / (amp * q) + 1)))

    response = band_response_db(Band(2, EqType.PEQ, f0_hz, gain_db, q), freqs, FS_HZ)

    # At 100 Hz and 48 kHz the bilinear transform's warping is far below 0.02 dB.
    np.testing.assert_allclose(response, prototype, atol=0.02)


def test_eq_response_is_the_sum_of_its_bands_for_any_gains():
    freqs = np.geomspace(20.0, 20000.0, 40)
    rng = np.random.default_rng(0)
    for _ in range(20):
        bands = tuple(
            Band(
                i,
                EqType.PEQ,
                float(rng.uniform(30, 15000)),
                float(rng.uniform(-15, 15)),
                float(rng.uniform(0.3, 10)),
            )
            for i in range(1, 7)
        )
        total = eq_response_db(SystemEq(True, bands), freqs, FS_HZ)
        np.testing.assert_allclose(
            total, sum(band_response_db(b, freqs, FS_HZ) for b in bands), atol=1e-9
        )


def test_peq_response_is_flat_far_from_centre():
    band = Band(2, EqType.PEQ, 1000.0, -6.0, 2.0)

    response = band_response_db(band, np.array([20.0, 15000.0]), FS_HZ)

    np.testing.assert_allclose(response, [0.0, 0.0], atol=0.1)


@pytest.mark.parametrize("eq_type", [EqType.PEQ, EqType.VEQ])
def test_parametric_band_with_zero_gain_is_exactly_flat(eq_type):
    freqs = np.geomspace(20.0, 20000.0, 50)

    response = band_response_db(Band(3, eq_type, 500.0, 0.0, 4.0), freqs, FS_HZ)

    assert np.array_equal(response, np.zeros_like(freqs))


def test_low_shelf_reaches_gain_below_and_zero_above():
    band = Band(1, EqType.LSHV, 200.0, 4.0, 0.7)

    response = band_response_db(band, np.array([20.0, 5000.0]), FS_HZ)

    np.testing.assert_allclose(response, [4.0, 0.0], atol=0.15)


def test_high_shelf_reaches_gain_above_and_zero_below():
    band = Band(6, EqType.HSHV, 4000.0, -5.0, 0.7)

    response = band_response_db(band, np.array([50.0, 20000.0]), FS_HZ)

    np.testing.assert_allclose(response, [0.0, -5.0], atol=0.15)


@pytest.mark.parametrize(
    ("eq_type", "expected_db"),
    [
        (EqType.BU6, -3.01),
        (EqType.BU12, -3.01),
        (EqType.BU18, -3.01),
        (EqType.BU24, -3.01),
        (EqType.BS12, -3.01),  # Bessel normalised to -3 dB at the corner (norm="mag")
        (EqType.BS24, -3.01),
        (EqType.LR12, -6.02),  # Linkwitz-Riley = squared Butterworth
        (EqType.LR24, -6.02),
    ],
)
def test_cut_slope_level_at_corner_frequency(eq_type, expected_db):
    response = band_response_db(Band(1, eq_type, 100.0, 0.0, 2.0), np.array([100.0]), FS_HZ)

    np.testing.assert_allclose(response, [expected_db], atol=0.02)


@pytest.mark.parametrize(
    ("eq_type", "db_per_octave"),
    [(EqType.BU6, 6.0), (EqType.BU12, 12.0), (EqType.BU18, 18.0), (EqType.LR24, 24.0)],
)
def test_cut_slope_steepness_far_below_corner(eq_type, db_per_octave):
    band = Band(1, eq_type, 1000.0, 0.0, 2.0)

    low, high = band_response_db(band, np.array([31.25, 62.5]), FS_HZ)

    # Five and four octaves below the corner the asymptotic slope is reached.
    assert high - low == pytest.approx(db_per_octave, abs=0.1)


@pytest.mark.parametrize(("number", "passes_highs"), [(1, True), (3, True), (4, False), (6, False)])
def test_slope_types_are_high_pass_on_bands_1_to_3_and_low_pass_on_4_to_6(number, passes_highs):
    band = Band(number, EqType.LR24, 1000.0, 0.0, 2.0)

    low, high = band_response_db(band, np.array([100.0, 10000.0]), FS_HZ)

    assert (high > -0.1 and low < -60.0) if passes_highs else (low > -0.1 and high < -60.0)


def test_lcut_and_hcut_are_second_order_butterworth_shapes():
    lcut = band_response_db(Band(1, EqType.LCUT, 100.0, 0.0, 2.0), np.array([100.0]), FS_HZ)
    hcut = band_response_db(Band(6, EqType.HCUT, 5000.0, 0.0, 2.0), np.array([5000.0]), FS_HZ)

    np.testing.assert_allclose([lcut[0], hcut[0]], [-3.01, -3.01], atol=0.05)


def test_eq_response_sums_bands():
    b2 = Band(2, EqType.PEQ, 500.0, -4.0, 1.5)
    b5 = Band(5, EqType.PEQ, 3000.0, 2.0, 1.0)
    eq = SystemEq(True, (_flat(1), b2, _flat(3), _flat(4), b5, _flat(6)))
    freqs = np.geomspace(20.0, 20000.0, 64)

    total = eq_response_db(eq, freqs, FS_HZ)

    expected = band_response_db(b2, freqs, FS_HZ) + band_response_db(b5, freqs, FS_HZ)
    np.testing.assert_allclose(total, expected, atol=1e-12)


def test_eq_response_is_zero_when_eq_is_off():
    eq = SystemEq(False, (Band(1, EqType.PEQ, 500.0, -9.0, 1.5), *(_flat(i) for i in range(2, 7))))

    total = eq_response_db(eq, np.array([500.0]), FS_HZ)

    assert np.array_equal(total, [0.0])


def test_eq_response_only_adjustable_skips_cut_filters():
    hpf = Band(1, EqType.BU18, 100.0, 0.0, 2.0)
    peq = Band(2, EqType.PEQ, 1000.0, -3.0, 2.0)
    eq = SystemEq(True, (hpf, peq, *(_flat(i) for i in range(3, 7))))
    freqs = np.array([50.0, 1000.0])

    adjustable = eq_response_db(eq, freqs, FS_HZ, only_adjustable=True)

    np.testing.assert_allclose(adjustable, band_response_db(peq, freqs, FS_HZ), atol=1e-12)


@pytest.mark.parametrize("fs_hz", [0.0, -48000.0, float("nan"), float("inf")])
def test_response_rejects_invalid_sample_rate(fs_hz):
    with pytest.raises(ValueError, match="fs_hz"):
        band_response_db(_flat(2), np.array([1000.0]), fs_hz)


@pytest.mark.parametrize(
    "band",
    [
        Band(2, EqType.PEQ, 480.0, -9.0, 1.5),
        Band(1, EqType.LSHV, 200.0, 4.0, 0.7),
        Band(6, EqType.HSHV, 4000.0, -5.0, 0.7),
        Band(1, EqType.LCUT, 100.0, 0.0, 2.0),
        Band(6, EqType.HCUT, 5000.0, 0.0, 2.0),
    ],
)
def test_band_sos_matches_response_model_for_biquad_types(band):
    freqs = np.geomspace(30.0, 15000.0, 40)
    sos = band_sos(band, FS_HZ)
    assert sos is not None

    _, h = signal.sosfreqz(sos, worN=freqs, fs=FS_HZ)

    # Same RBJ biquad in both functions.
    np.testing.assert_allclose(
        20 * np.log10(np.abs(h)), band_response_db(band, freqs, FS_HZ), atol=1e-6
    )


@pytest.mark.parametrize("eq_type", [EqType.BU18, EqType.LR24, EqType.BS12])
def test_band_sos_matches_response_model_for_slopes_well_below_nyquist(eq_type):
    band = Band(1, eq_type, 100.0, 0.0, 2.0)
    freqs = np.geomspace(30.0, 2000.0, 30)
    sos = band_sos(band, FS_HZ)
    assert sos is not None

    _, h = signal.sosfreqz(sos, worN=freqs, fs=FS_HZ)

    # Digital filter vs analog prototype: they agree far below Nyquist; Bessel warps slightly more.
    np.testing.assert_allclose(
        20 * np.log10(np.abs(h)), band_response_db(band, freqs, FS_HZ), atol=0.3
    )


def test_band_sos_is_none_for_flat_parametric_and_high_shelf_bands():
    assert band_sos(Band(2, EqType.PEQ, 1000.0, 0.0, 2.0), FS_HZ) is None
    assert band_sos(Band(6, EqType.HSHV, 8000.0, 0.0, 2.0), FS_HZ) is None


def test_band_sos_lr24_is_two_cascaded_second_order_butterworths():
    sos = band_sos(Band(6, EqType.LR24, 100.0, 0.0, 2.0), FS_HZ)

    assert sos is not None
    assert sos.shape == (2, 6)
    np.testing.assert_array_equal(sos[0], sos[1])
