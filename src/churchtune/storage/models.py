"""Schemas of the files ChurchTune writes. Every file carries ``schema_version``.

The shapes follow the files of the original single-hall tool (version 0, no version field);
``migrations`` lifts those to version 1 on reading.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from churchtune.domain.changes import Change, WriteScope
from churchtune.domain.eq import Band, EqType, SystemEq

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
    """One planned parameter write. Values must be real JSON numbers, finite, not strings."""

    path: str
    old_value: WireNumber
    new_value: WireNumber
    old_display: str
    new_display: str

    @classmethod
    def from_domain(cls, change: Change) -> "ChangeModel":
        """Build from a domain change."""
        return cls(
            path=change.path,
            old_value=change.old_value,
            new_value=change.new_value,
            old_display=change.old_display,
            new_display=change.new_display,
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


class SnapshotFile(FileModel):
    """``console/<ts>-snapshot.json``: a full read-only snapshot of the console (SPEC §6.4).

    Only the identity and the raw reply lines are stored; the parsed snapshot is rebuilt by the
    console driver, so a newer version can parse an old file better than the old one did.
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    taken_at: str
    family: str
    model: str
    name: str
    firmware: str
    host: str
    raw: dict[str, str]
