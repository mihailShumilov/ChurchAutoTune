"""System-EQ proposal by joint optimisation, starting from the greedy design.

Frequency, gain and Q of all adjustable bands are optimised together. Objective: excess above
the target counts fully, shortfall half (nulls are not chased); points that disagree (spread)
and frequencies below 70 Hz weigh less; a small penalty on gain, and one on two working cuts
closer than half the wider one's width (at least 1/6 octave), which would otherwise spread one
cut over several identical bands. Bounds keep the rules: cuts -9..0 dB, boosts 0..+3 dB with Q
up to 1.4, narrow existing notches keep at least half their depth and stay where they are.

The objective has several local minima of almost the same cost, and which one the solver
reaches can hinge on floating-point noise (another platform). So the solver also starts from the
solution with one cut fewer (two cuts on one problem merged, or one taken out) and with an idle
band put to work, and every finished proposal (on the console grid, uncommon bands put back) gets
one number: the weighted RMS deviation, plus a little per dB of gain, plus 0.05 dB for every
second cut within half an octave of another. The lowest number wins, so a second cut on one
problem stays only when it buys more than that. That this picks the same proposal on every
platform is checked empirically (noise tests), not guaranteed. After the optimisation every
changed band must still address a problem common to the points, or it is put back.
"""

import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass, replace

import numpy as np
from scipy.optimize import least_squares

from churchtune.analysis.points import Analysis
from churchtune.design.proposal import (
    Action,
    BandProposal,
    CandidateKind,
    Method,
    Prediction,
    Proposal,
    Reason,
    ReasonCode,
    SkipCode,
    Skipped,
    TargetSpec,
)
from churchtune.design.system_eq import (
    DEFAULT_CONFIG,
    DEFAULT_TARGET,
    DesignInput,
    design,
    design_input,
)
from churchtune.design.target import EqDesignConfig
from churchtune.domain.arrays import FloatArray
from churchtune.domain.eq import Band, EqQuantizer, EqType, SystemEq
from churchtune.domain.eq_model import band_response_db, eq_response_db

# A band counts as "set" above this gain.
SET_GAIN_DB = 0.1
START_GAIN_DB = -0.5
BOOST_Q_RANGE = (0.5, 1.4)
BOOST_FREQ_RANGE_HZ_LOW = 150.0
CUT_Q_RANGE = (0.5, 8.0)
# A kept notch may move by no more than this factor in frequency and Q (that is: not at all).
NOTCH_TOLERANCE = 1.0005
# Start values are kept this share of the range away from the bounds (solver requirement).
BOUND_MARGIN = 0.01
MIN_BOUND_WIDTH = 1e-6
SPREAD_WEIGHT_SCALE_DB = 3.0
SHORTFALL_WEIGHT = 0.5
GAIN_PENALTY = 0.15
RESIDUAL_SCALE = 10.0
# Optimised gains below this are set to zero.
MIN_GAIN_DB = 0.5
ZONE_HALF_OCT = 1 / 3
MIN_ZONE_HALF_OCT = 1 / 12
MODE_COMMON = 0.5
# Rounds of trying one band fewer or an idle band put to work, and solving again.
RESEED_ROUNDS = 3
RESEED_Q = 2.0
# Split halves start this many overlap spacings apart.
SPLIT_SPACING = 1.2
MODE_PAIR = 2  # two cuts on one problem
# What the score charges, in dB of weighted RMS deviation: per second cut on one problem (within
# CROWDED_OCT of another; it has to buy more than this) and per dB of gain (linear: two half bands
# cost what one does; small, a tie-break: low frequencies weigh little in the deviation, and a
# room-mode cut must not be priced out).
BAND_COST_DB = 0.05
CROWDED_OCT = 1 / 2
GAIN_COST_DB = 0.001
# Solver evaluations for a reseeded start (the plain solve keeps scipy's default).
RESEED_MAX_NFEV = 100
# Two cuts closer than this act on one problem: the solver is penalised for it.
MIN_CUT_SPACING_OCT = 1 / 6
# On the console grid (about 1/20 octave a step) two such cuts may come two steps closer; closer
# than this they count as stacked, and a reseeded proposal may not have more than the plain one.
STACKED_OCT = 1 / 8
OVERLAP_PENALTY = 2.0
_SKIPS_KEPT_FROM_GREEDY = frozenset({SkipCode.NARROW_DIP, SkipCode.LOCAL_PEAK})


