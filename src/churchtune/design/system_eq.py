"""System-EQ proposal from a measured session: the greedy design.

Rules:
  * at most six bands (one EQ block); cut-filter bands are locked;
  * only problems common to most points (peaks: 2/3 of the points; dips: 80 %);
  * cuts first; boosts only for broad (1 octave), consistent (spread up to 3 dB) dips, +3 dB max;
  * narrow dips and nulls are never filled (position dependent, EQ cannot fix them);
  * above 10 kHz everything is flagged approximate (uncalibrated measurement mic);
  * the measurement already contains the EQ that was active, so the new EQ is designed as
    ``new = old + correction`` and the predicted response is ``average + (new - old)``.
"""

import math
from dataclasses import dataclass, field, replace

import numpy as np
from scipy.optimize import lsq_linear

from churchtune.analysis.points import Analysis
from churchtune.design.proposal import (
    Action,
    BandProposal,
    CandidateKind,
    DesignWarning,
    Method,
    Prediction,
    Proposal,
    Reason,
    ReasonCode,
    SkipCode,
    Skipped,
    TargetSpec,
)
from churchtune.design.target import EqDesignConfig, bandwidth_to_q, target_curve
from churchtune.domain.arrays import FloatArray
from churchtune.domain.eq import Band, EqQuantizer, EqType, SystemEq
from churchtune.domain.eq_model import band_response_db, eq_response_db

POINT_ZONE_HALF_OCT = 1 / 12
REFINE_WEIGHT_ELSEWHERE = 0.3
REFINE_ITERATIONS = 3
# Below this gain a band's response is too small to linearise; a unit band is used instead.
REFINE_MIN_LINEAR_GAIN_DB = 0.5
MODE_HINT_BELOW_HZ = 300.0
DEFAULT_TARGET = TargetSpec()
DEFAULT_CONFIG = EqDesignConfig()


@dataclass(slots=True)
class _Candidate:
    """A peak or dip of the averaged response that may get a band."""

    kind: CandidateKind
    freq_hz: float
    dev_db: float
    width_oct: float
    low_hz: float
    high_hz: float
    n_points: int
    spread_db: float
    slot: int | None = None
    replaced_cut: Band | None = None


class _Slots:
    """Band slots of the EQ: locked (cut filters), active (existing non-zero), free."""

    def __init__(self, old: SystemEq, cfg: EqDesignConfig) -> None:
        self._cfg = cfg
        self.old = {b.band: b for b in old.bands}
        self.locked = {n for n, b in self.old.items() if not b.adjustable}
        self.active = {
            n
            for n, b in self.old.items()
            if n not in self.locked and abs(b.gain_db) >= cfg.active_gain_db
        }
        self.free = sorted(
            (n for n in self.old if n not in self.locked and n not in self.active),
            key=lambda n: (self.old[n].type != EqType.PEQ, n),
        )
        self.used: set[int] = set()

    def near_active(self, freq_hz: float, want_cut: bool | None, within_oct: float) -> int | None:
        """The unused active band nearest to ``freq_hz`` (optionally only cuts or only boosts)."""
        near = [
            n
            for n in self.active - self.used
            if abs(math.log2(self.old[n].freq_hz / freq_hz)) <= within_oct
            and (want_cut is None or (self.old[n].gain_db < 0) == want_cut)
        ]
        if not near:
            return None
        return min(near, key=lambda n: abs(math.log2(self.old[n].freq_hz / freq_hz)))

    def take(self, freq_hz: float, want_cut: bool | None = None) -> int | None:
        """Reserve a band for a feature: a nearby active band if there is one, else a free one."""
        within = self._cfg.reuse_oct if want_cut else self._cfg.merge_oct
        n = self.near_active(freq_hz, want_cut, within)
        if n is None:
            available = [k for k in self.free if k not in self.used]
            n = available[0] if available else None
        if n is not None:
            self.used.add(n)
        return n


@dataclass(frozen=True, slots=True)
class DesignInput:
    """What both design methods start from, on the analysis grid.

    Attributes:
        grid_hz: The grid.
        dev_db: Averaged response minus the target.
        point_devs_db: Each point's response minus the target.
        f_correct_from_hz: Lowest frequency that is corrected.
        old_response_db: Response of the EQ that was active while measuring.
    """

    grid_hz: FloatArray
    dev_db: FloatArray
    point_devs_db: tuple[FloatArray, ...]
    f_correct_from_hz: float
    old_response_db: FloatArray


