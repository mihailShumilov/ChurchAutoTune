"""Normalization formulas, quantization grids and console display formats.

The first block is ported from the original tool's ``test_params_osc.py``; the rest pins the
grids exhaustively and adds property tests.
"""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from churchtune.domain.eq import Band, EqQuantizer, EqType
from churchtune.mixers.x32 import params as P  # noqa: N812 - short alias kept from the original

EQ_GRIDS = [
    (P.FREQ_STEPS, P.norm_to_freq, P.freq_to_norm),
    (P.GAIN_STEPS, P.norm_to_gain, P.gain_to_norm),
    (P.Q_STEPS, P.norm_to_q, P.q_to_norm),
    (P.DELAY_STEPS, P.norm_to_delay, P.delay_to_norm),
]
unit = st.floats(min_value=0.0, max_value=1.0)


# ------------------------------------------------------------ ported from the original tool
def test_formula_endpoints():
    assert P.norm_to_freq(0) == pytest.approx(20)
    assert P.norm_to_freq(1) == pytest.approx(20000)
    assert P.norm_to_gain(0) == -15
    assert P.norm_to_gain(1) == 15
    assert P.norm_to_gain(0.5) == 0
    assert P.norm_to_q(0) == pytest.approx(10)
    assert P.norm_to_q(1) == pytest.approx(0.3)
    for freq_hz in (20, 63, 1000, 19999):
        assert P.norm_to_freq(P.freq_to_norm(freq_hz)) == pytest.approx(freq_hz)
    for q in (0.3, 0.7, 4, 10):
        assert P.norm_to_q(P.q_to_norm(q)) == pytest.approx(q)


@pytest.mark.parametrize(
    ("idx", "text"), [(71, "232.3"), (4, "23.0"), (133, "1k97"), (170, "7k09"), (194, "16k25")]
)
def test_freq_grid_matches_real_console_display(idx, text):
    """Values seen in the real dump are exact points of the 201-step log grid."""
    assert P.fmt_freq(P.norm_to_freq(idx / 200)) == text


@pytest.mark.parametrize("q", ["2.0", "3.9", "6.4", "7.4", "7.8"])
def test_q_grid_matches_real_console_display(q):
    x_norm = P.quantize_q(P.q_to_norm(float(q)))

    assert P.fmt_q(P.norm_to_q(x_norm)) == q


def test_fader_and_delay_formulas():
    for level_db in (-60, -37.5, -10.9, -5, 0, 10):
        assert P.norm_to_fader(P.fader_to_norm(level_db)) == pytest.approx(level_db, abs=1e-9)
    quantized = P.norm_to_fader(P.quantize_fader(P.fader_to_norm(-37.5)))
    assert quantized == pytest.approx(-37.5, abs=0.05)
    assert P.norm_to_delay(P.quantize_delay(P.delay_to_norm(4.37))) == pytest.approx(4.4, abs=1e-6)


# ------------------------------------------------------------ grids, exhaustively
def test_step_counts():
    assert (P.FREQ_STEPS, P.GAIN_STEPS, P.Q_STEPS) == (201, 121, 72)
    assert (P.FADER_STEPS, P.DELAY_STEPS) == (1024, 4998)


def test_eq_type_indices_match_the_console_enum():
    assert [str(t) for t in P.EQ_TYPES] == [
        "LCut", "LShv", "PEQ", "VEQ", "HShv", "HCut",
        "BU6", "BU12", "BS12", "LR12", "BU18", "BU24", "BS24", "LR24",
    ]  # fmt: skip
    assert set(P.EQ_TYPES) == set(EqType)


@pytest.mark.parametrize(("steps", "to_real", "to_norm"), EQ_GRIDS)
def test_every_grid_point_survives_a_round_trip_through_real_units(steps, to_real, to_norm):
    for idx in range(steps):
        assert P.step_index(to_norm(to_real(idx / (steps - 1))), steps) == idx


def test_every_fader_step_survives_a_round_trip_through_db():
    for idx in range(P.FADER_STEPS):
        x_norm = idx / (P.FADER_STEPS - 1)

        assert P.step_index(P.fader_to_norm(P.norm_to_fader(x_norm)), P.FADER_STEPS) == idx