class _Kind:
    BOOST = "boost"
    NOTCH = "notch"
    CUT = "cut"


@dataclass(frozen=True, slots=True)
class _Problem:
    """The optimisation problem: which slots move, where they start and within which bounds."""

    slots: tuple[int, ...]
    kinds: dict[int, str]
    x0: tuple[float, ...]
    lower: tuple[float, ...]
    upper: tuple[float, ...]


def _spacing_oct(q: float) -> float:
    """How far apart two cuts must be to act on different problems: half the wider one's width."""
    return max(MIN_CUT_SPACING_OCT, 1 / (2 * q))


def _overlap_residuals(x: FloatArray, pairs: list[tuple[int, int]]) -> FloatArray:
    """Residuals that grow as two working cuts come closer than ``_spacing_oct``.

    Two cuts on one spot do what one deeper cut does, and the solver would happily spread a cut
    over several bands; scaled by both gains, so a flat (idle) band may sit anywhere.
    """
    values = np.zeros(len(pairs))
    for k, (i, j) in enumerate(pairs):
        distance_oct = abs(x[3 * i] - x[3 * j]) / math.log(2)
        spacing_oct = _spacing_oct(math.exp(min(x[3 * i + 2], x[3 * j + 2])))
        closeness = max(0.0, spacing_oct - distance_oct) / spacing_oct
        values[k] = OVERLAP_PENALTY * closeness * math.sqrt(abs(x[3 * i + 1] * x[3 * j + 1]))
    return values


def _inside(value: float, low: float, high: float) -> float:
    """Clip a start value strictly inside ``[low, high]``."""
    margin = (high - low) * BOUND_MARGIN
    return float(np.clip(value, low + margin, high - margin))


def _problem(
    start: SystemEq, eq_meas: SystemEq, f_from_hz: float, cfg: EqDesignConfig, keep_notches: bool
) -> _Problem:
    slots = tuple(b.band for b in start.bands if eq_meas.band(b.band).adjustable)
    kinds: dict[int, str] = {}
    x0: list[float] = []
    lower: list[float] = []
    upper: list[float] = []
    for n in slots:
        new, old = start.band(n), eq_meas.band(n)
        is_set = abs(new.gain_db) > SET_GAIN_DB
        boost = new.gain_db > SET_GAIN_DB or (not is_set and old.gain_db > SET_GAIN_DB)
        notch = keep_notches and old.gain_db <= -cfg.active_gain_db and old.q >= cfg.notch_q
        freq0 = new.freq_hz if is_set else old.freq_hz
        old_or_start = old.gain_db if abs(old.gain_db) > SET_GAIN_DB else START_GAIN_DB
        gain0 = new.gain_db if is_set else old_or_start
        q0 = new.q if is_set else old.q
        if boost:
            kinds[n] = _Kind.BOOST
            gain_low, gain_high = 0.0, cfg.max_boost_db
            q_low, q_high = BOOST_Q_RANGE
            freq_low, freq_high = BOOST_FREQ_RANGE_HZ_LOW, cfg.approx_above_hz
        elif notch:
            # Frequency and Q stay exactly where they were (the notch protects that frequency);
            # only its depth may halve.
            kinds[n] = _Kind.NOTCH
            gain_low, gain_high = old.gain_db, old.gain_db / 2
            q_low, q_high = old.q / NOTCH_TOLERANCE, old.q * NOTCH_TOLERANCE
            freq_low, freq_high = old.freq_hz / NOTCH_TOLERANCE, old.freq_hz * NOTCH_TOLERANCE
        else:
            kinds[n] = _Kind.CUT
            gain_low, gain_high = cfg.max_cut_db, 0.0
            q_low, q_high = CUT_Q_RANGE
            freq_low, freq_high = f_from_hz, cfg.max_hz
        x0 += [
            _inside(math.log(freq0), math.log(freq_low), math.log(freq_high)),
            _inside(gain0, gain_low, gain_high),
            _inside(math.log(q0), math.log(q_low), math.log(q_high)),
        ]
        lower += [math.log(freq_low), gain_low, math.log(q_low)]
        upper += [math.log(freq_high), max(gain_high, gain_low + MIN_BOUND_WIDTH), math.log(q_high)]
    return _Problem(slots, kinds, tuple(x0), tuple(lower), tuple(upper))