def design_input(
    an: Analysis, eq_meas: SystemEq, fs_hz: float, target: TargetSpec, cfg: EqDesignConfig
) -> DesignInput:
    """Deviation of the session from the target, and the response of the measured-with EQ."""
    wanted = target_curve(
        an.grid_hz,
        target.tilt_db_per_oct,
        target.tilt_from_hz,
        target.bass_lift_db,
        target.bass_from_hz,
    )
    return DesignInput(
        grid_hz=an.grid_hz,
        dev_db=an.avg_db - wanted,
        point_devs_db=tuple(curve - wanted for curve in an.norm_db.values()),
        f_correct_from_hz=max(an.f_low_hz * 1.25, cfg.min_low_hz),
        old_response_db=eq_response_db(eq_meas, an.grid_hz, fs_hz),
    )


def design_warnings(eq_meas: SystemEq, eq_now: SystemEq | None) -> tuple[DesignWarning, ...]:
    """Warnings that do not depend on the method."""
    found = []
    if eq_now is not None and eq_now != eq_meas:
        found.append(DesignWarning.EQ_CHANGED_SINCE_MEASUREMENT)
    if not eq_meas.on:
        found.append(DesignWarning.EQ_WAS_OFF)
    return tuple(found)


def _width(dev: FloatArray, i: int, sign: int, grid: FloatArray) -> tuple[float, float, float]:
    """Half-height width (octaves) of a peak (sign=+1) or dip (sign=-1) at index ``i``."""
    half = dev[i] * 0.5
    low = i
    while low > 0 and sign * dev[low] > sign * half:
        low -= 1
    high = i
    while high < len(dev) - 1 and sign * dev[high] > sign * half:
        high += 1
    return math.log2(grid[high] / grid[low]), float(grid[low]), float(grid[high])


def _frac_points(
    point_devs: list[FloatArray], i: int, sign: int, threshold_db: float, grid: FloatArray
) -> float:
    """Share of points whose own deviation near ``grid[i]`` exceeds the threshold."""
    near = (grid >= grid[i] * 2**-POINT_ZONE_HALF_OCT) & (grid <= grid[i] * 2**POINT_ZONE_HALF_OCT)
    if sign > 0:
        values = [d[near].max() for d in point_devs]
    else:
        values = [-d[near].min() for d in point_devs]
    return sum(1 for v in values if v >= threshold_db) / len(point_devs)


@dataclass(slots=True)
class _Work:
    """Mutable working state of one greedy design."""

    data: DesignInput
    an: Analysis
    eq_meas: SystemEq
    fs_hz: float
    quantizer: EqQuantizer
    cfg: EqDesignConfig
    mask: np.ndarray
    slots: _Slots
    residual: FloatArray
    point_residuals: list[FloatArray]
    bands: list[Band]
    chosen: list[tuple[int, _Candidate]] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)

    @property
    def n_points(self) -> int:
        return len(self.data.point_devs_db)

    def response(self, band: Band) -> FloatArray:
        return band_response_db(band, self.data.grid_hz, self.fs_hz)

    def place(self, n: int, candidate: _Candidate, band: Band) -> None:
        """Put ``band`` into slot ``n`` and update the residuals with its effect."""
        delta = self.response(band) - self.response(self.bands[n - 1])
        self.residual = self.residual + delta
        self.point_residuals = [d + delta for d in self.point_residuals]
        self.bands[n - 1] = band
        candidate.slot = n
        self.chosen.append((n, candidate))

    def skip(self, c: _Candidate, code: SkipCode, **params: float | int | str | bool) -> None:
        self.skipped.append(
            Skipped(
                kind=c.kind,
                freq_hz=round(c.freq_hz, 1),
                dev_db=round(c.dev_db, 2),
                width_oct=round(c.width_oct, 2),
                points=c.n_points,
                reason=Reason(code, params),
            )
        )

    def candidate(
        self, kind: CandidateKind, i: int, sign: int, point_db: float
    ) -> tuple[_Candidate, float]:
        """Describe the feature at grid index ``i``; also the share of points that show it."""
        grid, res = self.data.grid_hz, self.residual
        width, low, high = _width(res, i, sign, grid)
        frac = _frac_points(self.point_residuals, i, sign, max(point_db, sign * 0.5 * res[i]), grid)
        found = _Candidate(
            kind=kind,
            freq_hz=float(grid[i]),
            dev_db=float(res[i]),
            width_oct=width,
            low_hz=low,
            high_hz=high,
            n_points=round(frac * self.n_points),
            spread_db=float(self.an.spread_db[i]),
        )
        return found, frac

    def peaking(self, n: int, c: _Candidate, gain_db: float) -> Band:
        q = self.quantizer.snap_q(float(np.clip(bandwidth_to_q(c.width_oct), *self.cfg.q_range)))
        return Band(
            n,
            EqType.PEQ,
            self.quantizer.snap_freq_hz(c.freq_hz),
            self.quantizer.snap_gain_db(gain_db),
            q,
        )


