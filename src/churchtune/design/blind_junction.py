"""System EQ while the sub / tops junction is not measured apart (ADR 0013).

A cut aimed at the bass of a hot or late sub is the same at every seat, and the seats without
that bass get thin; a dip at the junction may be a cancellation that no EQ fills. So, around the
crossover the console has, new cuts stay broad and gentle and no boost is centred there, and a
per-seat guard moves cuts back towards what the console had until no seat gets clearly worse by
them anywhere in the corrected range.

The solver sees the zone as residuals (``zone_residuals``); the finished proposal enforces it on
the console grid (``within_zone``) and passes the guard (``seat_guarded``).
"""

import math
from dataclasses import dataclass, replace

import numpy as np

from churchtune.design.system_eq import DesignInput
from churchtune.domain.arrays import FloatArray
from churchtune.domain.eq import Band, EqQuantizer, EqType, SystemEq
from churchtune.domain.eq_model import band_response_db, eq_response_db

# The zone as shares of the crossover frequency.
ZONE_LOW_FC_RATIO = 0.6
ZONE_HIGH_FC_RATIO = 2.0
# Soft edge of the zone for the solver, in natural log of frequency, and its residuals' weight.
ZONE_EDGE_LN = 0.05
ZONE_PENALTY = 2.0
# Below this a gain or a band's effect counts as nothing.
NOTHING_DB = 0.5
# Searching for a value on the console grid: the step tried, and how many tries at most.
SOFTEN_STEP_DB = 0.25
Q_SEARCH_FACTOR = 0.97
GRID_SEARCH_TRIES = 40
# Where the summed cuts are checked across the zone, and the most softening steps.
ZONE_POINTS = 48
ZONE_SUM_STEPS = 400
SEAT_GUARD_STEPS = 120


@dataclass(frozen=True, slots=True)
class BlindJunction:
    """Limits while the junction of the sub and the tops was not measured apart.

    Attributes:
        low_hz: Lower edge of the junction zone (0.6 fc).
        high_hz: Upper edge of the junction zone (2 fc).
        max_cut_db: The deepest new cut in the zone, also summed.
        max_q: The narrowest new cut centred in the zone.
        seat_guard_db: No seat may get further from the target by more than this in any third of
            an octave of the corrected range, and no third may change by more than twice this.
        average_guard_db: The average may get further from the target by no more than this.
    """

    low_hz: float
    high_hz: float
    max_cut_db: float = -3.0
    max_q: float = 1.0
    seat_guard_db: float = 2.0
    average_guard_db: float = 1.5

    @classmethod
    def around(cls, fc_hz: float) -> "BlindJunction":
        """The zone 0.6 fc .. 2 fc with the default limits."""
        return cls(ZONE_LOW_FC_RATIO * fc_hz, ZONE_HIGH_FC_RATIO * fc_hz)

    def holds(self, freq_hz: float) -> bool:
        """True for a band centre inside the zone."""
        return self.low_hz <= freq_hz <= self.high_hz

    def nearest_db(self, band: Band, fs_hz: float) -> float:
        """The band's response at the point of the zone nearest its centre.

        Inside the zone that is the centre itself (the band's gain), so the value is continuous
        across the zone edges.
        """
        at = min(max(band.freq_hz, self.low_hz), self.high_hz)
        return float(band_response_db(band, np.array([at]), fs_hz)[0])


def zone_residuals(
    x: FloatArray, cuts: list[int], blind: BlindJunction | None, fs_hz: float
) -> FloatArray:
    """Residuals that grow as a cut slot of ``x`` breaks the zone's limits.

    ``x`` holds (log frequency, gain, log Q) per slot. A cut centred in the zone: narrower than
    ``max_q``; any cut: deeper than ``max_cut_db`` at the zone point nearest its centre. The Q
    term is blended in at the zone edges, so the solver sees where the zone is.
    """
    if blind is None:
        return np.zeros(0)
    values = np.zeros(2 * len(cuts))
    low, high = math.log(blind.low_hz), math.log(blind.high_hz)
    for k, i in enumerate(cuts):
        log_freq, gain, log_q = x[3 * i : 3 * i + 3]
        inside = _sigmoid((log_freq - low) / ZONE_EDGE_LN) * _sigmoid(
            (high - log_freq) / ZONE_EDGE_LN
        )
        band = Band(1, EqType.PEQ, math.exp(log_freq), float(gain), math.exp(log_q))
        depth = max(0.0, blind.max_cut_db - blind.nearest_db(band, fs_hz))
        values[2 * k] = ZONE_PENALTY * depth
        values[2 * k + 1] = ZONE_PENALTY * inside * max(0.0, log_q - math.log(blind.max_q))
    return values


def _sigmoid(value: float) -> float:
    return 0.5 * (1 + math.tanh(value / 2))