def _moved(
    x: FloatArray, slot: int, values: tuple[float, float, float], problem: _Problem
) -> FloatArray:
    """``x`` with one slot set to (log frequency, gain, log Q), strictly inside its bounds."""
    moved = np.array(x, dtype=np.float64)
    for k, value in enumerate(values):
        j = 3 * slot + k
        moved[j] = _inside(value, problem.lower[j], problem.upper[j])
    return moved


_Bounds = tuple[tuple[float, ...], tuple[float, ...]]


def _working_cuts(x: FloatArray, problem: _Problem) -> list[int]:
    return [
        i
        for i, n in enumerate(problem.slots)
        if problem.kinds[n] == _Kind.CUT and x[3 * i + 1] <= -MIN_GAIN_DB
    ]


def _held_flat(x: FloatArray, problem: _Problem, removed: int) -> tuple[FloatArray, _Bounds]:
    """Start and bounds with ``removed`` and every idle cut held at 0 dB.

    Holding only the removed slot would let the solver move the same cut into an idle one.
    """
    lower, upper = list(problem.lower), list(problem.upper)
    start = np.array(x, dtype=np.float64)
    working = set(_working_cuts(x, problem)) - {removed}
    for i, n in enumerate(problem.slots):
        if problem.kinds[n] == _Kind.CUT and i not in working:
            lower[3 * i + 1], upper[3 * i + 1] = -MIN_BOUND_WIDTH, 0.0
            start[3 * i + 1] = -MIN_BOUND_WIDTH / 2
    return start, (tuple(lower), tuple(upper))


def _fewer_band_starts(
    x: FloatArray, problem: _Problem, modes_below_hz: float
) -> list[tuple[FloatArray, _Bounds]]:
    """Starts with one working cut fewer, in a fixed order.

    First every pair of cuts on one problem (closer than ``MIN_CUT_SPACING_OCT``, or within a
    third of an octave in the mode zone) merged into one: centred between them by gain, both
    gains, widened by their distance. Then each working cut taken out, the shallowest first. The
    way back from a split: without it, which of two almost equal minima is proposed would depend
    on where the solver happened to start.
    """
    working = _working_cuts(x, problem)
    if len(working) < MODE_PAIR:
        return []
    starts = []
    for one, two in itertools.combinations(working, MODE_PAIR):
        distance_oct = abs(x[3 * one] - x[3 * two]) / math.log(2)
        in_modes = max(x[3 * one], x[3 * two]) < math.log(modes_below_hz)
        if distance_oct < MIN_CUT_SPACING_OCT or (in_modes and distance_oct < ZONE_HALF_OCT):
            deep, shallow = (one, two) if x[3 * one + 1] <= x[3 * two + 1] else (two, one)
            g_deep, g_shallow = x[3 * deep + 1], x[3 * shallow + 1]
            centre = (g_deep * x[3 * deep] + g_shallow * x[3 * shallow]) / (g_deep + g_shallow)
            q = 1 / (1 / math.exp(x[3 * deep + 2]) + distance_oct)
            merged = _moved(x, deep, (centre, g_deep + g_shallow, math.log(q)), problem)
            starts.append(_held_flat(merged, problem, shallow))
    shallow_first = sorted(working, key=lambda i: (-x[3 * i + 1], i))
    starts.extend(_held_flat(x, problem, i) for i in shallow_first)
    return starts


