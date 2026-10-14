"""Plans and proposals between the design models and the stored files (S10, S11)."""

from collections.abc import Sequence

from churchtune.design.channel_audit import Finding
from churchtune.design.packages import Note, Package, PathEq
from churchtune.domain.snapshot import SignalRef
from churchtune.measure.plan import PlanPoint, PointPlan
from churchtune.storage.models import (
    AuditModel,
    CodedText,
    EffectModel,
    EqModel,
    LevelModel,
    OutputDelayModel,
    PackageModel,
    PackagesFile,
    PathEqModel,
    PathRefModel,
    PlanPointModel,
    PointPlanFile,
    PolarityModel,
)


# ------------------------------------------------------------------ plans
def plan_file(plan: PointPlan) -> PointPlanFile:
    """The stored form of a point plan."""
    return PointPlanFile(
        width_m=plan.width_m,
        depth_m=plan.depth_m,
        from_template=plan.from_template,
        points=tuple(
            PlanPointModel(
                id=p.id, x_m=p.x_m, y_m=p.y_m, row=p.row, side=p.side, junction=p.junction
            )
            for p in plan.points
        ),
    )


def plan_from_file(stored: PointPlanFile) -> PointPlan:
    """A point plan from its stored form."""
    return PointPlan(
        stored.width_m,
        stored.depth_m,
        tuple(PlanPoint(p.id, p.x_m, p.y_m, p.row, p.side, p.junction) for p in stored.points),
        stored.from_template,
    )


# ------------------------------------------------------------------ packages
def _ref(ref: SignalRef) -> PathRefModel:
    return PathRefModel.model_validate({"kind": ref.kind.value, "number": ref.number})


def _note(note: Note) -> CodedText:
    return CodedText(code=note.code, params=dict(note.params or {}))


def _path_eq(path: PathEq) -> PathEqModel:
    return PathEqModel(
        path=_ref(path.path),
        before=EqModel.from_domain(path.before),
        after=EqModel.from_domain(path.after),
    )


def package_model(package: Package, selected: bool) -> PackageModel:
    """The stored form of one package."""
    return PackageModel(
        kind=package.kind.value,
        suggest_only=package.suggest_only,
        risk=package.risk.value,
        selected=selected,
        requires=tuple(k.value for k in package.requires),
        eq=tuple(_path_eq(p) for p in package.eq),
        delays=tuple(
            OutputDelayModel(output=d.output, before_ms=d.before_ms, after_ms=d.after_ms)
            for d in package.delays
        ),
        delay_side=package.delay_side,
        delay_ms=package.delay_ms,
        levels=tuple(
            LevelModel(path=_ref(lv.path), before_db=lv.before_db, after_db=lv.after_db)
            for lv in package.levels
        ),
        polarities=tuple(
            PolarityModel(path=_ref(pl.path), before=pl.before, after=pl.after)
            for pl in package.polarities
        ),
        notes=tuple(_note(n) for n in package.notes),
        effect=None
        if package.effect is None
        else EffectModel(
            metric=package.effect.metric,
            before_db=round(package.effect.before_db, 2),
            after_db=round(package.effect.after_db, 2),
        ),
    )


def packages_file(  # noqa: PLR0913 - one stored file brings these together
    fc_hz: float,
    decided: Sequence[tuple[Package, bool]],
    notes: Sequence[Note],
    audit: tuple[Finding, ...],
    *,
    session: str,
    created: str,
) -> PackagesFile:
    """The stored proposal of a session.

    Every package shown, marked chosen or not, the notes and the channel advice.
    """
    return PackagesFile(
        created=created,
        session=session,
        fc_hz=round(fc_hz, 1),
        packages=tuple(package_model(p, selected) for p, selected in decided),
        notes=tuple(_note(n) for n in notes),
        audit=tuple(
            AuditModel(code=f.code.value, channels=f.channels, freq_hz=f.freq_hz, gain_db=f.gain_db)
            for f in audit
        ),
    )
