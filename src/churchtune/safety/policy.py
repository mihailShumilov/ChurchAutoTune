"""Write policy: which console parameters may be written, and with which values.

Two scopes of the proven single-hall tool, used by the expert CLI:
  * ``main`` — Main LR EQ only: ``/main/st/eq/on`` and ``/main/st/eq/[1-6]/(type|f|g|q)``.
    No cut-filter types may be set.
  * ``sub``  — EQ, mute, fader and polarity of the venue's matrices, and output delays, with
    value limits. Cut-filter types only on band 1 (high-pass) and band 6 (low-pass).

The scopes of SPEC §9.2 (M5), stricter, one per kind of change:
  * ``temp_mutes`` — matrix mutes only (channel, bus and Main mutes were never verified on
    hardware and stay by hand): value 0, or exactly the original recorded before the window.
  * ``system_eq`` — Main LR EQ and the EQ of the sub and top matrices: boosts up to +3 dB
    (Main, tops) or +6 dB (sub); an EQ is never switched off; cut filters only when the policy
    allows them (the crossover package), and only band 1 of a top matrix or band 6 of the sub.
  * ``levels`` — the sub matrices' faders: at most 10 dB from the fresh read, never above 0 dB.
  * ``delay`` and ``polarity`` — output delays (≤ 50 ms) and the sub matrix polarity; both
    never verified on hardware, so refused without ``allow_unverified_writes``.

Writes that were never confirmed on hardware (output delay, matrix polarity) are refused unless
the policy is created with ``allow_unverified_writes=True``; see docs/hardware-checklist.md.

Everything not listed is refused. The policy is plain and table-driven on purpose.
"""

import math
import re
from dataclasses import dataclass

from churchtune.domain.changes import Change, WireValue, WriteScope
from churchtune.domain.eq import ADJUSTABLE_TYPES, CUT_FILTER_TYPES, EqType
from churchtune.errors import UnsafeWrite
from churchtune.safety.laws import ValueLaws

MAIN_PATH_RE = re.compile(r"/main/st/eq(/on|/[1-6]/(type|f|g|q))")
SUB_EQ_PATH_RE = re.compile(r"/mtx/0([1-6])/eq/([1-6])/(type|f|g|q)")
SUB_MATRIX_PATH_RE = re.compile(r"/mtx/0([1-6])/(mix/on|mix/fader|preamp/invert)")
SUB_DELAY_PATH_RE = re.compile(r"/outputs/main/(0[1-9]|1[0-6])/delay/(on|time)")
MATRIX_MUTE_RE = re.compile(r"/mtx/0([1-6])/mix/on")
MATRIX_FADER_RE = re.compile(r"/mtx/0([1-6])/mix/fader")
MATRIX_EQ_RE = re.compile(r"/mtx/0([1-6])/eq(/on|/([1-6])/(type|f|g|q))")
MATRIX_POLARITY_RE = re.compile(r"/mtx/0([1-6])/preamp/invert")
LEVEL_STEP_CEILING_DB = 10.0

# Cut-filter types by direction. The slope types are high-pass on band 1 and low-pass on band 6.
_SLOPES = frozenset(CUT_FILTER_TYPES - {EqType.LCUT, EqType.HCUT})
HPF_TYPES: frozenset[EqType] = _SLOPES | {EqType.LCUT}
LPF_TYPES: frozenset[EqType] = _SLOPES | {EqType.HCUT}
HPF_BAND = 1
LPF_BAND = 6

SWITCH_KINDS = frozenset({"on", "invert"})
# UNVERIFIED: these writes were never done on hardware (docs/hardware-checklist.md #1, #2).
UNVERIFIED_KINDS = frozenset({"invert"})


def is_unverified_write(path: str) -> bool:
    """Whether writing ``path`` was never confirmed on hardware (output delay, polarity)."""
    kind = path.rsplit("/", 1)[-1]
    return bool(SUB_DELAY_PATH_RE.fullmatch(path)) or kind in UNVERIFIED_KINDS


# Ceilings no configuration may exceed; a venue can only tighten them.
MAIN_BOOST_CEILING_DB = 3.0
SUB_BOOST_CEILING_DB = 6.0
SUB_FADER_CEILING_DB = 0.0
SUB_DELAY_CEILING_MS = 50.0
_SWITCH_VALUES = (0, 1)
# Slack for float comparison of limits; values are pre-quantized, so this never admits a step.
_EPS = 1e-6