def _spare_slot(
    x: FloatArray, problem: _Problem, active: Callable[[FloatArray, int], bool]
) -> int | None:
    """The first cut slot that does nothing in the corrected range, if any."""
    for i, n in enumerate(problem.slots):
        if problem.kinds[n] == _Kind.CUT and not active(x, i):
            return i
    return None


def _reseed_starts(
    spare: tuple[int, FloatArray],
    excess_db: FloatArray,
    grid_hz: FloatArray,
    problem: _Problem,
    *,
    modes_below_hz: float,
) -> list[FloatArray]:
    """Starts with the spare slot put to work, in a fixed order.

    First at the largest remaining excess above the mode zone; then splitting each active cut
    (deepest first) into two halves a little more than the overlap spacing apart, which reaches
    the "two narrower cuts" minima a single wide cut hides. The mode zone gets neither: a room mode
    gets one compromise cut. ``excess_db`` is the weighted excess (weights up to 1), so the
    start gain is no deeper than the real excess; the solver takes it from there.
    """
    slot, x = spare
    starts = []
    above_modes_db = np.where(grid_hz >= modes_below_hz, excess_db, 0.0)
    if np.any(above_modes_db > 0):
        peak = int(np.argmax(above_modes_db))
        gain = -float(above_modes_db[peak])
        values = (math.log(float(grid_hz[peak])), gain, math.log(RESEED_Q))
        starts.append(_moved(x, slot, values, problem))
    cuts = [
        i
        for i, n in enumerate(problem.slots)
        if problem.kinds[n] == _Kind.CUT
        and i != slot
        and x[3 * i + 1] <= -MIN_GAIN_DB
        and math.exp(x[3 * i]) >= modes_below_hz
    ]
    for i in sorted(cuts, key=lambda i: (x[3 * i + 1], i)):
        log_freq, gain, log_q = x[3 * i : 3 * i + 3]
        # Halves a little more than the overlap spacing apart, so they start as two problems.
        shift = math.log(2) * SPLIT_SPACING * _spacing_oct(math.exp(log_q)) / 2
        half = (log_freq - shift, gain / 2, log_q)
        split = _moved(x, i, half, problem)
        starts.append(_moved(split, slot, (log_freq + shift, gain / 2, half[2]), problem))
    return starts


@dataclass(frozen=True, slots=True)
class _Score:
    """How good the finished proposal of a solution is.

    Attributes:
        weighted_rms_db: RMS of the deviation as the solver weighs it (shortfall half, spread and
            low frequencies less), without the penalties.
        gain_db: Sum of the absolute gains of the changed bands (counted linearly, so two half
            bands cost no less than one).
        stacked: How often new cuts sit on top of each other (``_stacked``).
        crowded: How many new cuts sit within ``CROWDED_OCT`` of the previous one: a second cut on
            one problem.
    """

    weighted_rms_db: float
    gain_db: float
    stacked: int
    crowded: int

    @property
    def total_db(self) -> float:
        """One number per proposal, so the choice between candidates is a total order.

        The weighted deviation, plus ``GAIN_COST_DB`` per dB of gain, plus ``BAND_COST_DB`` for
        every second cut on one problem and every stacked pair. Cuts on different problems cost
        nothing extra.
        """
        extra = self.crowded + self.stacked
        return self.weighted_rms_db + GAIN_COST_DB * self.gain_db + BAND_COST_DB * extra


def _crowded(cuts: list[Band]) -> int:
    """How many cuts sit within ``CROWDED_OCT`` of their lower neighbour."""
    freqs = sorted(b.freq_hz for b in cuts)
    return sum(1 for low, high in itertools.pairwise(freqs) if math.log2(high / low) < CROWDED_OCT)