def _cut_pass(w: _Work) -> None:
    """Greedy cuts, largest remaining excess first."""
    cfg, grid = w.cfg, w.data.grid_hz
    blocked = np.zeros_like(grid, dtype=bool)
    while True:
        res = w.residual
        open_peaks = w.mask & ~blocked & (res >= cfg.peak_min_db)
        if not open_peaks.any():
            return
        i = int(np.argmax(np.where(open_peaks, res, -np.inf)))
        # A point "has" the peak if its own excess is 2 dB or more and half the averaged excess.
        c, frac = w.candidate(CandidateKind.CUT, i, +1, cfg.peak_point_db)
        blocked |= (grid >= c.low_hz) & (grid <= c.high_hz)
        if frac < cfg.peak_common:
            w.skip(c, SkipCode.LOCAL_PEAK, n=c.n_points, n_points=w.n_points)
            continue
        n = w.slots.take(c.freq_hz, want_cut=True)
        if n is None:
            w.skip(c, SkipCode.NO_FREE_BAND, cuts_first=False)
            continue
        gain = float(np.clip(-cfg.correction * res[i], cfg.max_cut_db, 0.0))
        w.place(n, c, w.peaking(n, c, gain))


def _dip_skip_reason(
    w: _Work, c: _Candidate, frac: float
) -> tuple[SkipCode, dict[str, float | int]] | None:
    cfg = w.cfg
    if c.width_oct < cfg.dip_min_oct:
        return SkipCode.NARROW_DIP, {"width_oct": round(c.width_oct, 2)}
    if frac < cfg.dip_common:
        return SkipCode.LOCAL_DIP, {"n": c.n_points, "n_points": w.n_points}
    if c.spread_db > cfg.dip_max_spread_db:
        return SkipCode.LARGE_SPREAD, {"spread_db": round(c.spread_db, 1)}
    if c.freq_hz < w.an.f_low_hz * 2:
        return SkipCode.NEAR_ROLLOFF, {}
    return None


def _dip_pass(w: _Work) -> None:
    """Dips: only broad and consistent ones, or ones caused by an existing cut band."""
    cfg, grid = w.cfg, w.data.grid_hz
    blocked = np.zeros_like(grid, dtype=bool)
    while True:
        res = w.residual
        open_dips = w.mask & ~blocked & (res <= -cfg.dip_min_db)
        if not open_dips.any():
            return
        i = int(np.argmin(np.where(open_dips, res, np.inf)))
        c, frac = w.candidate(CandidateKind.BOOST, i, -1, cfg.dip_point_db)
        blocked |= (grid >= c.low_hz) & (grid <= c.high_hz)
        reason = _dip_skip_reason(w, c, frac)
        own = w.slots.near_active(c.freq_hz, want_cut=True, within_oct=cfg.merge_oct)
        if own is not None and frac >= cfg.peak_common and grid[i] <= cfg.approx_above_hz:
            old = w.slots.old[own]
            w.slots.used.add(own)
            fill = cfg.correction * -res[i]
            if reason is None:
                # Broad, consistent dip right where an existing cut sits: the slot becomes a
                # (capped) boost.
                c.replaced_cut = old
                gain = float(np.clip(fill + old.gain_db, 0.0, cfg.max_boost_db))
                w.place(own, c, w.peaking(own, c, gain))
            else:
                c.kind = CandidateKind.RELAX
                gain = w.quantizer.snap_gain_db(min(0.0, old.gain_db + fill))
                w.place(own, c, replace(old, gain_db=gain))
            continue
        if reason is not None:
            w.skip(c, reason[0], **reason[1])
            continue
        n = w.slots.take(c.freq_hz)
        if n is None:
            w.skip(c, SkipCode.NO_FREE_BAND, cuts_first=True)
            continue
        gain = float(np.clip(cfg.correction * -res[i], 0.0, cfg.max_boost_db))
        w.place(n, c, w.peaking(n, c, gain))


