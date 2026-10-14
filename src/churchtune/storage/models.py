"""Schemas of the files ChurchTune writes. Every file carries ``schema_version``.

The shapes follow the files of the original single-hall tool (version 0, no version field);
``migrations`` lifts those to version 1 on reading.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from churchtune.domain.changes import Change, WriteScope
from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.domain.levels import HARD_LIMIT_DBFS
from churchtune.domain.snapshot import ConsoleFamily, ConsoleIdentity, ConsoleSnapshot

SCHEMA_VERSION: Literal[1] = 1
ParamValue = float | int | str | bool | None
MATRIX_COUNT = 6


class FileModel(BaseModel):
    """Base of every stored model: immutable, unknown keys are an error."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class BandModel(FileModel):
    """One EQ band; ``freq`` in Hz and ``gain`` in dB, named as in the original files."""

    band: int
    type: EqType
    freq: float
    gain: float
    q: float

    @classmethod
    def from_domain(cls, band: Band) -> "BandModel":
        """Build from a domain band."""
        return cls(band=band.band, type=band.type, freq=band.freq_hz, gain=band.gain_db, q=band.q)

    def to_domain(self) -> Band:
        """Convert to a domain band."""
        return Band(self.band, self.type, self.freq, self.gain, self.q)


class EqModel(FileModel):
    """A six-band EQ block."""

    on: bool
    bands: tuple[BandModel, ...]

    @classmethod
    def from_domain(cls, eq: SystemEq) -> "EqModel":
        """Build from a domain EQ block."""
        return cls(on=eq.on, bands=tuple(BandModel.from_domain(b) for b in eq.bands))

    def to_domain(self) -> SystemEq:
        """Convert to a domain EQ block."""
        return SystemEq(self.on, tuple(b.to_domain() for b in self.bands))


class CodedText(FileModel):
    """A coded message with its parameters (warning, reason)."""

    code: str
    params: dict[str, ParamValue] = {}


class SweepModel(FileModel):
    """Sweep range and length."""

    f1: float
    f2: float
    seconds: float


class SessionFile(FileModel):
    """``session.json``: what a measurement session is and under which EQ it was measured."""

    schema_version: Literal[1] = SCHEMA_VERSION
    name: str
    kind: Literal["baseline", "verify"]
    base: str | None = None
    created: str
    eq_at_measurement: EqModel | None
    fs: float
    sweep: SweepModel
    warnings: tuple[str, ...] = ()


class PointMeta(FileModel):
    """``meta.json`` of one measured point."""

    schema_version: Literal[1] = SCHEMA_VERSION
    point: str
    time: str
    level_dbfs: float
    avg: int
    backend: str
    peak_dbfs: float
    clipped_samples: int
    noise_dbfs: float
    sweep_rms_dbfs: float
    snr_db: float
    pnr_db: float
    latency_ms: float
    verdict: Literal["ok", "warn", "bad"]
    warnings: tuple[CodedText, ...] = ()
    mic_input: int | None = None
    repeat_db: float | None = None


class RolesModel(FileModel):
    """Matrices by speaker role."""

    sub: tuple[int, ...]
    mains: tuple[int, ...]
    others: tuple[int, ...] = ()


class JunctionState(FileModel):
    """Console values worth remembering with a junction measurement."""

    sub_fader: float | None  # dB; None when the fader is down


class JunctionMeta(FileModel):
    """``meta.json`` of one sub / top junction point."""

    schema_version: Literal[1] = SCHEMA_VERSION
    point: str
    time: str
    level_dbfs: float
    peaks_dbfs: tuple[float, float, float]
    xruns: tuple[str, ...] = ()
    roles: RolesModel
    state: JunctionState


WireNumber = StrictInt | Annotated[float, Field(strict=True, allow_inf_nan=False)]


class ChangeModel(FileModel):
    """One planned parameter write. Values must be real JSON numbers, finite, not strings.

    ``scope`` is set in plans that span several write scopes (applied packages, M5): the undo of
    each write is then validated in the scope it was written in.
    """

    path: str
    old_value: WireNumber
    new_value: WireNumber
    old_display: str
    new_display: str
    scope: WriteScope | None = None

    @classmethod
    def from_domain(cls, change: Change, scope: WriteScope | None = None) -> "ChangeModel":
        """Build from a domain change."""
        return cls(
            path=change.path,
            old_value=change.old_value,
            new_value=change.new_value,
            old_display=change.old_display,
            new_display=change.new_display,
            scope=scope,
        )

    def to_domain(self) -> Change:
        """Convert to a domain change."""
        return Change(self.path, self.old_value, self.new_value, self.old_display, self.new_display)