def _stacked(bands: list[Band], grid_hz: FloatArray, fs_hz: float, max_cut_db: float) -> int:
    """How many times new cuts sit on top of each other.

    Each pair of neighbouring cuts closer than ``STACKED_OCT`` counts (one problem cut
    twice), and one more when together they cut deeper than one band may (``max_cut_db``).
    """
    cuts = sorted((b for b in bands if b.gain_db < 0), key=lambda b: b.freq_hz)
    close = sum(
        1
        for low, high in itertools.pairwise(cuts)
        if math.log2(high.freq_hz / low.freq_hz) < STACKED_OCT
    )
    if not cuts:
        return 0
    total = sum(band_response_db(b, grid_hz, fs_hz) for b in cuts)
    return close + int(np.min(total) < max_cut_db - MIN_GAIN_DB)


def _solve(  # noqa: PLR0913 - the solver needs the problem and the corrected range
    residual: Callable[[FloatArray], FloatArray],
    excess: Callable[[FloatArray], FloatArray],
    score: Callable[[FloatArray], _Score],
    problem: _Problem,
    *,
    grid_hz: FloatArray,
    active: Callable[[FloatArray, int], bool],
    modes_below_hz: float,
) -> FloatArray:
    """Solve from the greedy start, then try one band fewer or a spare band put to work.

    ``excess`` gives the weighted excess above the target (zero outside the corrected range),
    ``score`` judges the finished proposal of a solution (snapped, uncommon bands put back) and
    ``active`` tells whether a slot does anything in the corrected range. Every round tries all
    moves from the best solution so far and keeps the lowest ``_Score.total_db``: one total order,
    so the result does not depend on the order the moves were tried in. A proposal never has
    more stacked cuts than the plain solve's. The procedure is deterministic.
    """
    bounds = (problem.lower, problem.upper)
    solution = least_squares(residual, problem.x0, bounds=bounds)
    best = np.asarray(solution.x, dtype=np.float64)
    plain = score(best)
    best_total = plain.total_db
    for _ in range(RESEED_ROUNDS):
        starts = _fewer_band_starts(best, problem, modes_below_hz)
        spare = _spare_slot(best, problem, active)
        if spare is not None:
            moves = _reseed_starts(
                (spare, best), excess(best), grid_hz, problem, modes_below_hz=modes_below_hz
            )
            starts += [(start, bounds) for start in moves]
        round_best, round_total = best, best_total
        for start, limits in starts:
            solution = least_squares(residual, start, bounds=limits, max_nfev=RESEED_MAX_NFEV)
            candidate = np.asarray(solution.x, dtype=np.float64)
            candidate_score = score(candidate)
            total = candidate_score.total_db
            if candidate_score.stacked <= plain.stacked and total < round_total:
                round_best, round_total = candidate, total
        if round_total >= best_total:
            break
        best, best_total = round_best, round_total
    return best


def _eq_from(x: FloatArray, problem: _Problem, eq_meas: SystemEq) -> SystemEq:
    bands = list(eq_meas.bands)
    for i, n in enumerate(problem.slots):
        log_freq, gain, log_q = x[3 * i : 3 * i + 3]
        eq_type = eq_meas.band(n).type if problem.kinds[n] == _Kind.NOTCH else EqType.PEQ
        bands[n - 1] = Band(
            n, eq_type, float(math.exp(log_freq)), float(gain), float(math.exp(log_q))
        )
    return SystemEq(True, tuple(bands))


def _snap(eq: SystemEq, problem: _Problem, quantizer: EqQuantizer, eq_meas: SystemEq) -> list[Band]:
    """Put the optimised bands on the console grid; tiny gains become zero.

    A band that was flat and stays flat keeps its old frequency and Q: moving a band that does
    nothing would only be a write to the console.
    """
    snapped = []
    for band, old in zip(eq.bands, eq_meas.bands, strict=True):
        if not band.adjustable:
            snapped.append(band)
            continue
        gain = quantizer.snap_gain_db(band.gain_db)
        if abs(gain) < MIN_GAIN_DB and problem.kinds.get(band.band) != _Kind.NOTCH:
            gain = 0.0
        if gain == 0 and old.gain_db == 0:
            snapped.append(old)
            continue
        snapped.append(
            replace(
                band,
                freq_hz=quantizer.snap_freq_hz(band.freq_hz),
                gain_db=gain,
                q=quantizer.snap_q(band.q),
            )
        )
    return snapped


