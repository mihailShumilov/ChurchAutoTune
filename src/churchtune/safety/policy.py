"""Write policy: which console parameters may be written, and with which values.

Two scopes, as in the proven single-hall tool (the finer scopes of SPEC §9.2 arrive in M5):
  * ``main`` — Main LR EQ only: ``/main/st/eq/on`` and ``/main/st/eq/[1-6]/(type|f|g|q)``.
    No cut-filter types may be set.
  * ``sub``  — EQ, mute, fader and polarity of the venue's matrices, and output delays, with
    value limits. Cut-filter types only on band 1 (high-pass) and band 6 (low-pass).

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
    """

    laws: ValueLaws
    matrices: frozenset[int]
    limits: WriteLimits = WriteLimits()
    allow_unverified_writes: bool = False

    def validate(self, path: str, value: WireValue, scope: WriteScope) -> None:
        """Refuse a write that is outside the scope's allowlist or limits.

        Args:
            path: Console parameter address.
            value: Value as it would go on the wire (normalized float or int).
            scope: Scope the write belongs to.

        Raises:
            UnsafeWrite: The path, the value type or the value is not allowed.
        """
        if scope is WriteScope.MAIN:
            self._validate_main(path, value)
        elif scope is WriteScope.SUB:
            self._validate_sub(path, value)
        else:
            raise UnsafeWrite(f"unknown scope {scope!r}", path=path)

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