class ResultModel(FileModel):
    """Outcome of one write, as read back."""

    path: str
    wanted: int | float
    readback: int | float | str | None
    node: str | None
    ok: bool
    sent: bool = True
    error: str | None = None


class UndoFile(FileModel):
    """An undo file: the old values of one applied plan, written before the first write."""

    schema_version: Literal[1] = SCHEMA_VERSION
    created: str
    ip: str | None = None
    scope: WriteScope
    what: str | None = None
    proposal: str | None = None
    reverts: str | None = None
    eq_before: EqModel | None = None
    eq_after: EqModel | None = None
    changes: tuple[ChangeModel, ...]
    results: tuple[ResultModel, ...] = ()


class PendingMutes(FileModel):
    """Matrix switch states to restore after a junction measurement (True = on)."""

    schema_version: Literal[1] = SCHEMA_VERSION
    created: str
    mtx_on: dict[int, bool]

    @field_validator("mtx_on")
    @classmethod
    def _matrices_exist(cls, value: dict[int, bool]) -> dict[int, bool]:
        unknown = sorted(n for n in value if not 1 <= n <= MATRIX_COUNT)
        if unknown:
            raise ValueError(f"no such matrices: {unknown}")
        return value


class TargetModel(FileModel):
    """Target curve of a proposal."""

    tilt_db_per_oct: float
    tilt_from_hz: float
    bass_lift_db: float
    bass_from_hz: float = 100.0


class ProposalBand(BandModel):
    """One band of a proposal: the band, what it was, and why."""

    action: Literal["keep", "modify", "new"]
    was: BandModel
    approximate: bool
    reason: CodedText


class SkippedModel(FileModel):
    """A feature the proposal leaves alone."""

    kind: Literal["cut", "boost", "relax"]
    freq_hz: float
    dev_db: float
    width_oct: float
    points: int
    reason: CodedText


class PredictionModel(FileModel):
    """Modelled deviation from the target before and after."""

    rms_dev_before_db: float
    rms_dev_after_db: float
    max_dev_before_db: float
    max_dev_after_db: float
    range_hz: tuple[float, float]


class ProposalFile(FileModel):
    """``eq_proposal.json``."""

    schema_version: Literal[1] = SCHEMA_VERSION
    created: str
    session: str
    method: Literal["greedy", "optimize"]
    points: int
    target: TargetModel
    f_low_hz: float
    eq_on: bool
    bands: tuple[ProposalBand, ...]
    skipped: tuple[SkippedModel, ...] = ()
    warnings: tuple[str, ...] = ()
    prediction: PredictionModel
    eq_at_measurement: EqModel
    eq_current: EqModel | None = None


Finite = Annotated[float, Field(allow_inf_nan=False)]
PackageKindName = Literal["crossover", "alignment", "sub_level", "system_eq"]


class PathRefModel(FileModel):
    """A mix path that may carry a speaker: a matrix, a bus, Main LR or Mono, and its number.

    Input channels are not paths: the channel advice never becomes a change.
    """

    kind: Literal["matrix", "bus", "main_l", "mono"]
    number: Annotated[int, Field(ge=0, le=16)] = 0


class PathEqModel(FileModel):
    """The EQ block of one path, before and after a package."""

    path: PathRefModel
    before: EqModel
    after: EqModel


class OutputDelayModel(FileModel):
    """Delay of one main output, before and after, in ms."""

    output: Annotated[int, Field(ge=1, le=16)]
    before_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    after_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)]


class LevelModel(FileModel):
    """A fader, before and after, in dB (never above 0 dB in a package the program may write).

    ``before_db`` may be ``-inf`` (fader down); the apply step compares it with a fresh read
    within the console's fader step.
    """

    path: PathRefModel
    before_db: float
    after_db: Finite


class PolarityModel(FileModel):
    """Polarity of a path, before and after (True = inverted)."""

    path: PathRefModel
    before: bool
    after: bool


class EffectModel(FileModel):
    """Modelled effect of a package."""

    metric: str
    before_db: Finite
    after_db: Finite


class PackageModel(FileModel):
    """One change package and whether the person chose it.

    The file is not trusted by the apply step (M5): every value is validated again against the
    write scopes, re-read from the console and refused when ``before`` no longer matches, and a
    package is applied only together with every package it ``requires``. ``suggest_only``
    packages are never written.
    """

    kind: PackageKindName
    suggest_only: bool
    risk: Literal["low", "medium", "unverified"]
    selected: bool
    requires: tuple[PackageKindName, ...] = ()
    eq: tuple[PathEqModel, ...] = ()
    delays: tuple[OutputDelayModel, ...] = ()
    delay_side: Literal["sub", "tops"] | None = None
    delay_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0
    levels: tuple[LevelModel, ...] = ()
    polarities: tuple[PolarityModel, ...] = ()
    notes: tuple[CodedText, ...] = ()
    effect: EffectModel | None = None