def test_every_freq_grid_point_survives_the_display_format():
    for idx in range(P.FREQ_STEPS):
        shown = P.fmt_freq(P.norm_to_freq(idx / (P.FREQ_STEPS - 1)))

        assert P.step_index(P.freq_to_norm(P.parse_freq(shown)), P.FREQ_STEPS) == idx


def test_gain_grid_is_quarter_db_and_delay_grid_is_tenth_ms():
    assert P.norm_to_gain(1 / (P.GAIN_STEPS - 1)) - P.norm_to_gain(0) == pytest.approx(0.25)
    assert P.norm_to_delay(1 / (P.DELAY_STEPS - 1)) - P.norm_to_delay(0) == pytest.approx(0.1)


@pytest.mark.parametrize(
    ("x_norm", "level_db"),
    [(1.0, 10.0), (0.75, 0.0), (0.5, -10.0), (0.25, -30.0), (0.0625, -60.0), (0.03125, -75.0)],
)
def test_fader_law_segment_points(x_norm, level_db):
    assert P.norm_to_fader(x_norm) == pytest.approx(level_db)
    assert P.fader_to_norm(level_db) == pytest.approx(x_norm)


@pytest.mark.parametrize("x_norm", [0.0, -0.1])
def test_fader_down_is_minus_infinity(x_norm):
    assert P.norm_to_fader(x_norm) == -math.inf


@pytest.mark.parametrize("level_db", [-math.inf, -90.0, -120.0])
def test_fader_level_at_or_below_minus_90_db_is_zero(level_db):
    assert P.fader_to_norm(level_db) == 0.0


def test_fader_level_above_the_top_is_clamped():
    assert P.fader_to_norm(20.0) == 1.0
    assert P.fader_to_norm(math.inf) == 1.0


@pytest.mark.parametrize(
    ("to_norm", "below", "above"),
    [
        (P.freq_to_norm, 1.0, 99999.0),
        (P.gain_to_norm, -40.0, 40.0),
        (P.delay_to_norm, 0.0, 9999.0),
    ],
)
def test_real_values_outside_the_range_are_clamped(to_norm, below, above):
    assert to_norm(below) == 0.0
    assert to_norm(above) == 1.0


def test_q_outside_the_range_is_clamped():
    assert P.q_to_norm(100.0) == 0.0
    assert P.q_to_norm(0.01) == 1.0


@pytest.mark.parametrize(
    "func",
    [
        P.freq_to_norm, P.norm_to_freq, P.gain_to_norm, P.norm_to_gain, P.q_to_norm,
        P.norm_to_q, P.norm_to_fader, P.delay_to_norm, P.norm_to_delay,
        P.quantize_freq, P.quantize_gain, P.quantize_q, P.quantize_fader, P.quantize_delay,
        P.snap_freq_hz, P.snap_gain_db, P.snap_q,
    ],
)  # fmt: skip
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_input_is_rejected(func, bad):
    with pytest.raises(ValueError, match="finite"):
        func(bad)


def test_fader_to_norm_rejects_nan():
    with pytest.raises(ValueError, match="NaN"):
        P.fader_to_norm(math.nan)


@pytest.mark.parametrize(
    ("x_norm", "expected"), [(-0.5, 0), (0.0, 0), (1.0, 200), (7.0, 200), (0.5, 100)]
)
def test_step_index_clamps_to_the_grid(x_norm, expected):
    assert P.step_index(x_norm, P.FREQ_STEPS) == expected


# ------------------------------------------------------------ properties
@pytest.mark.parametrize(
    "quantizer",
    [P.quantize_freq, P.quantize_gain, P.quantize_q, P.quantize_fader, P.quantize_delay],
)
@given(x_norm=st.floats(min_value=-2.0, max_value=3.0))
def test_quantize_is_idempotent_and_stays_in_range(quantizer, x_norm):
    once = quantizer(x_norm)

    assert 0.0 <= once <= 1.0
    assert quantizer(once) == once


