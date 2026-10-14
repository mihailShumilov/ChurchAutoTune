"""A simulated hall with four known defects, for the M4 acceptance (SPEC §15 M4).

Eight listening points, each with its own room. Two tops (natural 70 Hz roll-off, no crossover
filter) and one sub (natural 180 Hz low-pass, no crossover filter) play into it. The defects:

1. **Junction dip.** The sub arrives ``sub_extra_ms`` late, so sub and tops cancel around
   100 Hz in every point. Crossover and alignment must fix it.
2. **Hump at 500 Hz**, broad and common to every point (the room plus the speakers). System EQ
   must cut it.
3. **Mode at 50 Hz**, common to every point, in the sub. System EQ must cut it.
4. **Narrow null at 800 Hz** in two of the eight points only: a positional cancellation that EQ
   cannot fix and must leave alone.

The sub also arrives at slightly different times from seat to seat, is wired with inverted
polarity and plays 6 dB louder than the tops: alignment and level must find a compromise.

These are the speakers' own responses, before any console EQ. A test passes them through the EQ
the console has (that is what a microphone measures), applies proposed filters, delays and
polarity to each group and listens to the result again.

Simplifications, on purpose: one room per point shared by sub and tops (at low frequencies they
share its reflections), more direct sound and a drier low end than a typical church, so the
defects and not random seat-to-seat effects dominate.
"""

from dataclasses import dataclass

import numpy as np
from scipy import signal

from churchtune.audio.fake.room import DIRECT_DELAY_S, Filter, synthetic_room
from churchtune.domain.arrays import FloatArray
from churchtune.domain.eq import Band, EqType
from churchtune.domain.eq_model import band_sos

POINTS = tuple(f"p{n}" for n in range(1, 9))
TOP_ROLL_OFF_HZ = 70.0
SUB_ROLL_OFF_HZ = 180.0
LENGTH_S = 1.5
SEED = 4000
# More direct sound than the default synthetic hall: the junction cancellation of the real hall
# (-9..-13 dB in every point) lives in the direct sound.
DRR_DB = 14.0
# A moderately damped low end: the defects, not random positional effects of the reverberant
# field, must dominate below 125 Hz, or no defect there is common to the points.
RT60_S = {
    31.5: 1.0,
    63: 1.0,
    125: 1.2,
    250: 1.4,
    500: 1.4,
    1000: 1.3,
    2000: 1.2,
    4000: 1.0,
    8000: 0.8,
    16000: 0.6,
}


@dataclass(frozen=True, slots=True)
class Defects:
    """The defects; each can be switched off (``None`` or 0) to test one at a time.

    Attributes:
        sub_extra_ms: Extra arrival time of the sub (the junction dip).
        sub_spread_ms: How much that arrival varies from seat to seat (tops hang, the sub
            stands on the floor), deterministic per point within ±this.
        sub_inverted: The sub is wired with inverted polarity.
        sub_gain_db: The sub is this much louder than the tops (a common real problem).
        hump: Broad peaking filter in every point ``(freq_hz, gain_db, q)``.
        mode: Peaking resonance of the sub in every point.
        null: Narrow cut in the tops at ``null_points`` only.
        null_points: Points with the null.
    """

    sub_extra_ms: float | None = 6.0
    sub_spread_ms: float = 1.0
    sub_inverted: bool = True
    sub_gain_db: float = 6.0
    hump: Filter | None = (500.0, 5.0, 1.4)
    mode: Filter | None = (50.0, 9.0, 3.0)
    null: Filter | None = (800.0, -18.0, 10.0)
    null_points: tuple[str, ...] = ("p2", "p5")


@dataclass(frozen=True, slots=True)
class PointIrs:
    """Impulse responses of one point on a common time base.

    Attributes:
        tops: Both tops together.
        sub: The sub.
    """

    tops: FloatArray
    sub: FloatArray

    @property
    def both(self) -> FloatArray:
        """Everything as it normally plays."""
        return np.asarray(self.tops + self.sub, dtype=np.float64)


def _peaking(ir: FloatArray, spec: Filter | None, fs_hz: float) -> FloatArray:
    if spec is None:
        return ir
    freq_hz, gain_db, q = spec
    sos = band_sos(Band(2, EqType.PEQ, freq_hz, gain_db, q), fs_hz)
    return ir if sos is None else np.asarray(signal.sosfilt(sos, ir), dtype=np.float64)


def halton_like(index: int) -> float:
    """A spread-out value in [0, 1) for point ``index`` (radical inverse in base 3)."""
    result, fraction, n = 0.0, 1.0, index + 1
    while n:
        fraction /= 3
        result += fraction * (n % 3)
        n //= 3
    return result


def shifted(ir: FloatArray, samples: float) -> FloatArray:
    """The response arriving ``samples`` later (whole samples; the tail is cut)."""
    n = round(samples)
    if n <= 0:
        return ir
    return np.concatenate([np.zeros(n), ir[:-n]])


def defect_hall(fs_hz: float, defects: Defects = Defects()) -> dict[str, PointIrs]:  # noqa: B008
    """The eight points of the hall, deterministic."""
    top_sos = signal.butter(2, TOP_ROLL_OFF_HZ, "highpass", fs=fs_hz, output="sos")
    sub_sos = signal.butter(4, SUB_ROLL_OFF_HZ, "lowpass", fs=fs_hz, output="sos")
    extra_s = (defects.sub_extra_ms or 0.0) / 1000
    sub_scale = (-1.0 if defects.sub_inverted else 1.0) * 10 ** (defects.sub_gain_db / 20)
    points: dict[str, PointIrs] = {}
    for index, name in enumerate(POINTS):
        rng = np.random.default_rng(SEED + index)
        distance_s = 0.0002 * index  # the points are not equally far away
        # One room per point: at low frequencies sub and tops share its reflections; the sub
        # only arrives later.
        room = synthetic_room(
            rng,
            fs_hz,
            length_s=LENGTH_S,
            direct_delay_s=DIRECT_DELAY_S + distance_s,
            drr_db=DRR_DB,
            rt60_s=RT60_S,
        )
        spread_s = defects.sub_spread_ms / 1000 * (2 * halton_like(index) - 1)
        tops = room
        sub = shifted(room, (extra_s + spread_s) * fs_hz) * sub_scale
        tops = np.asarray(signal.sosfilt(top_sos, tops), dtype=np.float64)
        sub = np.asarray(signal.sosfilt(sub_sos, sub), dtype=np.float64)
        tops = _peaking(tops, defects.hump, fs_hz)
        sub = _peaking(_peaking(sub, defects.hump, fs_hz), defects.mode, fs_hz)
        if name in defects.null_points:
            tops = _peaking(tops, defects.null, fs_hz)
        points[name] = PointIrs(tops=tops, sub=sub)
    return points
