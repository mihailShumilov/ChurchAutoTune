"""Change proposals grouped in packages (SPEC S11, §8.5): crossover, alignment, sub level, EQ.

A package says what changes on which path (before → after), why, the modelled effect and the
risk. Packages are console-neutral data: paths are ``SignalRef``s and outputs are numbers; turning
them into console writes is the driver's job (M5). The order of application is fixed:
crossover → alignment → sub level → system EQ. Each package is designed as if the packages before
it that the person keeps were applied: its "before" is their "after", and ``requires`` names them.
When the person leaves a package out, the later ones are designed again without it.

Measured responses already went through the EQ that was on the console. A prediction therefore
applies only the change of an EQ block (proposed over present response), never the whole block.

Where the topology leaves no place for a change (tops straight on Main LR, a sub on a bus), the
package is in suggestion mode: it says what to set by hand and is never written.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Literal

import numpy as np
from scipy import signal

from churchtune.analysis.points import Analysis
from churchtune.analysis.response import GRID_HZ, energy_average, magnitude_db
from churchtune.design.alignment import (
    AlignmentResult,
    JunctionPoint,
    analyze_alignment,
    recommend_level,
)
from churchtune.design.blind_junction import BlindJunction
from churchtune.design.crossover import DEFAULT_FC_HZ, CrossoverPlan, plan_crossover
from churchtune.design.crossover import NoteCode as CrossoverNoteCode
from churchtune.design.proposal import Prediction, TargetSpec
from churchtune.design.system_eq import DEFAULT_TARGET
from churchtune.design.system_eq_opt import optimize_design
from churchtune.domain.arrays import FloatArray
from churchtune.domain.console import ConsoleState, Roles
from churchtune.domain.console_paths import console_state, matrix_roles, strip_eq
from churchtune.domain.eq import CUT_FILTER_TYPES, EqQuantizer, SystemEq
from churchtune.domain.eq_model import band_sos
from churchtune.domain.snapshot import ConsoleSnapshot, OutputKind, SignalRef, SourceKind
from churchtune.domain.topology import TOP_ROLES, TopologyGuess

# SPEC §8.5: the crossover frequency is chosen in this range from the measured responses.
FC_RANGE_HZ = (80.0, 120.0)
FC_STEP_HZ = 5.0
TOPS_REFERENCE_HZ = (200.0, 1000.0)
SUB_REFERENCE_HZ = (40.0, 80.0)
WORKING_ZONE_DB = -6.0
# An alignment that improves the junction by less than this is not proposed.
MIN_ALIGNMENT_GAIN_DB = 1.0
# The sub fader moves at most this much in one package, and never above 0 dB.
# Below the ±10 dB of the levels scope by more than a fader step, so a proposal always passes it.
MAX_LEVEL_STEP_DB = 9.5
LOWEST_PROPOSED_FADER_DB = -89.5  # below about −90 dB an X32/M32 fader reads as down
MAX_FADER_DB = 0.0
# A predicted EQ change is limited to this much per frequency (near the zeros of cut filters).
EQ_CHANGE_LIMIT_DB = 24.0
ZONE_OCTAVES = 1.0  # the junction zone is fc / 2 .. 2 fc
TINY = 1e-12
MAIN_LR_LABEL = "main_lr"
# A system EQ is proposed when it improves the modelled RMS deviation by at least this much, or
# lowers the largest excess above the target by at least MIN_EQ_PEAK_GAIN_DB (system_eq_effect).
MIN_EQ_GAIN_DB = 0.2
MIN_EQ_PEAK_GAIN_DB = 1.0
# A cut filter here on the sub's or the tops' matrix is the crossover the console has now.
PRESENT_CROSSOVER_RANGE_HZ = (50.0, 250.0)
TINY_MOVE_OCT = 1 / 12
TINY_MOVE_DB = 0.5
MAIN_SOURCES = (SourceKind.MAIN_L, SourceKind.MAIN_R)
# Crossover notes about old corrections that go back to 0 dB, shown as one line per path.
RESET_CODES = frozenset(
    {
        CrossoverNoteCode.FAKE_LOW_PASS_REMOVED,
        CrossoverNoteCode.BOOST_ABOVE_SUB_REMOVED,
        CrossoverNoteCode.BOOST_IN_ZONE_REMOVED,
        CrossoverNoteCode.FAKE_HIGH_PASS_REMOVED,
    }
)


class PackageKind(StrEnum):
    """The packages, in the order they are applied."""

    CROSSOVER = "crossover"
    ALIGNMENT = "alignment"
    SUB_LEVEL = "sub_level"
    SYSTEM_EQ = "system_eq"


class Risk(StrEnum):
    """How careful the person must be with a package."""

    LOW = "low"  # small, reversible, verified writes
    MEDIUM = "medium"  # audible change of the system balance
    UNVERIFIED = "unverified"  # a write never checked on hardware: suggestion mode until it is


class EffectMetric(StrEnum):
    """What a package's modelled effect measures."""

    JUNCTION_SCORE = "junction-score"
    SUB_MINUS_TOPS = "sub-minus-tops-at-fc"
    RMS_DEVIATION = "rms-deviation"
    MAX_EXCESS = "max-excess"