def _q_at_most(limit: float, quantizer: EqQuantizer) -> float:
    """The largest console Q found at or below ``limit`` (searching downwards)."""
    candidate = limit
    for _ in range(GRID_SEARCH_TRIES):
        q = quantizer.snap_q(candidate)
        if q <= limit:
            return q
        candidate *= Q_SEARCH_FACTOR
    return quantizer.snap_q(candidate)


def _softer(gain_db: float, quantizer: EqQuantizer) -> float:
    """The next console gain above a cut's ``gain_db`` (towards 0), at most 0."""
    step = SOFTEN_STEP_DB
    for _ in range(GRID_SEARCH_TRIES):
        softer = min(0.0, quantizer.snap_gain_db(gain_db + step))
        if softer > gain_db:
            return softer
        step += SOFTEN_STEP_DB
    return 0.0


def _changeable(index: int, band: Band, old: Band, frozen: frozenset[int]) -> bool:
    """True for a band the zone rules and the guard may touch: changed, tunable, not frozen."""
    return band != old and band.adjustable and index not in frozen


def within_zone(  # noqa: PLR0913 - the zone needs the console grid and the model
    bands: list[Band],
    eq_meas: SystemEq,
    blind: BlindJunction,
    *,
    frozen: frozenset[int],
    quantizer: EqQuantizer,
    fs_hz: float,
) -> list[Band]:
    """The proposal with the zone's limits enforced, on the console grid.

    ``frozen`` holds the indices of bands the rules never touch (kept notches). An existing
    correction centred in the zone stays as it was: without the junction measured it is not ours
    to move. A new cut centred in the zone is made no deeper than ``max_cut_db`` and no narrower
    than ``max_q``; one centred outside is made shallower until it cuts no deeper than that at
    the zone edge; then all new cuts together are held to ``max_cut_db``. A new boost centred in
    the zone goes back.
    """
    limited = []
    for index, (band, old) in enumerate(zip(bands, eq_meas.bands, strict=True)):
        if not _changeable(index, band, old, frozen):
            limited.append(band)
        elif old.adjustable and old.gain_db != 0 and blind.holds(old.freq_hz):
            limited.append(old)  # also when the solver zeroed it: blind, it stays as it was
        elif band.gain_db == 0:
            limited.append(band)
        elif band.gain_db > 0:
            limited.append(old if blind.holds(band.freq_hz) else band)
        elif blind.holds(band.freq_hz):
            gain = quantizer.snap_gain_db(max(band.gain_db, blind.max_cut_db))
            q = _q_at_most(min(band.q, blind.max_q), quantizer)
            limited.append(replace(band, gain_db=gain, q=q))
        else:
            softer = band
            while softer.gain_db < 0 and blind.nearest_db(softer, fs_hz) < blind.max_cut_db:
                softer = replace(softer, gain_db=_softer(softer.gain_db, quantizer))
            limited.append(softer if softer.gain_db < 0 else old)
    return zone_sum_limited(
        limited, eq_meas, blind, frozen=frozen, quantizer=quantizer, fs_hz=fs_hz
    )


def zone_sum_limited(  # noqa: PLR0913 - the zone needs the console grid and the model
    bands: list[Band],
    eq_meas: SystemEq,
    blind: BlindJunction,
    *,
    frozen: frozenset[int],
    quantizer: EqQuantizer,
    fs_hz: float,
) -> list[Band]:
    """The new cuts made shallower until together they cut no deeper than ``max_cut_db``.

    Checked across the zone, one console gain step at a time: two broad cuts must not add up to
    a deep one. The cut that contributes most where the sum is deepest gives way first.
    """
    zone = np.geomspace(blind.low_hz, blind.high_hz, ZONE_POINTS)
    limited = list(bands)
    for _ in range(ZONE_SUM_STEPS):
        new_cuts = [
            i
            for i, (b, o) in enumerate(zip(limited, eq_meas.bands, strict=True))
            if _changeable(i, b, o, frozen) and b.gain_db < 0
        ]
        responses = {i: band_response_db(limited[i], zone, fs_hz) for i in new_cuts}
        if not responses:
            break
        total = np.sum(list(responses.values()), axis=0)
        deepest = int(np.argmin(total))
        if total[deepest] >= blind.max_cut_db:
            break
        worst = min(new_cuts, key=lambda i: (responses[i][deepest], i))
        gain = _softer(limited[worst].gain_db, quantizer)
        limited[worst] = replace(limited[worst], gain_db=gain) if gain < 0 else eq_meas.bands[worst]
    return limited


def third_octave_worsening(
    before_db: FloatArray, after_db: FloatArray, grid_hz: FloatArray, centres_hz: FloatArray
) -> FloatArray:
    """How much further from the target each third of an octave gets (dB, positive is worse)."""
    worse = np.zeros(len(centres_hz))
    for k, centre in enumerate(centres_hz):
        near = (grid_hz >= centre * 2 ** (-1 / 6)) & (grid_hz < centre * 2 ** (1 / 6))
        if near.any():
            worse[k] = abs(float(after_db[near].mean())) - abs(float(before_db[near].mean()))
    return worse