@dataclass(frozen=True, slots=True)
class WriteLimits:
    """Value limits of the write policy.

    Attributes:
        main_max_boost_db: Highest EQ gain accepted on Main LR.
        sub_max_boost_db: Highest EQ gain accepted on matrix EQ.
        sub_max_fader_db: Highest matrix fader level accepted.
        sub_max_delay_ms: Longest output delay accepted.
    """

    main_max_boost_db: float = MAIN_BOOST_CEILING_DB
    sub_max_boost_db: float = SUB_BOOST_CEILING_DB
    sub_max_fader_db: float = SUB_FADER_CEILING_DB
    sub_max_delay_ms: float = SUB_DELAY_CEILING_MS

    def __post_init__(self) -> None:
        ceilings = (
            ("main_max_boost_db", self.main_max_boost_db, MAIN_BOOST_CEILING_DB),
            ("sub_max_boost_db", self.sub_max_boost_db, SUB_BOOST_CEILING_DB),
            ("sub_max_fader_db", self.sub_max_fader_db, SUB_FADER_CEILING_DB),
            ("sub_max_delay_ms", self.sub_max_delay_ms, SUB_DELAY_CEILING_MS),
        )
        for name, value, ceiling in ceilings:
            # NaN fails both comparisons and is refused; a limit may only be tightened.
            if not (math.isfinite(value) and value <= ceiling):
                raise ValueError(f"{name} must be a finite number not above {ceiling}, got {value}")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_norm(value: object) -> bool:
    # NaN fails the range comparison and is refused.
    return isinstance(value, float) and 0.0 <= value <= 1.0