class NoteCode(StrEnum):
    """Codes of the package notes (the crossover adds its own, ``crossover-*``)."""

    CROSSOVER_TOPS_ON_MAIN = "crossover-tops-on-main"
    CROSSOVER_SUB_NOT_ON_MATRIX = "crossover-sub-not-on-matrix"
    CROSSOVER_SHARED_PATH = "crossover-shared-path"
    CROSSOVER_CHECK_SPEAKERS = "crossover-check-speakers"
    CROSSOVER_FILTERS = "crossover-filters"
    CROSSOVER_RESET = "crossover-reset"
    CROSSOVER_MORE_LOWS_TO_TOPS = "crossover-more-lows-to-tops"
    ALIGNMENT_DELAY_SUB = "alignment-delay-sub"
    ALIGNMENT_DELAY_TOPS = "alignment-delay-tops"
    ALIGNMENT_DELAY_BY_HAND = "alignment-delay-by-hand"
    ALIGNMENT_POLARITY = "alignment-polarity"
    ALIGNMENT_NOT_NEEDED = "alignment-not-needed"
    SUB_LEVEL = "sub-level"
    SUB_LEVEL_ABOVE_ZERO = "sub-level-above-zero"
    SUB_LEVEL_BY_EAR = "sub-level-by-ear"
    SYSTEM_EQ_ON = "system-eq-on"
    SYSTEM_EQ_ABOVE_FC = "system-eq-above-fc"
    SYSTEM_EQ_BLIND_JUNCTION = "system-eq-blind-junction"
    SYSTEM_EQ_PREDICTED_JUNCTION = "system-eq-predicted-junction"
    SYSTEM_EQ_ALSO_OTHER_OUTPUTS = "system-eq-also-other-outputs"
    JUNCTION_NOT_MEASURED = "junction-not-measured"


@dataclass(frozen=True, slots=True)
class Note:
    """Why something is proposed or left alone; a code with values, rendered by the UI."""

    code: str
    params: Mapping[str, float | int | str] | None = None


@dataclass(frozen=True, slots=True)
class PathEq:
    """The EQ block of one path, before and after."""

    path: SignalRef
    before: SystemEq
    after: SystemEq

    @property
    def changed(self) -> bool:
        """True when the package changes this block."""
        return self.before != self.after


@dataclass(frozen=True, slots=True)
class OutputDelayChange:
    """Delay of one physical (main) output, before and after, in ms."""

    output: int
    before_ms: float
    after_ms: float


@dataclass(frozen=True, slots=True)
class LevelChange:
    """A fader, before and after, in dB."""

    path: SignalRef
    before_db: float
    after_db: float


@dataclass(frozen=True, slots=True)
class PolarityChange:
    """Polarity of a path, before and after (True = inverted)."""

    path: SignalRef
    before: bool
    after: bool


@dataclass(frozen=True, slots=True)
class Effect:
    """The modelled effect: a metric before and after (dB); the verify measurement decides."""

    metric: str
    before_db: float
    after_db: float


@dataclass(frozen=True, slots=True)
class Package:
    """One package of changes.

    Attributes:
        kind: What it is.
        suggest_only: True when the program shows the values and the person sets them by hand.
        risk: How careful to be.
        requires: Packages this one was designed after; without them it is not valid.
        eq: EQ blocks that change.
        delays: Output delays that change.
        delay_side: Which side waits (``sub`` or ``tops``), when there is a delay.
        delay_ms: How long it waits.
        levels: Faders that change (every sub matrix of a pair).
        polarities: Polarities that change.
        notes: Why, in order.
        effect: Modelled effect.
    """

    kind: PackageKind
    suggest_only: bool
    risk: Risk
    requires: tuple[PackageKind, ...] = ()
    eq: tuple[PathEq, ...] = ()
    delays: tuple[OutputDelayChange, ...] = ()
    delay_side: Literal["sub", "tops"] | None = None
    delay_ms: float = 0.0
    levels: tuple[LevelChange, ...] = ()
    polarities: tuple[PolarityChange, ...] = ()
    notes: tuple[Note, ...] = ()
    effect: Effect | None = None