@pytest.mark.parametrize(
    ("quantizer", "steps"),
    [
        (P.quantize_freq, P.FREQ_STEPS),
        (P.quantize_gain, P.GAIN_STEPS),
        (P.quantize_q, P.Q_STEPS),
        (P.quantize_fader, P.FADER_STEPS),
        (P.quantize_delay, P.DELAY_STEPS),
    ],
)
@given(x_norm=unit)
def test_quantize_moves_a_value_by_at_most_half_a_step(quantizer, steps, x_norm):
    assert abs(quantizer(x_norm) - x_norm) <= 0.5 / (steps - 1) + 1e-12


@given(a=unit, b=unit)
def test_quantize_is_monotonic(a, b):
    low, high = sorted((a, b))

    assert P.quantize_gain(low) <= P.quantize_gain(high)


@given(freq_hz=st.floats(min_value=20.0, max_value=20000.0))
def test_snap_freq_is_idempotent_and_within_half_a_grid_step(freq_hz):
    snapped = P.snap_freq_hz(freq_hz)
    half_step_ratio = 1000 ** (0.5 / 200)

    assert P.snap_freq_hz(snapped) == pytest.approx(snapped, rel=1e-12)
    assert 1 / half_step_ratio - 1e-9 <= snapped / freq_hz <= half_step_ratio + 1e-9


@given(gain_db=st.floats(min_value=-15.0, max_value=15.0))
def test_snap_gain_lands_on_quarter_db(gain_db):
    snapped = P.snap_gain_db(gain_db)

    assert abs(snapped - gain_db) <= 0.125 + 1e-9
    assert snapped * 4 == pytest.approx(round(snapped * 4), abs=1e-9)


@given(level_db=st.floats(min_value=-89.9, max_value=10.0))
def test_fader_law_is_monotonic_and_invertible(level_db):
    x_norm = P.fader_to_norm(level_db)

    assert 0.0 < x_norm <= 1.0
    assert P.norm_to_fader(x_norm) == pytest.approx(level_db, abs=1e-9)


# ------------------------------------------------------------ display
@pytest.mark.parametrize(
    ("freq_hz", "text"),
    [(20.0, "20.0"), (999.94, "999.9"), (1000.0, "1k00"), (1977.1, "1k97"), (20000.0, "20k00")],
)
def test_fmt_freq(freq_hz, text):
    assert P.fmt_freq(freq_hz) == text


@pytest.mark.parametrize(
    ("gain_db", "text"),
    [(0.0, "+0.00"), (-1.75, "-1.75"), (3.0, "+3.00"), (-14.75, "-14.7"), (-15.0, "-15.0"),
     (10.0, "+10.0"), (9.75, "+9.75")],
)  # fmt: skip
def test_fmt_gain(gain_db, text):
    assert P.fmt_gain(gain_db) == text


@pytest.mark.parametrize(
    ("level_db", "text"), [(-math.inf, "-oo"), (-37.5, "-37.5"), (0.0, "+0.0"), (2.04, "+2.0")]
)
def test_fmt_fader(level_db, text):
    assert P.fmt_fader(level_db) == text


@pytest.mark.parametrize(
    ("text", "freq_hz"),
    [("232.3", 232.3), ("1k97", 1970.0), ("16k25", 16250.0), (" 23.0 ", 23.0), ("2k", 2000.0)],
)
def test_parse_freq(text, freq_hz):
    assert P.parse_freq(text) == pytest.approx(freq_hz)


def test_describe_band_uses_console_formats():
    band = Band(2, EqType.PEQ, 479.76658380389813, -9.0, 1.5308693793684)

    assert P.describe_band(band) == "PEQ   479.8 Hz  -9.00 dB Q 1.5"


def test_x32_eq_grid_implements_the_quantizer_protocol():
    grid: EqQuantizer = P.X32EqGrid()

    assert grid.snap_freq_hz(480.0) == pytest.approx(479.7666, abs=1e-3)
    assert grid.snap_gain_db(-8.9) == -9.0
    assert grid.snap_q(1.5) == pytest.approx(1.5309, abs=1e-3)
