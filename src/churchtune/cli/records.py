"""Conversions between the stored file models and the objects the other layers work with."""

from collections.abc import Mapping, Sequence
from typing import Protocol

from churchtune.analysis.points import Analysis, PointResponse, analyze_points
from churchtune.analysis.response import calibration_on_grid
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
from churchtune.domain.changes import Change
from churchtune.measure.point import PointMeasurement
from churchtune.report.markdown import PointRow
from churchtune.report.texts import design_warning_text, quality_warning_text
from churchtune.storage import files
from churchtune.storage.models import (
    BandModel,
    ChangeModel,
    CodedText,
    EqModel,
    PointMeta,
    PredictionModel,
    ProposalBand,
    ProposalFile,
    ResultModel,
    SkippedModel,
    TargetModel,
)
from churchtune.storage.paths import Workspace

_SKIP_CODES = {code.value for code in SkipCode}
_REASON_CODES = {code.value for code in ReasonCode}
_WARNING_CODES = {code.value for code in DesignWarning}


def point_meta(point: str, time: str, backend: str, m: PointMeasurement) -> PointMeta:
    """The stored quality record of a measured point."""
    q = m.quality
    return PointMeta(
        point=point,
        time=time,
        level_dbfs=m.level_dbfs,
        avg=m.n_avg,
        backend=backend,
        peak_dbfs=q.peak_dbfs,
        clipped_samples=q.clipped_samples,
        noise_dbfs=q.noise_dbfs,
        sweep_rms_dbfs=q.sweep_rms_dbfs,
        snr_db=q.snr_db,
        pnr_db=q.pnr_db,
        latency_ms=q.latency_ms,
        verdict=q.verdict.value,
        warnings=tuple(CodedText(code=w.code.value, params=dict(w.params)) for w in q.warnings),
    )


def warning_texts(meta: PointMeta) -> tuple[str, ...]:
    """The warnings of a point as sentences."""
    return tuple(quality_warning_text(w.code, w.params) for w in meta.warnings)


def point_rows(points: Mapping[str, files.StoredPoint]) -> dict[str, PointRow]:
    """Report rows of the stored points; a point without meta gets an empty row."""
    rows = {}
    for name, point in points.items():
        meta = point.meta
        rows[name] = (
            PointRow()
            if meta is None
            else PointRow(
                meta.peak_dbfs,
                meta.noise_dbfs,
                meta.snr_db,
                meta.pnr_db,
                meta.latency_ms,
                meta.verdict,
                warning_texts(meta),
            )
        )
    return rows


def analyse_stored(workspace: Workspace, points: Mapping[str, files.StoredPoint]) -> Analysis:
    """Analyse stored points, with the microphone calibration file if there is one."""
    cal = files.load_mic_cal(workspace.mic_cal_file)
    cal_db = calibration_on_grid(*cal) if cal is not None else None
    return analyze_points({n: PointResponse(p.ir, p.fs_hz) for n, p in points.items()}, cal_db)


def _coded(reason: Reason) -> CodedText:
    return CodedText(code=reason.code.value, params=dict(reason.params))


def proposal_file(proposal: Proposal, session: str, created: str) -> ProposalFile:
    """The stored form of a proposal."""
    target = proposal.target
    return ProposalFile(
        created=created,
        session=session,
        method=proposal.method.value,
        points=proposal.n_points,
        target=TargetModel(
            tilt_db_per_oct=target.tilt_db_per_oct,
            tilt_from_hz=target.tilt_from_hz,
            bass_lift_db=target.bass_lift_db,
            bass_from_hz=target.bass_from_hz,
        ),
        f_low_hz=proposal.f_low_hz,
        eq_on=proposal.eq_on,
        bands=tuple(
            ProposalBand(
                **BandModel.from_domain(b.band).model_dump(),
                action=b.action.value,
                was=BandModel.from_domain(b.was),
                approximate=b.approximate,
                reason=_coded(b.reason),
            )
            for b in proposal.bands
        ),
        skipped=tuple(
            SkippedModel(
                kind=s.kind.value,
                freq_hz=s.freq_hz,
                dev_db=s.dev_db,
                width_oct=s.width_oct,
                points=s.points,
                reason=_coded(s.reason),
            )
            for s in proposal.skipped
        ),
        warnings=tuple(w.value for w in proposal.warnings),
        prediction=PredictionModel(
            rms_dev_before_db=proposal.prediction.rms_dev_before_db,
            rms_dev_after_db=proposal.prediction.rms_dev_after_db,
            max_dev_before_db=proposal.prediction.max_dev_before_db,
            max_dev_after_db=proposal.prediction.max_dev_after_db,
            range_hz=proposal.prediction.range_hz,
        ),
        eq_at_measurement=EqModel.from_domain(proposal.eq_at_measurement),
        eq_current=EqModel.from_domain(proposal.eq_current) if proposal.eq_current else None,
    )