@dataclass(frozen=True, slots=True)
class DesignContext:
    """Everything the packages are designed from.

    Attributes:
        analysis: Analysis of the measured points (everything playing).
        snapshot: The console as read in this session (the EQ the points were measured with).
        topology: Confirmed speaker roles.
        quantizer: The console's EQ value grid.
        fs_hz: Sample rate of the responses and of the EQ model.
        junction: Sub / tops / both responses per point, when the junction was measured.
        target: House curve.
    """

    analysis: Analysis
    snapshot: ConsoleSnapshot
    topology: TopologyGuess
    quantizer: EqQuantizer
    fs_hz: float
    junction: Mapping[str, JunctionPoint] | None = None
    target: TargetSpec = DEFAULT_TARGET


@dataclass(frozen=True, slots=True)
class Packages:
    """The proposal of a session.

    Attributes:
        fc_hz: Crossover frequency the packages use.
        packages: In the order of application.
        notes: Findings that lead to no package (for example "alignment not needed").
    """

    fc_hz: float
    packages: tuple[Package, ...]
    notes: tuple[Note, ...]

    def of(self, kind: PackageKind) -> Package | None:
        """The package of one kind, or None."""
        return next((p for p in self.packages if p.kind is kind), None)


# ------------------------------------------------------------------ responses
def eq_response(eq: SystemEq, freqs_hz: FloatArray, fs_hz: float) -> np.ndarray:
    """Complex response of an EQ block (1 everywhere when it is switched off)."""
    response = np.ones(len(freqs_hz), dtype=np.complex128)
    if not eq.on:
        return response
    w = 2 * np.pi * freqs_hz / fs_hz
    for band in eq.bands:
        sos = band_sos(band, fs_hz)
        if sos is not None:
            response *= signal.sosfreqz(sos, worN=w)[1]
    return response


def with_eq_change(ir: FloatArray, before: SystemEq, after: SystemEq, fs_hz: float) -> FloatArray:
    """An impulse response measured through ``before`` as it would be through ``after``.

    The spectrum is multiplied by after / before; the change is limited to ±24 dB per frequency,
    so the zero of a cut filter that is removed does not blow up into noise.
    """
    if before == after:
        return ir
    n = len(ir)
    size = 2 * n  # zero-padded: a linear, not circular, convolution
    freqs = np.fft.rfftfreq(size, 1 / fs_hz)
    present = eq_response(before, freqs, fs_hz)
    floor = np.where(np.abs(present) < TINY, TINY, present)
    ratio = eq_response(after, freqs, fs_hz) / floor
    limit = 10 ** (EQ_CHANGE_LIMIT_DB / 20)
    magnitude = np.clip(np.abs(ratio), 1 / limit, limit)
    ratio = magnitude * np.exp(1j * np.angle(ratio))
    return np.asarray(np.fft.irfft(np.fft.rfft(ir, size) * ratio, size)[:n], dtype=np.float64)


def _off(eq: SystemEq) -> SystemEq:
    return eq.with_on(False)


def _band_mean(curve: FloatArray, low: float, high: float) -> float:
    band = (low <= GRID_HZ) & (high >= GRID_HZ)
    return float(np.median(curve[band]))


def _power_mean_db(curves: list[FloatArray]) -> FloatArray:
    return np.asarray(10 * np.log10(np.mean([10 ** (c / 10) for c in curves], axis=0)))


def choose_fc(junction: Mapping[str, JunctionPoint] | None, fs_hz: float) -> float:
    """The crossover frequency (SPEC §8.5), 80..120 Hz, from the speakers' own responses.

    The frequency nearest to the default 100 Hz where the tops are within 6 dB of their
    200..1000 Hz level and the sub within 6 dB of its 40..80 Hz level (medians, so a mode does
    not set the reference); 100 Hz without junction measurements or when no frequency qualifies.
    ``junction`` must hold the natural responses (the present matrix EQ taken out).
    """
    if not junction:
        return DEFAULT_FC_HZ
    tops = _power_mean_db([magnitude_db(p.tops, fs_hz) for p in junction.values()])
    sub = _power_mean_db([magnitude_db(p.sub, fs_hz) for p in junction.values()])
    tops_ref = _band_mean(tops, *TOPS_REFERENCE_HZ)
    sub_ref = _band_mean(sub, *SUB_REFERENCE_HZ)
    low, high = FC_RANGE_HZ
    candidates = sorted(
        np.arange(low, high + FC_STEP_HZ / 2, FC_STEP_HZ), key=lambda f: abs(f - DEFAULT_FC_HZ)
    )
    for fc in candidates:  # the default first, then outwards
        i = int(np.argmin(np.abs(GRID_HZ - fc)))
        if tops[i] >= tops_ref + WORKING_ZONE_DB and sub[i] >= sub_ref + WORKING_ZONE_DB:
            return float(fc)
    return DEFAULT_FC_HZ


