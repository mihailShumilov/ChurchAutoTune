"""X32/M32 parameter laws: normalized 0..1 values, quantization grids and display formats.

Formulas (P.-G. Maillot, "Unofficial X32/M32 OSC Remote Protocol"):
  freq  : f = 20 * 1000**x      201 steps (x = i/200)   log 20 Hz..20 kHz
  gain  : g = 30*x - 15         121 steps (0.25 dB)     -15..+15 dB
  Q     : Q = 10 * 0.03**x      72 steps  (x = i/71)    10..0.3 (log)
  type  : int enum, see EQ_TYPES
  fader : four linear segments  1024 steps              -oo..+10 dB
  delay : ms = 0.3 + 499.7*x    4998 steps (0.1 ms)     0.3..500 ms

The EQ grids were cross-checked against a real console dump (fw 4.09): "232.3", "1k97", "7k09",
"16k25", "23.0" and Q "3.9"/"2.0"/"6.4"/"7.4"/"7.8" are exact grid points. Always read back after
writing.
"""

import math
from dataclasses import dataclass

from churchtune.domain.eq import Band, EqType

# Index in this tuple is the integer the console uses for ``.../eq/N/type``. 0..5 are the classic
# types; 6..13 are the extra cut slopes of the bus/matrix/main EQ.
EQ_TYPES: tuple[EqType, ...] = (
    EqType.LCUT,
    EqType.LSHV,
    EqType.PEQ,
    EqType.VEQ,
    EqType.HSHV,
    EqType.HCUT,
    EqType.BU6,
    EqType.BU12,
    EqType.BS12,
    EqType.LR12,
    EqType.BU18,
    EqType.BU24,
    EqType.BS24,
    EqType.LR24,
)

FREQ_STEPS = 201
GAIN_STEPS = 121
Q_STEPS = 72
# UNVERIFIED: step count from Maillot; -37.5 and -29.3 dB read back correctly on fw 4.09
# (docs/hardware-checklist.md #7).
FADER_STEPS = 1024

FREQ_MIN_HZ, FREQ_MAX_HZ = 20.0, 20000.0
GAIN_MIN_DB, GAIN_MAX_DB = -15.0, 15.0
Q_MIN, Q_MAX = 0.3, 10.0
FADER_MAX_DB = 10.0
FADER_OFF_DB = -90.0
# UNVERIFIED: delay formula and step count are from Maillot and were never written on hardware
# (docs/hardware-checklist.md #1).
DELAY_MIN_MS, DELAY_MAX_MS = 0.3, 500.0
DELAY_STEP_MS = 0.1
DELAY_STEPS = round((DELAY_MAX_MS - DELAY_MIN_MS) / DELAY_STEP_MS) + 1

# Fader law: (lowest x_norm of the segment, slope dB per unit, offset dB), highest segment first.
_FADER_SEGMENTS: tuple[tuple[float, float, float], ...] = (
    (0.5, 40.0, -30.0),
    (0.25, 80.0, -50.0),
    (0.0625, 160.0, -70.0),
    (0.0, 480.0, -90.0),
)

_KILO = 1000.0
# Gains of 10 dB and more are shown with one decimal, truncated.
_GAIN_SHORT_FORMAT_FROM_DB = 10.0