def _violations(
    change: FloatArray, data: DesignInput, centres: FloatArray, blind: BlindJunction
) -> list[tuple[float, float]]:
    """(dB over the limit, third centre) of every broken limit, the worst first."""
    grid = data.grid_hz
    found: list[tuple[float, float]] = []
    curves = [(seat, blind.seat_guard_db) for seat in data.point_devs_db]
    curves.append((data.dev_db, blind.average_guard_db))
    for curve, allowed_db in curves:
        over = third_octave_worsening(curve, curve + change, grid, centres) - allowed_db
        found += [(float(o), float(c)) for o, c in zip(over, centres, strict=True) if o > 0]
    # A seat swinging from above the target to below it is not "no worse": the change itself is
    # capped too (the same at every seat).
    zero = np.zeros_like(change)
    swing = third_octave_worsening(zero, change, grid, centres) - 2 * blind.seat_guard_db
    found += [(float(o), float(c)) for o, c in zip(swing, centres, strict=True) if o > 0]
    return sorted(found, key=lambda v: (-v[0], v[1]))


def seat_guarded(  # noqa: PLR0913 - the guard needs the whole picture of the seats
    bands: list[Band],
    eq_meas: SystemEq,
    data: DesignInput,
    *,
    mask: np.ndarray,
    blind: BlindJunction,
    frozen: frozenset[int],
    quantizer: EqQuantizer,
    fs_hz: float,
) -> list[Band]:
    """The proposal with cuts moved back until no seat gets clearly worse by them.

    Judged in thirds of an octave of the corrected range, per seat against its own response
    before (``BlindJunction`` holds the limits). Only bands proposed as cuts are moved (they are
    what makes seats without the bass thin; a boost in the zone is not proposed at all). Each
    step takes the worst broken limit that such a cut can mend (it changes the response there by
    more than half a dB) and moves the cut halfway back to what the console had there, on its
    grid (towards a deeper old cut too: the seats were measured with it); a cut close to its old
    gain, or one that took the place of an existing correction, gives the old band back whole.
    Stops when no broken limit is a cut's doing: a boost the solver moved from the hall's own
    correction is not mended here (ADR 0013, open). Deterministic.
    """
    grid = data.grid_hz
    corrected = grid[mask]
    low, high = float(corrected[0]), float(corrected[-1])
    if high <= low:
        return list(bands)
    count = max(1, math.ceil(3 * math.log2(high / low)))
    centres = low * 2 ** ((np.arange(count) + 0.5) / 3)
    guarded = list(bands)
    old_response = eq_response_db(SystemEq(True, eq_meas.bands), grid, fs_hz)
    for _ in range(SEAT_GUARD_STEPS):
        change = eq_response_db(SystemEq(True, tuple(guarded)), grid, fs_hz) - old_response
        target = _cut_to_soften(
            guarded, eq_meas, _violations(change, data, centres, blind), frozen, fs_hz
        )
        if target is None:
            break
        guarded[target] = _toward_old(guarded[target], eq_meas.bands[target], quantizer)
    return zone_sum_limited(
        guarded, eq_meas, blind, frozen=frozen, quantizer=quantizer, fs_hz=fs_hz
    )


def _cut_to_soften(
    bands: list[Band],
    eq_meas: SystemEq,
    violations: list[tuple[float, float]],
    frozen: frozenset[int],
    fs_hz: float,
) -> int | None:
    """The cut that changes the response most where the worst mendable limit is broken."""
    for _, centre in violations:
        at = np.array([centre])
        effects = [
            (abs(float(band_response_db(b, at, fs_hz)[0] - band_response_db(o, at, fs_hz)[0])), i)
            for i, (b, o) in enumerate(zip(bands, eq_meas.bands, strict=True))
            if _changeable(i, b, o, frozen) and b.gain_db < 0
        ]
        mendable = [(effect, -i) for effect, i in effects if effect > NOTHING_DB]
        if mendable:
            return -max(mendable)[1]
    return None


def _toward_old(band: Band, old: Band, quantizer: EqQuantizer) -> Band:
    """Halfway from ``band`` back to ``old`` on the console grid; ``old`` when that is close."""
    moved = (band.freq_hz, band.q, band.type) != (old.freq_hz, old.q, old.type)
    if abs(band.gain_db - old.gain_db) <= NOTHING_DB or (moved and old.gain_db != 0):
        return old  # a correction moved from its place comes back whole
    half = quantizer.snap_gain_db((band.gain_db + old.gain_db) / 2)
    if half == band.gain_db:
        return old
    return replace(band, gain_db=half)