def _matrix_ref(number: int) -> SignalRef:
    return SignalRef(SourceKind.MATRIX, number)


def _fed_by_main(snapshot: ConsoleSnapshot, ref: SignalRef) -> bool:
    if ref.kind in MAIN_SOURCES:
        return True
    if ref.kind is SourceKind.MATRIX:
        return any(s.target == ref.number and s.open for s in snapshot.main.matrix_sends)
    return False


@dataclass(frozen=True, slots=True)
class _Junction:
    """The junction of a session: as measured, through the present matrix EQ."""

    points: Mapping[str, JunctionPoint]
    sub_now: SystemEq
    tops_now: SystemEq

    def through(
        self, sub_eq: SystemEq, tops_eq: SystemEq, fs_hz: float
    ) -> dict[str, JunctionPoint]:
        """The junction as it would sound with other matrix EQ (only the change is applied)."""
        result = {}
        for name, point in self.points.items():
            sub = with_eq_change(point.sub, self.sub_now, sub_eq, fs_hz)
            tops = with_eq_change(point.tops, self.tops_now, tops_eq, fs_hz)
            result[name] = JunctionPoint(sub=sub, tops=tops, both=sub + tops)
        return result


# ------------------------------------------------------------------ crossover
def _crossover(
    ctx: DesignContext, state: ConsoleState, roles: Roles | None, fc_hz: float
) -> tuple[Package | None, CrossoverPlan | None]:
    if not ctx.topology.subs:
        return None, None
    fc = round(fc_hz)
    if roles is None:
        if any(t.kind in MAIN_SOURCES for t in ctx.topology.tops):
            code = NoteCode.CROSSOVER_TOPS_ON_MAIN
        elif set(ctx.topology.subs) & {*ctx.topology.tops, *ctx.topology.others}:
            code = NoteCode.CROSSOVER_SHARED_PATH
        else:
            code = NoteCode.CROSSOVER_SUB_NOT_ON_MATRIX
        hints = (Note(code, {"fc_hz": fc}), Note(NoteCode.CROSSOVER_CHECK_SPEAKERS))
        return Package(PackageKind.CROSSOVER, True, Risk.MEDIUM, notes=hints), None
    plan = plan_crossover(state, roles, ctx.quantizer, ctx.fs_hz, fc_hz=fc_hz)
    eq = tuple(
        PathEq(_matrix_ref(n), state.matrices[n].eq, SystemEq(True, bands))
        for n, bands in plan.targets.items()
    )
    changed = tuple(p for p in eq if p.changed)
    if not changed:
        return None, plan
    notes: list[Note] = [
        Note(NoteCode.CROSSOVER_FILTERS, {"fc_hz": round(plan.fc_hz), "slope": str(plan.slope)})
    ]
    resets: dict[int, int] = {}
    for n in plan.notes:
        if n.code in RESET_CODES:
            resets[n.matrix] = resets.get(n.matrix, 0) + 1
        elif n.code is CrossoverNoteCode.MORE_LOWS_TO_TOPS:
            params = dict(n.params)
            notes.append(
                Note(
                    NoteCode.CROSSOVER_MORE_LOWS_TO_TOPS,
                    {
                        "path": _matrix_ref(n.matrix).label(),
                        "at_hz": round(float(params["at_hz"])),
                        "old_db": round(float(params["old_db"]), 1),
                        "new_db": round(float(params["new_db"]), 1),
                    },
                )
            )
    notes.extend(
        Note(NoteCode.CROSSOVER_RESET, {"path": _matrix_ref(m).label(), "count": count})
        for m, count in resets.items()
    )
    notes.append(Note(NoteCode.CROSSOVER_CHECK_SPEAKERS))
    return (
        Package(PackageKind.CROSSOVER, False, Risk.MEDIUM, eq=changed, notes=tuple(notes)),
        plan,
    )