def _refine_gains(w: _Work) -> None:
    """Jointly refine the chosen bands' gains (fixed frequency and Q) by bounded least squares."""
    if not w.chosen:
        return
    cfg, grid, dev = w.cfg, w.data.grid_hz, w.data.dev_db
    weights = np.where(w.mask, REFINE_WEIGHT_ELSEWHERE, 0.0)
    want = np.zeros_like(grid)
    for _, c in w.chosen:
        region = (grid >= c.low_hz) & (grid <= c.high_hz) & w.mask
        weights[region] = 1.0
        want[region] = -cfg.correction * dev[region]
    is_boost = [c.kind is CandidateKind.BOOST for _, c in w.chosen]
    lower = [0.0 if boost else cfg.max_cut_db for boost in is_boost]
    upper = [cfg.max_boost_db if boost else 0.0 for boost in is_boost]
    gains = np.array([w.bands[n - 1].gain_db for n, _ in w.chosen])
    for _ in range(REFINE_ITERATIONS):
        zeroed = list(w.bands)
        for n, _c in w.chosen:
            zeroed[n - 1] = replace(zeroed[n - 1], gain_db=0.0)
        base = eq_response_db(SystemEq(True, tuple(zeroed)), grid, w.fs_hz) - w.data.old_response_db
        columns = []
        for (n, _c), gain, boost in zip(w.chosen, gains, is_boost, strict=True):
            unit = gain if abs(gain) >= REFINE_MIN_LINEAR_GAIN_DB else (1.0 if boost else -1.0)
            columns.append(w.response(replace(zeroed[n - 1], gain_db=float(unit))) / unit)
        matrix = np.array(columns).T * weights[:, None]
        gains = lsq_linear(matrix, (want - base) * weights, bounds=(lower, upper)).x
    for (n, c), raw_gain in zip(w.chosen, gains, strict=True):
        gain = w.quantizer.snap_gain_db(float(raw_gain))
        if abs(gain) < cfg.min_effect_db and c.kind is not CandidateKind.RELAX:
            w.skip(c, SkipCode.TOO_SMALL, gain_db=gain, min_effect_db=cfg.min_effect_db)
            w.bands[n - 1] = w.eq_meas.band(n)
            c.slot = None
            continue
        w.bands[n - 1] = replace(w.bands[n - 1], gain_db=gain)


def _band_reason(c: _Candidate, new: Band, old: Band, w: _Work) -> Reason:
    common = {"freq_hz": new.freq_hz, "dev_db": c.dev_db, "n": c.n_points, "n_points": w.n_points}
    if c.kind is CandidateKind.RELAX:
        return Reason(
            ReasonCode.RELAX, {**common, "old_gain_db": old.gain_db, "new_gain_db": new.gain_db}
        )
    shape = {
        **common,
        "gain_db": new.gain_db,
        "q": new.q,
        "width_oct": c.width_oct,
        "approximate": c.freq_hz > w.cfg.approx_above_hz,
    }
    if c.kind is CandidateKind.CUT:
        return Reason(ReasonCode.CUT, {**shape, "mode_hint": c.freq_hz < MODE_HINT_BELOW_HZ})
    replaced = c.replaced_cut
    return Reason(
        ReasonCode.BOOST,
        {
            **shape,
            "max_boost_db": w.cfg.max_boost_db,
            "spread_db": c.spread_db,
            "replaced_type": str(replaced.type) if replaced else None,
            "replaced_freq_hz": replaced.freq_hz if replaced else None,
            "replaced_gain_db": replaced.gain_db if replaced else None,
            "replaced_q": replaced.q if replaced else None,
        },
    )