def _finite(value: float, name: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return value


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


# ---------------------------------------------------------------- normalization
def freq_to_norm(freq_hz: float) -> float:
    """Frequency in Hz (clamped to 20..20000) to the normalized value."""
    freq_hz = _clamp(_finite(freq_hz, "freq_hz"), FREQ_MIN_HZ, FREQ_MAX_HZ)
    return math.log(freq_hz / FREQ_MIN_HZ) / math.log(FREQ_MAX_HZ / FREQ_MIN_HZ)


def norm_to_freq(x_norm: float) -> float:
    """Normalized value to frequency in Hz."""
    return float(FREQ_MIN_HZ * (FREQ_MAX_HZ / FREQ_MIN_HZ) ** _finite(x_norm, "x_norm"))


def gain_to_norm(gain_db: float) -> float:
    """EQ gain in dB (clamped to -15..+15) to the normalized value."""
    gain_db = _clamp(_finite(gain_db, "gain_db"), GAIN_MIN_DB, GAIN_MAX_DB)
    return (gain_db - GAIN_MIN_DB) / (GAIN_MAX_DB - GAIN_MIN_DB)


def norm_to_gain(x_norm: float) -> float:
    """Normalized value to EQ gain in dB."""
    return (GAIN_MAX_DB - GAIN_MIN_DB) * _finite(x_norm, "x_norm") + GAIN_MIN_DB


def q_to_norm(q: float) -> float:
    """Q (clamped to 0.3..10) to the normalized value; note that x = 0 is the highest Q."""
    q = _clamp(_finite(q, "q"), Q_MIN, Q_MAX)
    return math.log(q / Q_MAX) / math.log(Q_MIN / Q_MAX)


def norm_to_q(x_norm: float) -> float:
    """Normalized value to Q."""
    return float(Q_MAX * (Q_MIN / Q_MAX) ** _finite(x_norm, "x_norm"))


def norm_to_fader(x_norm: float) -> float:
    """Normalized fader value to level in dB; zero and below is ``-inf`` (fader down)."""
    x_norm = _finite(x_norm, "x_norm")
    if x_norm <= 0:
        return -math.inf
    for low, slope, offset in _FADER_SEGMENTS:
        if x_norm >= low:
            return x_norm * slope + offset
    raise AssertionError("unreachable: the last fader segment starts at 0")  # pragma: no cover


def fader_to_norm(level_db: float) -> float:
    """Fader level in dB to the normalized value; ``-inf`` and -90 dB or lower map to 0."""
    if math.isnan(level_db):
        raise ValueError("level_db must not be NaN")
    if level_db <= FADER_OFF_DB:
        return 0.0
    for low, slope, offset in _FADER_SEGMENTS:
        if level_db >= low * slope + offset:
            return _clamp((level_db - offset) / slope, 0.0, 1.0)
    raise AssertionError("unreachable: levels above -90 dB fall in a segment")  # pragma: no cover


def delay_to_norm(delay_ms: float) -> float:
    """Output delay in ms (clamped to 0.3..500) to the normalized value."""
    delay_ms = _clamp(_finite(delay_ms, "delay_ms"), DELAY_MIN_MS, DELAY_MAX_MS)
    return (delay_ms - DELAY_MIN_MS) / (DELAY_MAX_MS - DELAY_MIN_MS)


def norm_to_delay(x_norm: float) -> float:
    """Normalized value to output delay in ms."""
    return DELAY_MIN_MS + _finite(x_norm, "x_norm") * (DELAY_MAX_MS - DELAY_MIN_MS)


# ---------------------------------------------------------------- quantization
def step_index(x_norm: float, steps: int) -> int:
    """Index of the grid step nearest to ``x_norm`` (clamped to 0..1) on a grid of ``steps``."""
    return round(_clamp(_finite(x_norm, "x_norm"), 0.0, 1.0) * (steps - 1))


def quantize(x_norm: float, steps: int) -> float:
    """Snap a normalized value to a grid of ``steps`` equally spaced values in 0..1."""
    return step_index(x_norm, steps) / (steps - 1)


def quantize_freq(x_norm: float) -> float:
    """Snap a normalized EQ frequency to the console grid."""
    return quantize(x_norm, FREQ_STEPS)


def quantize_gain(x_norm: float) -> float:
    """Snap a normalized EQ gain to the console grid."""
    return quantize(x_norm, GAIN_STEPS)


def quantize_q(x_norm: float) -> float:
    """Snap a normalized Q to the console grid."""
    return quantize(x_norm, Q_STEPS)


def quantize_fader(x_norm: float) -> float:
    """Snap a normalized fader value to the console grid."""
    return quantize(x_norm, FADER_STEPS)


def quantize_delay(x_norm: float) -> float:
    """Snap a normalized output delay to the console grid."""
    return quantize(x_norm, DELAY_STEPS)


def snap_freq_hz(freq_hz: float) -> float:
    """Nearest EQ frequency the console can store, in Hz."""
    return norm_to_freq(quantize_freq(freq_to_norm(freq_hz)))


def snap_gain_db(gain_db: float) -> float:
    """Nearest EQ gain the console can store, in dB."""
    return norm_to_gain(quantize_gain(gain_to_norm(gain_db)))


def snap_q(q: float) -> float:
    """Nearest Q the console can store."""
    return norm_to_q(quantize_q(q_to_norm(q)))


@dataclass(frozen=True, slots=True)
class X32EqGrid:
    """The X32/M32 EQ value grid as an ``EqQuantizer`` for the design layer."""

    def snap_freq_hz(self, freq_hz: float) -> float:
        """Nearest EQ frequency the console can store, in Hz."""
        return snap_freq_hz(freq_hz)

    def snap_gain_db(self, gain_db: float) -> float:
        """Nearest EQ gain the console can store, in dB."""
        return snap_gain_db(gain_db)

    def snap_q(self, q: float) -> float:
        """Nearest Q the console can store."""
        return snap_q(q)


# ---------------------------------------------------------------- display / parse
def fmt_freq(freq_hz: float) -> str:
    """Console style: ``232.3``, ``1k97``, ``16k25`` (1 kHz and above truncated to 10 Hz)."""
    if freq_hz < _KILO - 0.05:
        return f"{freq_hz:.1f}"
    tens = int(freq_hz / 10.0 + 1e-6)
    return f"{tens // 100}k{tens % 100:02d}"


def fmt_gain(gain_db: float) -> str:
    """Console style: ``+0.00``, ``-1.75``; from 10 dB one truncated decimal (``-14.7``)."""
    if abs(gain_db) >= _GAIN_SHORT_FORMAT_FROM_DB - 1e-9:
        truncated = math.trunc(abs(gain_db) * 10 + 1e-6) / 10
        return f"{'-' if gain_db < 0 else '+'}{truncated:.1f}"
    return f"{gain_db:+.2f}"


def fmt_q(q: float) -> str:
    """Console style: one decimal."""
    return f"{q:.1f}"


def fmt_fader(level_db: float) -> str:
    """Console style: ``-oo`` or a signed level with one decimal."""
    return "-oo" if level_db == -math.inf else f"{level_db:+.1f}".replace("+-", "-")


def parse_freq(text: str) -> float:
    """Parse a console frequency string (``232.3``, ``1k97``, ``16k25``) into Hz."""
    text = text.strip()
    if "k" in text:
        whole, frac = text.split("k", 1)
        return float(whole or 0) * _KILO + (float("0." + frac) * _KILO if frac else 0.0)
    return float(text)


def describe_band(band: Band) -> str:
    """One band as the console shows it: ``PEQ   479.8 Hz  -9.00 dB Q 1.5``."""
    return (
        f"{band.type:<4} {fmt_freq(band.freq_hz):>6} Hz "
        f"{fmt_gain(band.gain_db):>6} dB Q {fmt_q(band.q)}"
    )