@dataclass(frozen=True, slots=True)
class _Zones:
    """Mean deviation in a third of an octave around a frequency, within the corrected range."""

    grid_hz: FloatArray
    mask: np.ndarray

    def mean(self, curve: FloatArray, freq_hz: float, half_oct: float = ZONE_HALF_OCT) -> float:
        near = (
            (self.grid_hz >= freq_hz * 2**-half_oct)
            & (self.grid_hz <= freq_hz * 2**half_oct)
            & self.mask
        )
        return float(curve[near].mean()) if near.any() else 0.0

    def points_above(
        self,
        point_devs: tuple[FloatArray, ...],
        freq_hz: float,
        db: float,
        half_oct: float = ZONE_HALF_OCT,
    ) -> int:
        return sum(1 for d in point_devs if self.mean(d, freq_hz, half_oct) > db)

    def points_below(
        self,
        point_devs: tuple[FloatArray, ...],
        freq_hz: float,
        db: float,
        half_oct: float = ZONE_HALF_OCT,
    ) -> int:
        return sum(1 for d in point_devs if self.mean(d, freq_hz, half_oct) < db)


def band_half_oct(q: float) -> float:
    """Half the width a band acts on, in octaves.

    1/(2Q), never narrower than 1/12 octave and never wider than the third of an octave the rule
    used before.

    The commonness rule asks whether the points show the feature where the band acts; averaging
    a narrow resonance over a fixed wide zone would wash it out and mistake narrow for local.
    """
    return min(ZONE_HALF_OCT, max(MIN_ZONE_HALF_OCT, 1 / (2 * q)))


def _revert_uncommon(  # noqa: PLR0913 - one pass over everything the rule check needs
    bands: list[Band],
    eq_meas: SystemEq,
    problem: _Problem,
    *,
    data: DesignInput,
    zones: _Zones,
    cfg: EqDesignConfig,
) -> list[Skipped]:
    """Put back every changed band that does not address a problem common to the points."""
    reverted: list[Skipped] = []
    n_points = len(data.point_devs_db)
    for i, new in enumerate(bands):
        old = eq_meas.bands[i]
        untouched = old == new or new.gain_db == 0 or problem.kinds.get(new.band) == _Kind.NOTCH
        if not old.adjustable or untouched:
            continue
        if new.gain_db < 0:
            n_bad = zones.points_above(
                data.point_devs_db, new.freq_hz, cfg.peak_point_db, band_half_oct(new.q)
            )
            share = MODE_COMMON if new.freq_hz < cfg.mode_below_hz else cfg.peak_common
        else:
            n_bad = zones.points_below(data.point_devs_db, new.freq_hz, -cfg.dip_point_db)
            share = cfg.dip_common
        need = math.ceil(n_points * share)
        if n_bad < need:
            half_width = 1 / (2 * new.q)
            reverted.append(
                Skipped(
                    kind=CandidateKind.BOOST if new.gain_db > 0 else CandidateKind.CUT,
                    freq_hz=round(new.freq_hz, 1),
                    dev_db=round(zones.mean(data.dev_db, new.freq_hz), 2),
                    width_oct=round(math.log2((1 + half_width) / max(1 - half_width, 1e-3)), 2),
                    points=n_bad,
                    reason=Reason(
                        SkipCode.NOT_COMMON,
                        {
                            "n": n_bad,
                            "n_points": n_points,
                            "need": need,
                            "gain_db": new.gain_db,
                            "freq_hz": new.freq_hz,
                            "q": new.q,
                        },
                    ),
                )
            )
            bands[i] = old
        elif new.freq_hz < cfg.mode_below_hz and new.gain_db < cfg.mode_max_cut_db:
            # Modes are position dependent: a compromise depth.
            bands[i] = replace(new, gain_db=cfg.mode_max_cut_db)
    return reverted