def _band_reason(coded: CodedText) -> Reason:
    if coded.code in _REASON_CODES:
        return Reason(ReasonCode(coded.code), coded.params)
    return Reason(ReasonCode.LEGACY, {"text": str(coded.params.get("text", coded.code))})


def _skip_reason(coded: CodedText) -> Reason:
    if coded.code in _SKIP_CODES:
        return Reason(SkipCode(coded.code), coded.params)
    # A file of the original tool: the text is kept under a neutral code.
    return Reason(SkipCode.NOT_COMMON, {"text": str(coded.params.get("text", coded.code))})


def proposal_from_file(stored: ProposalFile) -> tuple[Proposal, tuple[str, ...]]:
    """A stored proposal as a design proposal.

    Returns:
        The proposal, and the warnings as sentences (files of the original tool hold sentences
        instead of codes, so the texts are returned separately).
    """
    coded_warnings = tuple(DesignWarning(w) for w in stored.warnings if w in _WARNING_CODES)
    texts = tuple(
        design_warning_text(DesignWarning(w)) if w in _WARNING_CODES else w for w in stored.warnings
    )
    target = stored.target
    proposal = Proposal(
        method=Method(stored.method),
        n_points=stored.points,
        target=TargetSpec(
            target.tilt_db_per_oct, target.tilt_from_hz, target.bass_lift_db, target.bass_from_hz
        ),
        f_low_hz=stored.f_low_hz,
        eq_on=stored.eq_on,
        bands=tuple(
            BandProposal(
                band=BandModel(
                    band=b.band, type=b.type, freq=b.freq, gain=b.gain, q=b.q
                ).to_domain(),
                was=b.was.to_domain(),
                action=Action(b.action),
                approximate=b.approximate,
                reason=_band_reason(b.reason),
            )
            for b in sorted(stored.bands, key=lambda b: b.band)
        ),
        skipped=tuple(
            Skipped(
                CandidateKind(s.kind),
                s.freq_hz,
                s.dev_db,
                s.width_oct,
                s.points,
                _skip_reason(s.reason),
            )
            for s in stored.skipped
        ),
        warnings=coded_warnings,
        prediction=Prediction(
            stored.prediction.rms_dev_before_db,
            stored.prediction.rms_dev_after_db,
            stored.prediction.max_dev_before_db,
            stored.prediction.max_dev_after_db,
            stored.prediction.range_hz,
        ),
        eq_at_measurement=stored.eq_at_measurement.to_domain(),
        eq_current=stored.eq_current.to_domain() if stored.eq_current else None,
    )
    return proposal, texts


def change_models(changes: Sequence[Change]) -> tuple[ChangeModel, ...]:
    """Planned writes in their stored form."""
    return tuple(ChangeModel.from_domain(c) for c in changes)


class WriteResultLike(Protocol):
    """The fields of a write result (the writer module itself is imported lazily elsewhere)."""

    @property
    def path(self) -> str: ...  # noqa: D102
    @property
    def wanted(self) -> int | float: ...  # noqa: D102
    @property
    def readback(self) -> int | float | str | bytes | None: ...  # noqa: D102
    @property
    def node(self) -> str | None: ...  # noqa: D102
    @property
    def ok(self) -> bool: ...  # noqa: D102
    @property
    def sent(self) -> bool: ...  # noqa: D102
    @property
    def error(self) -> str | None: ...  # noqa: D102


def result_models(results: Sequence[WriteResultLike]) -> tuple[ResultModel, ...]:
    """Write results in their stored form (a blob answer is stored as text)."""
    return tuple(
        ResultModel(
            path=r.path,
            wanted=r.wanted,
            readback=r.readback.hex() if isinstance(r.readback, bytes) else r.readback,
            node=r.node,
            ok=r.ok,
            sent=r.sent,
            error=r.error,
        )
        for r in results
    )