# ------------------------------------------------------------------ alignment and level
def _delay_changes(
    snapshot: ConsoleSnapshot, refs: tuple[SignalRef, ...], extra_ms: float
) -> tuple[tuple[OutputDelayChange, ...], bool]:
    """Delays of the main outputs fed by ``refs``; True when some path has none (by hand)."""
    changes = []
    missing = False
    for ref in refs:
        outputs = [o for o in snapshot.outputs_fed_by(ref) if o.kind is OutputKind.MAIN]
        missing = missing or not outputs
        for output in outputs:
            before = output.delay.delay_ms if output.delay and output.delay.on else 0.0
            changes.append(OutputDelayChange(output.number, before, round(before + extra_ms, 1)))
    return tuple(changes), missing


def _alignment(
    ctx: DesignContext, result: AlignmentResult, roles: Roles, requires: tuple[PackageKind, ...]
) -> tuple[Package | None, Note | None]:
    if result.gain_db < MIN_ALIGNMENT_GAIN_DB:
        return None, Note(NoteCode.ALIGNMENT_NOT_NEEDED, {"gain_db": round(result.gain_db, 1)})
    snapshot = ctx.snapshot
    side: Literal["sub", "tops"] = "sub" if result.delay_ms >= 0 else "tops"
    if side == "sub":
        refs = tuple(_matrix_ref(n) for n in roles.sub)
        code = NoteCode.ALIGNMENT_DELAY_SUB
    else:  # the tops wait, and every other speaker with the same programme with them
        refs = tuple(_matrix_ref(n) for n in (*roles.mains, *roles.others))
        code = NoteCode.ALIGNMENT_DELAY_TOPS
    delay_ms = round(abs(result.delay_ms), 1)
    delays, missing = _delay_changes(snapshot, refs, delay_ms)
    notes = [Note(code, {"delay_ms": delay_ms})]
    if missing:
        notes.append(Note(NoteCode.ALIGNMENT_DELAY_BY_HAND, {"delay_ms": delay_ms}))
    polarities: tuple[PolarityChange, ...] = ()
    if result.polarity < 0:
        subs = [snapshot.matrices[n] for n in roles.sub]
        polarities = tuple(PolarityChange(s.ref, s.invert, not s.invert) for s in subs)
        notes.append(Note(NoteCode.ALIGNMENT_POLARITY))
    package = Package(
        PackageKind.ALIGNMENT,
        True,  # output delay and polarity writes are unverified on hardware (ADR 0005)
        Risk.UNVERIFIED,
        requires=requires,
        delays=delays,
        delay_side=side,
        delay_ms=delay_ms,
        polarities=polarities,
        notes=tuple(notes),
        effect=Effect(EffectMetric.JUNCTION_SCORE, result.score_now_db, result.score_best_db),
    )
    return package, None


def _sub_level(
    ctx: DesignContext, result: AlignmentResult, roles: Roles, requires: tuple[PackageKind, ...]
) -> Package | None:
    change = recommend_level(result, cap_db=MAX_LEVEL_STEP_DB)
    if change == 0.0:
        return None
    subs = [ctx.snapshot.matrices[n] for n in roles.sub]
    if any(s.fader_db + change < LOWEST_PROPOSED_FADER_DB for s in subs):
        return None  # the console would read the fader as down (−∞): not a level to propose
    levels = tuple(LevelChange(s.ref, s.fader_db, round(s.fader_db + change, 1)) for s in subs)
    suggest_only = any(level.after_db > MAX_FADER_DB for level in levels)
    notes = [Note(NoteCode.SUB_LEVEL, {"change_db": round(change, 1)})]
    if suggest_only:
        highest = max(level.after_db for level in levels)
        notes.append(Note(NoteCode.SUB_LEVEL_ABOVE_ZERO, {"after_db": highest}))
    notes.append(Note(NoteCode.SUB_LEVEL_BY_EAR))
    diff = result.sub_minus_tops_at_fc_db
    return Package(
        PackageKind.SUB_LEVEL,
        suggest_only,
        Risk.MEDIUM,
        requires=requires,
        levels=levels,
        notes=tuple(notes),
        effect=Effect(EffectMetric.SUB_MINUS_TOPS, diff, round(diff + change, 1)),
    )


def _shift(ir: FloatArray, samples: float) -> FloatArray:
    n = round(samples)
    return ir if n <= 0 else np.concatenate([np.zeros(n), ir[:-n]])


