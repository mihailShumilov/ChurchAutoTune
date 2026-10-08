"""The system-EQ proposal as pure data.

Reasons and warnings are codes with parameters, not sentences: the report and the UI turn them
into text in the user's language.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from churchtune.domain.eq import Band, SystemEq

ReasonParams = Mapping[str, float | int | str | bool | None]


class Method(StrEnum):
    """How the proposal was computed."""

    GREEDY = "greedy"
    OPTIMIZE = "optimize"


class Action(StrEnum):
    """What happens to a band."""

    KEEP = "keep"
    MODIFY = "modify"  # an active band changes
    NEW = "new"  # a band that was at 0 dB gets a job


class CandidateKind(StrEnum):
    """What a detected feature would need."""

    CUT = "cut"
    BOOST = "boost"
    RELAX = "relax"  # an existing cut is too deep and gets shallower


class ReasonCode(StrEnum):
    """Why a band is what it is in the proposal."""

    LOCKED_CUT_FILTER = "locked-cut-filter"  # cut filter: never changed
    UNCHANGED = "unchanged"
    UNCHANGED_INACTIVE = "unchanged-inactive"  # band at 0 dB, nothing to do
    UNCHANGED_EXISTING = "unchanged-existing"  # existing setting, no problem found nearby
    CUT = "cut"  # the average is above the target in most points
    BOOST = "boost"  # broad dip common to the points, capped boost
    RELAX = "relax"  # existing cut sits in a dip and is made shallower
    NOTCH_KEPT = "notch-kept"  # narrow existing cut (feedback?): at most halved
    NOT_NEEDED = "not-needed"  # the band goes back to 0 dB
    OPTIMIZED = "optimized"  # set by the joint optimisation


class SkipCode(StrEnum):
    """Why a detected feature gets no band."""

    LOCAL_PEAK = "local-peak"  # peak in too few points
    NO_FREE_BAND = "no-free-band"
    NARROW_DIP = "narrow-dip"  # interference or a null: EQ cannot fix it
    LOCAL_DIP = "local-dip"  # dip in too few points
    LARGE_SPREAD = "large-spread"  # depends on the position
    NEAR_ROLLOFF = "near-rolloff"  # next to the speakers' natural low roll-off
    TOO_SMALL = "too-small"  # computed correction below the minimum effect
    NOT_COMMON = "not-common"  # the optimiser's band did not address a common problem


class DesignWarning(StrEnum):
    """Something the operator must know before applying the proposal."""

    EQ_CHANGED_SINCE_MEASUREMENT = "eq-changed-since-measurement"
    EQ_WAS_OFF = "eq-was-off"


@dataclass(frozen=True, slots=True)
class Reason:
    """A coded reason with the numbers that go into its text."""

    code: ReasonCode | SkipCode
    params: ReasonParams


@dataclass(frozen=True, slots=True)
class BandProposal:
    """One band of the proposal.

    Attributes:
        band: The proposed band.
        was: The band as it was during the measurement.
        action: Keep, modify or new.
        approximate: True above 10 kHz, where an uncalibrated mic is not reliable.
        reason: Why.
    """

    band: Band
    was: Band
    action: Action
    approximate: bool
    reason: Reason


@dataclass(frozen=True, slots=True)
class Skipped:
    """A detected feature that gets no band.

    Attributes:
        kind: What it would have needed.
        freq_hz: Where it is.
        dev_db: Deviation of the average from the target there.
        width_oct: Its half-height width in octaves.
        points: Number of points that show it.
        reason: Why it is left alone.
    """

    kind: CandidateKind
    freq_hz: float
    dev_db: float
    width_oct: float
    points: int
    reason: Reason


@dataclass(frozen=True, slots=True)
class Prediction:
    """Modelled deviation from the target before and after, in dB, over ``range_hz``."""

    rms_dev_before_db: float
    rms_dev_after_db: float
    max_dev_before_db: float
    max_dev_after_db: float
    range_hz: tuple[float, float]


@dataclass(frozen=True, slots=True)
class TargetSpec:
    """The house curve: flat to ``tilt_from_hz``, then a tilt; plus a low shelf.

    Attributes:
        tilt_db_per_oct: Slope above ``tilt_from_hz`` (negative falls).
        tilt_from_hz: Where the tilt starts.
        bass_lift_db: Low-shelf lift; half of it at ``bass_from_hz``, about all an octave below.
        bass_from_hz: Corner of the low shelf.
    """

    tilt_db_per_oct: float = -1.0
    tilt_from_hz: float = 1000.0
    bass_lift_db: float = 3.0
    bass_from_hz: float = 100.0


@dataclass(frozen=True, slots=True)
class Proposal:
    """A system-EQ proposal for one EQ block.

    Attributes:
        method: How it was computed.
        n_points: Number of measured points behind it.
        target: The target curve.
        f_low_hz: Below this nothing is corrected.
        eq_on: The EQ block is switched on by the proposal.
        bands: The six bands.
        skipped: Features that were found and deliberately left alone.
        warnings: What the operator must know.
        prediction: Modelled effect; the verify measurement tells the truth.
        eq_at_measurement: EQ that was active while measuring (the design starts from it).
        eq_current: EQ on the console when the proposal was made, if it was read.
    """

    method: Method
    n_points: int
    target: TargetSpec
    f_low_hz: float
    eq_on: bool
    bands: tuple[BandProposal, ...]
    skipped: tuple[Skipped, ...]
    warnings: tuple[DesignWarning, ...]
    prediction: Prediction
    eq_at_measurement: SystemEq
    eq_current: SystemEq | None

    def to_eq(self) -> SystemEq:
        """The proposed EQ block."""
        return SystemEq(self.eq_on, tuple(b.band for b in self.bands))