def _band_proposal(  # noqa: PLR0913 - one band needs the whole picture for its reason
    old: Band,
    new: Band,
    kind: str | None,
    *,
    data: DesignInput,
    after: FloatArray,
    zones: _Zones,
    cfg: EqDesignConfig,
) -> BandProposal:
    if not old.adjustable:
        return BandProposal(new, old, Action.KEEP, False, Reason(ReasonCode.LOCKED_CUT_FILTER, {}))
    if old == new:
        return BandProposal(new, old, Action.KEEP, False, Reason(ReasonCode.UNCHANGED, {}))
    action = Action.MODIFY if abs(old.gain_db) >= cfg.active_gain_db else Action.NEW
    approximate = new.freq_hz > cfg.approx_above_hz and abs(new.gain_db) > 0
    freq = new.freq_hz if abs(new.gain_db) > 0 else old.freq_hz
    if new.gain_db < 0:
        n_bad = zones.points_above(data.point_devs_db, freq, cfg.peak_point_db)
    else:
        n_bad = zones.points_below(data.point_devs_db, freq, -cfg.dip_point_db)
    if kind == _Kind.NOTCH:
        reason = Reason(
            ReasonCode.NOTCH_KEPT, {"old_gain_db": old.gain_db, "new_gain_db": new.gain_db}
        )
    elif new.gain_db == 0:
        reason = Reason(
            ReasonCode.NOT_NEEDED,
            {"freq_hz": old.freq_hz, "after_db": zones.mean(after, old.freq_hz)},
        )
    else:
        reason = Reason(
            ReasonCode.OPTIMIZED,
            {
                "gain_db": new.gain_db,
                "freq_hz": new.freq_hz,
                "q": new.q,
                "before_db": zones.mean(data.dev_db, new.freq_hz),
                "after_db": zones.mean(after, new.freq_hz),
                "n": n_bad,
                "n_points": len(data.point_devs_db),
                "mode_zone": new.freq_hz < cfg.mode_below_hz and new.gain_db < 0,
                "approximate": approximate,
            },
        )
    return BandProposal(new, old, action, approximate, reason)