def _junction_after(
    junction: Mapping[str, JunctionPoint], packages: Collection[Package], fs_hz: float
) -> dict[str, FloatArray]:
    """The "both" response of each junction point after the alignment and level packages."""
    sub_gain, sub_sign, sub_ms, tops_ms = 1.0, 1.0, 0.0, 0.0
    for package in packages:
        if package.levels:
            level = package.levels[0]
            sub_gain = 10 ** ((level.after_db - level.before_db) / 20)
        if package.polarities:
            sub_sign = -1.0
        if package.delay_side == "sub":
            sub_ms = package.delay_ms
        elif package.delay_side == "tops":
            tops_ms = package.delay_ms
    return {
        name: np.asarray(
            _shift(point.tops, tops_ms * 1e-3 * fs_hz)
            + sub_sign * sub_gain * _shift(point.sub, sub_ms * 1e-3 * fs_hz),
            dtype=np.float64,
        )
        for name, point in junction.items()
    }


def predicted_analysis(
    analysis: Analysis,
    measured: Mapping[str, JunctionPoint],
    after: Mapping[str, FloatArray],
    fs_hz: float,
) -> Analysis:
    """The analysis as the system will sound after the junction packages (predicted).

    Each junction point changes by its own "after" minus "as measured"; a point without a
    junction measurement changes by the mean change. The average, spread and range are computed
    again, so the system EQ is designed for the system after the packages before it, not for a
    junction they already fix. The verify measurement tells the truth.

    Raises:
        ValueError: No junction point is a point of the analysis.
    """
    if not set(measured) & set(analysis.norm_db):
        raise ValueError("the junction points are not points of the analysis")
    deltas = {
        name: magnitude_db(after[name], fs_hz) - magnitude_db(point.both, fs_hz)
        for name, point in measured.items()
    }
    mean_delta = np.asarray(np.mean(list(deltas.values()), axis=0), dtype=np.float64)

    def moved(curves: Mapping[str, FloatArray]) -> dict[str, FloatArray]:
        return {k: v + deltas.get(k, mean_delta) for k, v in curves.items()}

    norm = moved(analysis.norm_db)
    curves = np.array(list(norm.values()))
    return replace(
        analysis,
        raw_db=moved(analysis.raw_db),
        norm_db=norm,
        avg_db=energy_average(curves),
        spread_db=np.asarray(curves.std(axis=0), dtype=np.float64),
        min_db=np.asarray(curves.min(axis=0), dtype=np.float64),
        max_db=np.asarray(curves.max(axis=0), dtype=np.float64),
    )


# ------------------------------------------------------------------ system EQ
def eq_paths(ctx: DesignContext, fc_hz: float) -> tuple[tuple[SignalRef, ...], float]:
    """Where the system EQ goes (SPEC §8.5) and the lowest frequency it may correct.

    A path that feeds every speaker (Main LR feeding the tops and the sub) carries it and may
    correct the whole range. Otherwise it goes on the tops (Main LR when they hang on it, else
    every top matrix) and stays above the crossover: the sub range is not theirs.
    """
    topology = ctx.topology
    speakers = (*topology.tops, *topology.subs)
    main = SignalRef(SourceKind.MAIN_L)
    if speakers and all(_fed_by_main(ctx.snapshot, ref) for ref in speakers):
        return (main,), ctx.analysis.f_low_hz
    floor = max(ctx.analysis.f_low_hz, fc_hz) if topology.subs else ctx.analysis.f_low_hz
    if all(t.kind in MAIN_SOURCES for t in topology.tops):
        return (main,), floor
    tops = tuple(sorted({t for t in topology.tops if t.kind is SourceKind.MATRIX}, key=str))
    return tops, floor


def _eq_of(ctx: DesignContext, ref: SignalRef, crossover: CrossoverPlan | None) -> SystemEq:
    """The EQ of a path after the crossover package (or as it is, without one)."""
    if ref.kind is SourceKind.MATRIX and crossover and ref.number in crossover.targets:
        return SystemEq(True, crossover.targets[ref.number])
    strip = ctx.snapshot.strip(ref)
    if strip is None:
        raise ValueError(f"no strip behind {ref.label()}")
    return strip_eq(strip)


def _other_main_outputs(ctx: DesignContext) -> bool:
    """True when Main LR also feeds outputs that are not the tops (stream, recording, foyer)."""
    for guess in ctx.topology.outputs:
        if guess.path.kind in MAIN_SOURCES and guess.role not in TOP_ROLES:
            return True
    speaker_outputs = {(g.kind, g.number) for g in ctx.topology.outputs}
    return any(
        o.source.kind in MAIN_SOURCES and (o.kind, o.number) not in speaker_outputs
        for o in ctx.snapshot.outputs
    )


