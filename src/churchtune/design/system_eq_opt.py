"""System-EQ proposal by joint optimisation, starting from the greedy design.

Frequency, gain and Q of all adjustable bands are optimised together. Objective: excess above
the target counts fully, shortfall half (nulls are not chased); points that disagree (spread)
and frequencies below 70 Hz weigh less; a small penalty on gain. Bounds keep the rules: cuts
-9..0 dB, boosts 0..+3 dB with Q up to 1.4, narrow existing notches keep at least half their
depth and stay where they are. After the optimisation every changed band must still address a
problem common to the points, or it is put back.
"""

import math
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
from churchtune.domain.eq_model import eq_response_db

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
MODE_COMMON = 0.5
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


def _eq_from(x: FloatArray, problem: _Problem, eq_meas: SystemEq) -> SystemEq:
    bands = list(eq_meas.bands)
    for i, n in enumerate(problem.slots):
        log_freq, gain, log_q = x[3 * i : 3 * i + 3]
        eq_type = eq_meas.band(n).type if problem.kinds[n] == _Kind.NOTCH else EqType.PEQ
        bands[n - 1] = Band(
            n, eq_type, float(math.exp(log_freq)), float(gain), float(math.exp(log_q))
        )
    return SystemEq(True, tuple(bands))


def _snap(eq: SystemEq, problem: _Problem, quantizer: EqQuantizer) -> list[Band]:
    """Put the optimised bands on the console grid; tiny gains become zero."""
    snapped = []
    for band in eq.bands:
        if not band.adjustable:
            snapped.append(band)
            continue
        gain = quantizer.snap_gain_db(band.gain_db)
        if abs(gain) < MIN_GAIN_DB and problem.kinds.get(band.band) != _Kind.NOTCH:
            gain = 0.0
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

    def mean(self, curve: FloatArray, freq_hz: float) -> float:
        near = (
            (self.grid_hz >= freq_hz * 2**-ZONE_HALF_OCT)
            & (self.grid_hz <= freq_hz * 2**ZONE_HALF_OCT)
            & self.mask
        )
        return float(curve[near].mean()) if near.any() else 0.0

    def points_above(self, point_devs: tuple[FloatArray, ...], freq_hz: float, db: float) -> int:
        return sum(1 for d in point_devs if self.mean(d, freq_hz) > db)

    def points_below(self, point_devs: tuple[FloatArray, ...], freq_hz: float, db: float) -> int:
        return sum(1 for d in point_devs if self.mean(d, freq_hz) < db)


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
            n_bad = zones.points_above(data.point_devs_db, new.freq_hz, cfg.peak_point_db)
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

    def residual(x: FloatArray) -> FloatArray:
        response = eq_response_db(_eq_from(x, problem, eq_meas), grid, fs_hz)
        after = dev + response - data.old_response_db
        weighted = np.where(after > 0, after, SHORTFALL_WEIGHT * after) * weight
        penalty = GAIN_PENALTY * np.array([x[3 * i + 1] for i in range(n_slots)])
        return np.concatenate([weighted[mask] / norm * RESIDUAL_SCALE, penalty])

    if problem.slots:
        solution = least_squares(residual, problem.x0, bounds=(problem.lower, problem.upper))
        optimised = _eq_from(solution.x, problem, eq_meas)
    else:
        optimised = SystemEq(True, eq_meas.bands)
    bands = _snap(optimised, problem, quantizer)
    zones = _Zones(grid, mask)
    reverted = _revert_uncommon(bands, eq_meas, problem, data=data, zones=zones, cfg=cfg)
    after = dev + eq_response_db(SystemEq(True, tuple(bands)), grid, fs_hz) - data.old_response_db
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