def optimize_design(  # noqa: PLR0913 - explicit inputs instead of globals
    an: Analysis,
    eq_meas: SystemEq,
    quantizer: EqQuantizer,
    fs_hz: float,
    *,
    target: TargetSpec = DEFAULT_TARGET,
    eq_now: SystemEq | None = None,
    cfg: EqDesignConfig = DEFAULT_CONFIG,
    keep_notches: bool = True,
) -> Proposal:
    """System-EQ design by joint optimisation of all adjustable bands.

    Args:
        an: Analysis of the measured session.
        eq_meas: EQ that was active during the measurement.
        quantizer: The console's EQ value grid; every proposed value lies on it.
        fs_hz: Sample rate used for the EQ response model.
        target: Target curve.
        eq_now: EQ on the console now, if read; a difference to ``eq_meas`` is flagged.
        cfg: Design thresholds.
        keep_notches: Treat narrow existing cuts as feedback notches that keep half their depth.

    Returns:
        The proposal. ``prediction.max_dev_*`` is the largest excess above the target (dips are
        not filled by design).
    """
    greedy = design(an, eq_meas, quantizer, fs_hz, target=target, eq_now=eq_now, cfg=cfg)
    data = design_input(an, eq_meas, fs_hz, target, cfg)
    grid, dev = data.grid_hz, data.dev_db
    mask = (grid >= data.f_correct_from_hz) & (grid <= cfg.approx_above_hz)
    weight = 1.0 / (1.0 + (an.spread_db / SPREAD_WEIGHT_SCALE_DB) ** 2)
    weight = np.where(grid < cfg.low_weight_below_hz, cfg.low_weight * weight, weight)
    problem = _problem(greedy.to_eq(), eq_meas, data.f_correct_from_hz, cfg, keep_notches)
    norm = math.sqrt(max(int(mask.sum()), 1))
    n_slots = len(problem.slots)
    cuts = [i for i, n in enumerate(problem.slots) if problem.kinds[n] == _Kind.CUT]
    cut_pairs = [(i, j) for i, j in itertools.combinations(cuts, MODE_PAIR)]

    def after_of(x: FloatArray) -> FloatArray:
        response = eq_response_db(_eq_from(x, problem, eq_meas), grid, fs_hz)
        return np.asarray(dev + response - data.old_response_db, dtype=np.float64)

    def residual(x: FloatArray) -> FloatArray:
        after = after_of(x)
        weighted = np.where(after > 0, after, SHORTFALL_WEIGHT * after) * weight
        gains = np.array([x[3 * i + 1] for i in range(n_slots)])
        penalty = GAIN_PENALTY * gains
        overlap = _overlap_residuals(x, cut_pairs)
        return np.concatenate([weighted[mask] / norm * RESIDUAL_SCALE, penalty, overlap])

    def excess(x: FloatArray) -> FloatArray:
        return np.asarray(np.where(mask, np.maximum(after_of(x), 0.0) * weight, 0.0))

    zones = _Zones(grid, mask)

    def finish(x: FloatArray | None) -> tuple[list[Band], list[Skipped], FloatArray]:
        """The proposal of a solution: on the console grid, uncommon bands put back."""
        optimised = (
            _eq_from(x, problem, eq_meas) if x is not None else SystemEq(True, eq_meas.bands)
        )
        bands = _snap(optimised, problem, quantizer, eq_meas)
        reverted = _revert_uncommon(bands, eq_meas, problem, data=data, zones=zones, cfg=cfg)
        response = eq_response_db(SystemEq(True, tuple(bands)), grid, fs_hz)
        return bands, reverted, np.asarray(dev + response - data.old_response_db)

    def score(x: FloatArray) -> _Score:
        bands, _, after = finish(x)
        changed = [b for b, old in zip(bands, eq_meas.bands, strict=True) if b != old]
        weighted = np.where(after > 0, after, SHORTFALL_WEIGHT * after) * weight
        return _Score(
            weighted_rms_db=float(np.sqrt(np.mean(weighted[mask] ** 2))),
            gain_db=sum(abs(b.gain_db) for b in changed),
            stacked=_stacked([b for b in changed if b.gain_db], grid, fs_hz, cfg.max_cut_db),
            crowded=_crowded([b for b in changed if b.gain_db < 0]),
        )

    def active(x: FloatArray, slot: int) -> bool:
        band = _eq_from(x, problem, eq_meas).bands[problem.slots[slot] - 1]
        return bool(np.max(np.abs(band_response_db(band, grid, fs_hz)[mask])) >= MIN_GAIN_DB)

    x = None
    if problem.slots:
        x = _solve(
            residual,
            excess,
            score,
            problem,
            grid_hz=grid,
            active=active,
            modes_below_hz=cfg.mode_below_hz,
        )
    bands, reverted, after = finish(x)
    proposals = tuple(
        _band_proposal(
            old, new, problem.kinds.get(new.band), data=data, after=after, zones=zones, cfg=cfg
        )
        for old, new in zip(eq_meas.bands, bands, strict=True)
    )
    kept = tuple(s for s in greedy.skipped if s.reason.code in _SKIPS_KEPT_FROM_GREEDY)
    return replace(
        greedy,
        method=Method.OPTIMIZE,
        bands=proposals,
        skipped=(*reverted, *kept),
        prediction=Prediction(
            rms_dev_before_db=round(float(np.sqrt(np.mean(dev[mask] ** 2))), 2),
            rms_dev_after_db=round(float(np.sqrt(np.mean(after[mask] ** 2))), 2),
            max_dev_before_db=round(float(dev[mask].max()), 2),
            max_dev_after_db=round(float(after[mask].max()), 2),
            range_hz=(round(float(grid[mask][0]), 1), cfg.approx_above_hz),
        ),
    )