def _without_tiny_moves(start: SystemEq, designed: SystemEq) -> SystemEq:
    """The designed EQ with inaudible moves of existing bands put back.

    Less than 1/12 octave and 0.5 dB: such a move would only make the table longer.
    """
    bands = tuple(
        old
        if old.adjustable
        and new.adjustable
        and old.type is new.type
        and old.gain_db != 0
        and abs(np.log2(new.freq_hz / old.freq_hz)) < TINY_MOVE_OCT
        and abs(new.gain_db - old.gain_db) < TINY_MOVE_DB
        else new
        for old, new in zip(start.bands, designed.bands, strict=True)
    )
    return SystemEq(designed.on, bands)


def _eq_label(ref: SignalRef) -> str:
    """The label of an EQ path; Main LR is one block (``main_lr``), not its left side."""
    return MAIN_LR_LABEL if ref.kind is SourceKind.MAIN_L else ref.label()


def system_eq_effect(prediction: Prediction) -> Effect | None:
    """The effect a system EQ is proposed for, or None when it is not worth a screen.

    The RMS deviation over the range when it improves by at least ``MIN_EQ_GAIN_DB``; otherwise
    the largest excess above the target when that drops by at least ``MIN_EQ_PEAK_GAIN_DB`` and
    the RMS deviation does not get worse (a hump or a mode fixed well moves the RMS little). The
    predictions are rounded to 0.01 dB and so are the differences, so a gain of exactly a
    threshold is not lost to floating-point noise.

    Args:
        prediction: The modelled result of the proposed EQ.
    """
    rms_gain_db = round(prediction.rms_dev_before_db - prediction.rms_dev_after_db, 2)
    peak_gain_db = round(prediction.max_dev_before_db - prediction.max_dev_after_db, 2)
    if rms_gain_db >= MIN_EQ_GAIN_DB:
        return Effect(
            EffectMetric.RMS_DEVIATION, prediction.rms_dev_before_db, prediction.rms_dev_after_db
        )
    if rms_gain_db >= 0 and peak_gain_db >= MIN_EQ_PEAK_GAIN_DB:
        return Effect(
            EffectMetric.MAX_EXCESS, prediction.max_dev_before_db, prediction.max_dev_after_db
        )
    return None


def present_crossover_hz(ctx: DesignContext) -> float | None:
    """The crossover frequency set on the console now, or None when there is none.

    The cut filter on the last band of the sub's matrix (a low-pass) and on the first band of a
    top matrix (a high-pass), within 50..250 Hz and with that EQ switched on; their geometric
    mean when both are there.
    """
    roles = matrix_roles(ctx.topology)
    if roles is None:
        return None
    state = console_state(ctx.snapshot)
    found = []
    for numbers, band_index in ((roles.sub, -1), (roles.mains, 0)):
        eqs = [state.matrices[n].eq for n in numbers if n in state.matrices]
        bands = [eq.bands[band_index] for eq in eqs if eq.on]  # a switched-off EQ filters nothing
        cut = next(
            (b for b in bands if b.type in CUT_FILTER_TYPES and _in_crossover_range(b.freq_hz)),
            None,
        )
        if cut is not None:
            found.append(cut.freq_hz)
    return float(np.exp(np.mean(np.log(found)))) if found else None


def _in_crossover_range(freq_hz: float) -> bool:
    low, high = PRESENT_CROSSOVER_RANGE_HZ
    return low <= freq_hz <= high