@dataclass(frozen=True, slots=True)
class WritePolicy:
    """The allowlist and value validators for one venue.

    Attributes:
        laws: The console's value encoding.
        matrices: Matrices that may be written in the ``sub`` scope (the venue's sub, top and
            other speaker paths). Every parameter of any other matrix is refused.
        limits: Value limits.
        allow_unverified_writes: Accept the writes that were never confirmed on hardware
            (output delay, matrix polarity). Off by default.
        sub_matrices: The sub path's matrices (levels, polarity, +6 dB boosts, band-6 LPF).
        top_matrices: The top speakers' matrices (+3 dB boosts, band-1 HPF).
        mute_originals: For each matrix mute of a temporary-mute window, its value before the
            window; only 0 or that value may be written in ``temp_mutes``.
        level_baselines: For each sub fader, its level in dB from the fresh read before the plan.
        allow_cut_filters: Accept cut-filter types in ``system_eq`` (the crossover package).
    """

    laws: ValueLaws
    matrices: frozenset[int]
    limits: WriteLimits = WriteLimits()
    allow_unverified_writes: bool = False
    sub_matrices: frozenset[int] = frozenset()
    top_matrices: frozenset[int] = frozenset()
    mute_originals: tuple[tuple[str, int], ...] = ()
    level_baselines: tuple[tuple[str, float], ...] = ()
    allow_cut_filters: bool = False

    def __post_init__(self) -> None:
        if self.sub_matrices & self.top_matrices:
            raise ValueError("a matrix cannot be both a sub and a top path")
        if not (self.sub_matrices | self.top_matrices) <= self.matrices:
            raise ValueError("sub and top matrices must be venue matrices (not venue matrices)")

    def validate(self, path: str, value: WireValue, scope: WriteScope) -> None:
        """Refuse a write that is outside the scope's allowlist or limits.

        Args:
            path: Console parameter address.
            value: Value as it would go on the wire (normalized float or int).
            scope: Scope the write belongs to.

        Raises:
            UnsafeWrite: The path, the value type or the value is not allowed.
        """
        validators = {
            WriteScope.MAIN: self._validate_main,
            WriteScope.SUB: self._validate_sub,
            WriteScope.TEMP_MUTES: self._validate_temp_mute,
            WriteScope.SYSTEM_EQ: self._validate_system_eq,
            WriteScope.LEVELS: self._validate_level,
            WriteScope.DELAY: self._validate_delay,
            WriteScope.POLARITY: self._validate_polarity,
        }
        validator = validators.get(scope)
        if validator is None:
            raise UnsafeWrite(f"unknown scope {scope!r}", path=path)
        validator(path, value)

    def checked_change(self, change: Change, scope: WriteScope) -> Change:
        """Return ``change`` after validating its new value in ``scope``."""
        self.validate(change.path, change.new_value, scope)
        return change

    def _eq_type(self, path: str, value: WireValue) -> EqType:
        if not _is_int(value) or not 0 <= value < len(self.laws.eq_types):
            raise UnsafeWrite(f"bad type value {value!r} for {path}", path=path)
        return self.laws.eq_types[int(value)]

    def _validate_main(self, path: str, value: WireValue) -> None:
        if not MAIN_PATH_RE.fullmatch(path):
            raise UnsafeWrite(f"path not allowed: {path}", path=path)
        kind = path.rsplit("/", 1)[1]
        if kind == "type":
            if self._eq_type(path, value) in CUT_FILTER_TYPES:
                raise UnsafeWrite(f"refusing to set cut-filter type via {path}", path=path)
        elif kind == "on":
            if not _is_int(value) or value not in _SWITCH_VALUES:
                raise UnsafeWrite(f"bad on value {value!r}", path=path)
        elif not _is_norm(value):
            raise UnsafeWrite(f"bad normalized value {value!r} for {path}", path=path)
        elif kind == "g" and self.laws.gain_db(float(value)) > self.limits.main_max_boost_db + _EPS:
            # The plan already refuses this; repeated here so the writer's own check catches it too.
            raise UnsafeWrite(
                f"boost {self.laws.gain_db(float(value)):+.2f} dB > "
                f"+{self.limits.main_max_boost_db} dB refused ({path})",
                path=path,
            )

    def _validate_sub(self, path: str, value: WireValue) -> None:
        matrix_match = SUB_EQ_PATH_RE.fullmatch(path) or SUB_MATRIX_PATH_RE.fullmatch(path)
        delay_match = SUB_DELAY_PATH_RE.fullmatch(path)
        if not (matrix_match or delay_match):
            raise UnsafeWrite(f"path not allowed in sub scope: {path}", path=path)
        if matrix_match and int(matrix_match.group(1)) not in self.matrices:
            raise UnsafeWrite(f"this matrix is not in the sub scope: {path}", path=path)
        kind = path.rsplit("/", 1)[1]
        if is_unverified_write(path) and not self.allow_unverified_writes:
            raise UnsafeWrite(
                f"writing {path} was never verified on hardware; set the value by hand",
                path=path,
                unverified=True,
            )
        if kind in SWITCH_KINDS:
            if not _is_int(value) or value not in _SWITCH_VALUES:
                raise UnsafeWrite(f"bad on/off value {value!r} for {path}", path=path)
            return
        if kind == "type":
            self._validate_sub_type(path, value, band=int(path.rsplit("/", 2)[1]))
            return
        if not _is_norm(value):
            raise UnsafeWrite(f"bad normalized value {value!r} for {path}", path=path)
        self._validate_sub_limit(path, kind, float(value))

    def _validate_sub_type(self, path: str, value: WireValue, band: int) -> None:
        eq_type = self._eq_type(path, value)
        allowed = (
            ADJUSTABLE_TYPES
            | (HPF_TYPES if band == HPF_BAND else frozenset())
            | (LPF_TYPES if band == LPF_BAND else frozenset())
        )
        if eq_type not in allowed:
            raise UnsafeWrite(f"type {eq_type} not allowed on band {band} ({path})", path=path)

    def _validate_sub_limit(self, path: str, kind: str, x_norm: float) -> None:
        limits = self.limits
        if kind == "g" and self.laws.gain_db(x_norm) > limits.sub_max_boost_db + _EPS:
            raise UnsafeWrite(
                f"boost {self.laws.gain_db(x_norm):+.2f} dB > +{limits.sub_max_boost_db} dB "
                f"refused ({path})",
                path=path,
            )
        if kind == "fader" and self.laws.fader_db(x_norm) > limits.sub_max_fader_db + _EPS:
            raise UnsafeWrite(
                f"fader {self.laws.fader_db(x_norm):+.1f} dB > {limits.sub_max_fader_db} dB "
                f"refused ({path})",
                path=path,
            )
        if kind == "time" and self.laws.delay_ms(x_norm) > limits.sub_max_delay_ms + _EPS:
            raise UnsafeWrite(
                f"delay {self.laws.delay_ms(x_norm):.1f} ms > {limits.sub_max_delay_ms} ms "
                f"refused ({path})",
                path=path,
            )

    # ------------------------------------------------------------ the scopes of SPEC §9.2
    def _require_unverified_opt_in(self, path: str) -> None:
        if not self.allow_unverified_writes:
            raise UnsafeWrite(
                f"writing {path} was never verified on hardware; set the value by hand",
                path=path,
                unverified=True,
            )

    def _validate_temp_mute(self, path: str, value: WireValue) -> None:
        match = MATRIX_MUTE_RE.fullmatch(path)
        if not match:
            raise UnsafeWrite(f"only matrix mutes are temporary mutes: {path}", path=path)
        speaker = int(match.group(1)) in self.sub_matrices | self.top_matrices
        if value != 0 and not speaker:
            # Muting is never louder; switching on is only for the sub's and the tops' matrices.
            raise UnsafeWrite(f"only a speaker matrix is switched back on: {path}", path=path)
        originals = dict(self.mute_originals)
        if path not in originals:
            raise UnsafeWrite(f"no original value recorded for {path}", path=path)
        if not _is_int(value) or value not in (0, originals[path]):
            raise UnsafeWrite(
                f"{path} may only be muted or restored, not set to {value!r}", path=path
            )

    def _validate_system_eq(self, path: str, value: WireValue) -> None:
        main = MAIN_PATH_RE.fullmatch(path)
        matrix = MATRIX_EQ_RE.fullmatch(path)
        number = int(matrix.group(1)) if matrix else None
        is_sub = number in self.sub_matrices
        if not (main or (number is not None and (is_sub or number in self.top_matrices))):
            raise UnsafeWrite(f"path not allowed in system_eq: {path}", path=path)
        kind = path.rsplit("/", 1)[1]
        if kind == "on":
            if not _is_int(value) or value != 1:
                raise UnsafeWrite(f"an EQ is never switched off by a plan ({path})", path=path)
            return
        band = int(path.rsplit("/", 2)[1])
        if kind == "type":
            eq_type = self._eq_type(path, value)
            if eq_type in ADJUSTABLE_TYPES:
                return
            crossover_band = (number is not None) and (
                (is_sub and band == LPF_BAND and eq_type in LPF_TYPES)
                or (not is_sub and band == HPF_BAND and eq_type in HPF_TYPES)
            )
            if not (self.allow_cut_filters and crossover_band):
                raise UnsafeWrite(f"cut filter {eq_type} not allowed at {path}", path=path)
            return
        if not _is_norm(value):
            raise UnsafeWrite(f"bad normalized value {value!r} for {path}", path=path)
        ceiling = self.limits.sub_max_boost_db if is_sub else self.limits.main_max_boost_db
        if kind == "g" and self.laws.gain_db(float(value)) > ceiling + _EPS:
            raise UnsafeWrite(
                f"boost {self.laws.gain_db(float(value)):+.2f} dB > +{ceiling} dB refused ({path})",
                path=path,
            )

    def _validate_level(self, path: str, value: WireValue) -> None:
        match = MATRIX_FADER_RE.fullmatch(path)
        if not match or int(match.group(1)) not in self.sub_matrices:
            raise UnsafeWrite(f"only the sub path's fader is a level: {path}", path=path)
        if not _is_norm(value):
            raise UnsafeWrite(f"bad normalized value {value!r} for {path}", path=path)
        baselines = dict(self.level_baselines)
        if path not in baselines:
            raise UnsafeWrite(f"no fresh reading of {path} to limit the step", path=path)
        level = self.laws.fader_db(float(value))
        if level > self.limits.sub_max_fader_db + _EPS:
            raise UnsafeWrite(f"fader {level:+.1f} dB above 0 dB refused ({path})", path=path)
        if abs(level - baselines[path]) > LEVEL_STEP_CEILING_DB + _EPS:
            raise UnsafeWrite(
                f"fader step {level - baselines[path]:+.1f} dB beyond ±10 dB refused ({path})",
                path=path,
            )

    def _validate_delay(self, path: str, value: WireValue) -> None:
        if not SUB_DELAY_PATH_RE.fullmatch(path):
            raise UnsafeWrite(f"path not allowed in delay: {path}", path=path)
        self._require_unverified_opt_in(path)
        if path.endswith("/on"):
            if not _is_int(value) or value not in _SWITCH_VALUES:
                raise UnsafeWrite(f"bad on/off value {value!r} for {path}", path=path)
            return
        if not _is_norm(value):
            raise UnsafeWrite(f"bad normalized value {value!r} for {path}", path=path)
        self._validate_sub_limit(path, "time", float(value))

    def _validate_polarity(self, path: str, value: WireValue) -> None:
        match = MATRIX_POLARITY_RE.fullmatch(path)
        if not match or int(match.group(1)) not in self.sub_matrices:
            raise UnsafeWrite(f"only the sub matrix polarity may change: {path}", path=path)
        self._require_unverified_opt_in(path)
        if not _is_int(value) or value not in _SWITCH_VALUES:
            raise UnsafeWrite(f"bad polarity value {value!r} for {path}", path=path)