def _band_proposals(w: _Work) -> tuple[BandProposal, ...]:
    by_slot = {c.slot: c for _, c in w.chosen if c.slot}
    result = []
    for old, new in zip(w.eq_meas.bands, w.bands, strict=True):
        c = by_slot.get(new.band)
        if not old.adjustable:
            entry = BandProposal(
                new, old, Action.KEEP, False, Reason(ReasonCode.LOCKED_CUT_FILTER, {})
            )
        elif old != new and c is not None:
            active = abs(old.gain_db) >= w.cfg.active_gain_db
            entry = BandProposal(
                new,
                old,
                Action.MODIFY if active else Action.NEW,
                c.freq_hz > w.cfg.approx_above_hz,
                _band_reason(c, new, old, w),
            )
        else:
            inactive = abs(old.gain_db) < w.cfg.active_gain_db
            code = ReasonCode.UNCHANGED_INACTIVE if inactive else ReasonCode.UNCHANGED_EXISTING
            entry = BandProposal(new, old, Action.KEEP, False, Reason(code, {}))
        result.append(entry)
    return tuple(result)


def _prediction(w: _Work) -> Prediction:
    grid, dev = w.data.grid_hz, w.data.dev_db
    new_response = eq_response_db(SystemEq(True, tuple(w.bands)), grid, w.fs_hz)
    after = dev + new_response - w.data.old_response_db
    evaluated = w.mask & (grid <= w.cfg.approx_above_hz)
    has_range = bool(evaluated.any())

    def rms(curve: FloatArray) -> float:
        return float(np.sqrt(np.mean(curve[evaluated] ** 2))) if has_range else 0.0

    def worst(curve: FloatArray) -> float:
        return round(float(np.abs(curve[evaluated]).max()), 2) if has_range else 0.0

    return Prediction(
        rms_dev_before_db=round(rms(dev), 2),
        rms_dev_after_db=round(rms(after), 2),
        max_dev_before_db=worst(dev),
        max_dev_after_db=worst(after),
        range_hz=(round(float(grid[w.mask][0]), 1), w.cfg.approx_above_hz),
    )


def design(  # noqa: PLR0913 - explicit inputs instead of globals
    an: Analysis,
    eq_meas: SystemEq,
    quantizer: EqQuantizer,
    fs_hz: float,
    *,
    target: TargetSpec = DEFAULT_TARGET,
    eq_now: SystemEq | None = None,
    cfg: EqDesignConfig = DEFAULT_CONFIG,
) -> Proposal:
    """Greedy system-EQ design: cuts, then allowed boosts, then a joint refinement of the gains.

    Args:
        an: Analysis of the measured session.
        eq_meas: EQ that was active during the measurement.
        quantizer: The console's EQ value grid; every proposed value lies on it.
        fs_hz: Sample rate used for the EQ response model.
        target: Target curve.
        eq_now: EQ on the console now, if read; a difference to ``eq_meas`` is flagged.
        cfg: Design thresholds.

    Returns:
        The proposal. Its prediction is a model; the verify measurement tells the truth.
    """
    data = design_input(an, eq_meas, fs_hz, target, cfg)
    grid = data.grid_hz
    work = _Work(
        data=data,
        an=an,
        eq_meas=eq_meas,
        fs_hz=fs_hz,
        quantizer=quantizer,
        cfg=cfg,
        mask=(grid >= data.f_correct_from_hz) & (grid <= cfg.max_hz),
        slots=_Slots(eq_meas, cfg),
        residual=data.dev_db.copy(),
        point_residuals=[d.copy() for d in data.point_devs_db],
        bands=list(eq_meas.bands),
    )
    _cut_pass(work)
    _dip_pass(work)
    _refine_gains(work)
    return Proposal(
        method=Method.GREEDY,
        n_points=work.n_points,
        target=target,
        f_low_hz=round(an.f_low_hz, 1),
        eq_on=True,
        bands=_band_proposals(work),
        skipped=tuple(work.skipped),
        warnings=design_warnings(eq_meas, eq_now),
        prediction=_prediction(work),
        eq_at_measurement=eq_meas,
        eq_current=eq_now,
    )