class AuditModel(FileModel):
    """One piece of channel advice."""

    code: str
    channels: tuple[int, ...]
    freq_hz: float | None = None
    gain_db: float | None = None


class PackagesFile(FileModel):
    """``proposal.json`` of a wizard session: the packages and the channel advice (SPEC S11).

    Nothing in it has been written to the console; applying it is a separate step (M5).
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    created: str
    session: str
    fc_hz: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    packages: tuple[PackageModel, ...]
    notes: tuple[CodedText, ...] = ()
    audit: tuple[AuditModel, ...] = ()


class PlanPointModel(FileModel):
    """One planned measurement position."""

    id: str
    x_m: float
    y_m: float
    row: Literal["front", "middle", "back"]
    side: Literal["left", "centre", "right"]
    junction: bool = False


class PointPlanFile(FileModel):
    """``venues/<slug>/plan.json``: the point plan, reused by verify (SPEC §8.3)."""

    schema_version: Literal[1] = SCHEMA_VERSION
    width_m: float
    depth_m: float
    from_template: bool
    points: tuple[PlanPointModel, ...]


class SnapshotFile(FileModel):
    """``console/<ts>-snapshot.json``: a full read-only snapshot of the console (SPEC §6.4).

    Only the identity and the raw reply lines are stored; the parsed snapshot is rebuilt by the
    console driver, so a newer version can parse an old file better than the old one did.
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    taken_at: str
    family: ConsoleFamily
    model: str
    name: str
    firmware: str
    host: str
    raw: dict[str, str]

    @classmethod
    def from_snapshot(cls, snapshot: ConsoleSnapshot) -> "SnapshotFile":
        """The stored form of a snapshot: identity and raw lines."""
        identity = snapshot.identity
        return cls(
            taken_at=snapshot.taken_at,
            family=identity.family,
            model=identity.model,
            name=identity.name,
            firmware=identity.firmware,
            host=identity.host,
            raw=dict(snapshot.raw),
        )

    def identity(self) -> ConsoleIdentity:
        """The console identity stored in the file."""
        return ConsoleIdentity(self.family, self.model, self.name, self.firmware, self.host)


class MicCalibrationModel(FileModel):
    """One microphone of a calibrated set."""

    usb_input: int
    model: str
    sensitivity_db: float
    delay_ms: float
    inverted: bool
    shape_db: tuple[float, ...]
    problems: tuple[str, ...] = ()


class MicCalibrationFile(FileModel):
    """``mics/<set-id>/calibration.json``: relative calibration of a microphone set (SPEC S9).

    Reused without asking for ``MIC_CALIBRATION_MAX_AGE_DAYS`` while the mapping is the same.
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    set_id: str
    created: str
    centres_hz: tuple[float, ...]
    mics: tuple[MicCalibrationModel, ...]


Goal = Literal["full", "verify", "sub", "report"]


class HallSizeModel(FileModel):
    """Hall dimensions for the point plan (all optional knowledge)."""

    width_m: float
    depth_m: float
    stage_to_first_row_m: float = 2.0


MAX_WIZARD_MICS = 8


class WizardStateFile(FileModel):
    """``venues/<slug>/sessions/<ts>-<kind>/session.json`` of the wizard (SPEC §6.2, §6.4).

    Saved after every step; ``ctune resume`` continues after the last completed step.
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    venue: str
    venue_name: str
    lang: str
    goal: Goal = "full"
    created: str
    completed: tuple[str, ...] = ()
    console_host: str | None = None
    console_port: int | None = None
    console_model: str | None = None
    snapshot_file: str | None = None
    roles: dict[str, str] = {}
    variant: str | None = None
    speakers_active: bool | None = None
    hall: HallSizeModel | None = None
    mic_count: Annotated[int, Field(ge=0, le=MAX_WIZARD_MICS)] = 1
    mic_model: str = "ECM8000"
    mic_inputs: tuple[Annotated[int, Field(gt=0)], ...] = ()
    calibration_set: str | None = None
    # Never above the hard output limit: a hand-edited file must not make anything louder.
    sweep_level_dbfs: Annotated[float, Field(le=HARD_LIMIT_DBFS, allow_inf_nan=False)] | None = None
    points: tuple[str, ...] = ()
    measured: dict[str, str] = {}
    junction_points: tuple[str, ...] = ()
    proposal_file: str | None = None
    # Undo file of the packages written in S12 (relative to the home directory).
    applied_undo: str | None = None
    # S13: the points measured again, those done so far, and the verdict (SPEC §8.6).
    verify_points: tuple[str, ...] = ()
    verify_measured: dict[str, str] = {}
    verify_verdict: Literal["better", "no_change", "worse"] | None = None