def _system_eq(  # noqa: PLR0913 - the EQ needs the whole picture of the packages before it
    ctx: DesignContext,
    fc_hz: float,
    crossover: CrossoverPlan | None,
    base: Analysis,
    *,
    requires: tuple[PackageKind, ...],
    extra_notes: tuple[Note, ...],
) -> Package | None:
    paths, f_low_hz = eq_paths(ctx, fc_hz)
    analysis = replace(base, f_low_hz=f_low_hz)
    # Without the junction measured, the EQ stays broad and gentle there and no seat may suffer;
    # the zone sits around the crossover the console has, when it has one.
    blind = None
    if ctx.topology.subs and not ctx.junction:
        blind = BlindJunction.around(present_crossover_hz(ctx) or fc_hz)
    eq: list[PathEq] = []
    prediction: Prediction | None = None
    for ref in paths:  # every path is designed on its own: locked bands may differ per path
        start = _eq_of(ctx, ref, crossover)
        proposal = optimize_design(
            analysis, start, ctx.quantizer, ctx.fs_hz, target=ctx.target, blind_junction=blind
        )
        eq.append(PathEq(ref, start, _without_tiny_moves(start, proposal.to_eq())))
        if prediction is None:
            prediction = proposal.prediction
    changed = tuple(p for p in eq if p.changed)
    effect = system_eq_effect(prediction) if prediction is not None else None
    if not changed or effect is None:
        return None  # nothing worth a screen: no real improvement expected
    notes = [Note(NoteCode.SYSTEM_EQ_ON, {"path": _eq_label(p.path)}) for p in changed]
    if f_low_hz > ctx.analysis.f_low_hz:
        notes.append(Note(NoteCode.SYSTEM_EQ_ABOVE_FC, {"fc_hz": round(f_low_hz)}))
    if blind is not None:
        zone = {"low_hz": round(blind.low_hz), "high_hz": round(blind.high_hz)}
        notes.append(Note(NoteCode.SYSTEM_EQ_BLIND_JUNCTION, zone))
    notes.extend(extra_notes)
    if any(p.path.kind in MAIN_SOURCES for p in changed) and _other_main_outputs(ctx):
        notes.append(Note(NoteCode.SYSTEM_EQ_ALSO_OTHER_OUTPUTS))
    return Package(
        PackageKind.SYSTEM_EQ,
        False,
        Risk.LOW,
        requires=requires,
        eq=changed,
        notes=tuple(notes),
        effect=effect,
    )


# ------------------------------------------------------------------ all of it
def build_packages(ctx: DesignContext, left_out: Collection[PackageKind] = frozenset()) -> Packages:
    """Design every package that applies, in the order of application.

    Args:
        ctx: What the design starts from.
        left_out: Packages the person did not keep: the later ones are designed without them.
    """
    state = console_state(ctx.snapshot)
    roles = matrix_roles(ctx.topology)
    junction = None
    if ctx.junction and roles is not None:
        junction = _Junction(
            ctx.junction, state.matrices[roles.sub[0]].eq, state.matrices[roles.mains[0]].eq
        )
    natural = None
    if junction is not None:
        natural = junction.through(_off(junction.sub_now), _off(junction.tops_now), ctx.fs_hz)
    fc_hz = ctx.quantizer.snap_freq_hz(choose_fc(natural, ctx.fs_hz))
    packages: list[Package] = []
    notes: list[Note] = []
    kept: list[PackageKind] = []

    if ctx.junction:
        crossover, plan = _crossover(ctx, state, roles, fc_hz)
    else:  # a crossover set blind can open a hole at the junction: measure sub and tops first
        crossover, plan = None, None
    if crossover is not None and PackageKind.CROSSOVER not in left_out:
        packages.append(crossover)
        kept.append(PackageKind.CROSSOVER)
    elif crossover is not None:
        plan = None  # left out: the later packages see the console as it is
    eq_base = ctx.analysis
    eq_notes: tuple[Note, ...] = ()
    if junction is not None and roles is not None:
        sub_eq = _eq_of(ctx, _matrix_ref(roles.sub[0]), plan)
        tops_eq = _eq_of(ctx, _matrix_ref(roles.mains[0]), plan)
        predicted = junction.through(sub_eq, tops_eq, ctx.fs_hz)
        result = analyze_alignment(predicted, fc_hz, ctx.fs_hz)
        alignment, note = _alignment(ctx, result, roles, tuple(kept))
        notes.extend(n for n in (note,) if n is not None)
        if alignment is not None and PackageKind.ALIGNMENT not in left_out:
            packages.append(alignment)
            kept.append(PackageKind.ALIGNMENT)
        level = _sub_level(ctx, result, roles, tuple(kept))
        if level is not None and PackageKind.SUB_LEVEL not in left_out:
            packages.append(level)
            kept.append(PackageKind.SUB_LEVEL)
        if kept:  # the EQ is designed for the system after the junction packages
            after = _junction_after(predicted, packages, ctx.fs_hz)
            eq_base = predicted_analysis(ctx.analysis, ctx.junction or {}, after, ctx.fs_hz)
            eq_notes = (Note(NoteCode.SYSTEM_EQ_PREDICTED_JUNCTION),)
    elif ctx.topology.subs:
        notes.append(Note(NoteCode.JUNCTION_NOT_MEASURED))
    system_eq = _system_eq(ctx, fc_hz, plan, eq_base, requires=tuple(kept), extra_notes=eq_notes)
    if system_eq is not None and PackageKind.SYSTEM_EQ not in left_out:
        packages.append(system_eq)
    return Packages(fc_hz, tuple(packages), tuple(notes))
