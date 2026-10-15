"""Change packages per topology: where things go, suggestion mode, order, and the junction."""

from dataclasses import replace

import numpy as np
import pytest

from churchtune.analysis.points import PointResponse, analyze_points
from churchtune.audio.fake.defects import Defects, defect_hall
from churchtune.audio.fake.room import synthetic_room
from churchtune.design.alignment import JunctionPoint
from churchtune.design.packages import (
    DesignContext,
    EffectMetric,
    PackageKind,
    Risk,
    build_packages,
    choose_fc,
    eq_paths,
    system_eq_effect,
)
from churchtune.design.proposal import Prediction
from churchtune.domain.eq import Band, EqType
from churchtune.domain.snapshot import SignalRef, SourceKind
from churchtune.mixers.x32.params import X32EqGrid
from churchtune.mixers.x32.topology import infer_topology
from tests.fixtures.topologies import TOPOLOGIES, load

FS = 48000.0
GRID = X32EqGrid()
MAIN = SignalRef(SourceKind.MAIN_L)


def flat(eq):
    return tuple(
        Band(b.band, EqType.PEQ, b.freq_hz, 0.0, b.q) if not b.adjustable else replace(b, gain_db=0)
        for b in eq
    )


def flat_snapshot(name):
    real = load(TOPOLOGIES / f"{name}.toml").snapshot()
    matrices = {n: replace(m, eq=flat(m.eq), eq_on=False) for n, m in real.matrices.items()}
    buses = {n: replace(b, eq=flat(b.eq), eq_on=False) for n, b in real.buses.items()}
    main = replace(real.main, eq=flat(real.main.eq))
    return replace(real, main=main, matrices=matrices, buses=buses)


@pytest.fixture(scope="module")
def hall():
    return defect_hall(FS)


@pytest.fixture(scope="module")
def analysis(hall):
    return analyze_points({p: PointResponse(h.both, FS) for p, h in hall.items()})


def junction_of(hall):
    return {p: JunctionPoint(sub=h.sub, tops=h.tops, both=h.both) for p, h in hall.items()}


def context(name, analysis, junction=None):
    snapshot = flat_snapshot(name)
    return DesignContext(analysis, snapshot, infer_topology(snapshot), GRID, FS, junction)


def test_without_a_junction_measurement_no_crossover_is_set_blind(analysis):
    packages = build_packages(context("hall-2026-10-02", analysis))
    assert [n.code for n in packages.notes] == ["junction-not-measured"]
    assert packages.of(PackageKind.CROSSOVER) is None
    assert packages.of(PackageKind.ALIGNMENT) is None
    eq = packages.of(PackageKind.SYSTEM_EQ)
    assert eq is not None
    assert [p.path for p in eq.eq] == [MAIN]
    assert eq.requires == ()


def test_with_a_junction_the_crossover_is_on_the_matrices(hall, analysis):
    packages = build_packages(context("hall-2026-10-02", analysis, junction_of(hall)))
    crossover = packages.of(PackageKind.CROSSOVER)
    assert crossover is not None
    assert not crossover.suggest_only
    assert {p.path for p in crossover.eq} == {SignalRef(SourceKind.MATRIX, n) for n in (1, 2, 3)}
    sub = next(p for p in crossover.eq if p.path.number == 3).after
    assert sub.band(6).type is EqType.LR24


def test_tops_on_main_give_a_crossover_suggestion_only(hall, analysis):
    packages = build_packages(context("tops-on-main", analysis, junction_of(hall)))
    crossover = packages.of(PackageKind.CROSSOVER)
    assert crossover is not None
    assert crossover.suggest_only
    assert crossover.eq == ()
    assert crossover.notes[0].code == "crossover-tops-on-main"


def test_a_sub_on_a_bus_keeps_the_eq_above_the_crossover(hall, analysis):
    ctx = context("sub-on-bus", analysis, junction_of(hall))
    packages = build_packages(ctx)
    crossover = packages.of(PackageKind.CROSSOVER)
    assert crossover is not None
    assert crossover.suggest_only
    assert crossover.notes[0].code == "crossover-sub-not-on-matrix"
    paths, floor = eq_paths(ctx, packages.fc_hz)
    assert all(p.kind is not SourceKind.BUS for p in paths)
    assert floor >= packages.fc_hz


def test_without_a_sub_there_is_no_crossover(analysis):
    packages = build_packages(context("no-sub", analysis))
    assert packages.of(PackageKind.CROSSOVER) is None
    assert packages.notes == ()


def test_packages_come_in_the_order_of_application(hall, analysis):
    packages = build_packages(context("hall-2026-10-02", analysis, junction_of(hall)))
    order = [p.kind for p in packages.packages]
    assert order == sorted(order, key=list(PackageKind).index)


def test_a_late_sub_makes_the_tops_wait_in_suggestion_mode():
    late = defect_hall(FS, Defects(sub_extra_ms=9.0, hump=None, mode=None, null=None))
    analysis = analyze_points({p: PointResponse(h.both, FS) for p, h in late.items()})
    packages = build_packages(context("hall-2026-10-02", analysis, junction_of(late)))
    alignment = packages.of(PackageKind.ALIGNMENT)
    assert alignment is not None
    assert alignment.suggest_only  # output delay writes are unverified on hardware (ADR 0005)
    assert alignment.risk is Risk.UNVERIFIED
    assert alignment.notes[0].code == "alignment-delay-tops"
    snapshot = flat_snapshot("hall-2026-10-02")
    sub_outputs = {o.number for o in snapshot.outputs_fed_by(SignalRef(SourceKind.MATRIX, 3))}
    assert alignment.delays
    assert all(d.output not in sub_outputs for d in alignment.delays)
    assert all(d.after_ms > d.before_ms for d in alignment.delays)
    assert alignment.effect is not None
    assert alignment.effect.after_db > alignment.effect.before_db


def test_a_sub_level_above_0_db_is_only_suggested(hall, analysis):
    ctx = context("hall-2026-10-02", analysis, junction_of(hall))
    quiet = {
        p: replace(j, sub=j.sub * 0.05, both=j.tops + j.sub * 0.05) for p, j in ctx.junction.items()
    }
    sub = ctx.snapshot.matrices[3]
    loud_fader = replace(
        ctx.snapshot, matrices={**ctx.snapshot.matrices, 3: replace(sub, fader_db=-2.0)}
    )
    packages = build_packages(replace(ctx, snapshot=loud_fader, junction=quiet))
    level = packages.of(PackageKind.SUB_LEVEL)
    assert level is not None
    assert level.levels
    change = level.levels[0]
    assert change.after_db > 0.0
    assert level.suggest_only
    assert change.after_db - change.before_db <= 10.0


def test_choose_fc_is_100_without_measurements_and_in_range_with_them(hall):
    assert choose_fc(None, FS) == 100.0
    assert 80.0 <= choose_fc(junction_of(hall), FS) <= 120.0


def test_the_eq_never_moves_a_crossover_filter(hall, analysis):
    packages = build_packages(context("hall-2026-10-02", analysis, junction_of(hall)))
    eq = packages.of(PackageKind.SYSTEM_EQ)
    assert eq is not None
    for path in eq.eq:
        for old, new in zip(path.before.bands, path.after.bands, strict=True):
            if not old.adjustable:
                assert new == old
    assert np.isfinite(eq.effect.after_db) if eq.effect else True


def test_an_inverted_sub_is_flipped_back():
    points = {}
    for i in range(4):
        room = synthetic_room(np.random.default_rng(70 + i), FS, length_s=1.0, drr_db=14.0)
        points[f"p{i + 1}"] = JunctionPoint(sub=-room, tops=room, both=np.zeros_like(room))
    analysis = analyze_points({p: PointResponse(j.tops, FS) for p, j in points.items()})
    packages = build_packages(context("hall-2026-10-02", analysis, points))
    alignment = packages.of(PackageKind.ALIGNMENT)
    assert alignment is not None
    assert alignment.polarities
    flip = alignment.polarities[0]
    assert flip.after is not flip.before
    assert "alignment-polarity" in [n.code for n in alignment.notes]


def test_leaving_the_crossover_out_redesigns_the_rest(hall, analysis):
    ctx = context("hall-2026-10-02", analysis, junction_of(hall))
    full = build_packages(ctx)
    without = build_packages(ctx, {PackageKind.CROSSOVER})
    assert without.of(PackageKind.CROSSOVER) is None
    for package in without.packages:
        assert PackageKind.CROSSOVER not in package.requires
    eq = without.of(PackageKind.SYSTEM_EQ)
    if eq is not None:
        for path in eq.eq:
            assert all(b.type is not EqType.LR24 for b in path.after.bands)
    assert full.of(PackageKind.CROSSOVER) is not None


def test_a_matrix_that_is_sub_and_tops_gets_no_crossover(hall, analysis):
    snapshot = flat_snapshot("hall-2026-10-02")
    guess = infer_topology(snapshot)
    mtx1 = SignalRef(SourceKind.MATRIX, 1)
    shared = replace(guess, subs=(mtx1,))  # matrix 1 feeds a top and the sub
    ctx = DesignContext(analysis, snapshot, shared, GRID, FS, junction_of(hall))
    crossover = build_packages(ctx).of(PackageKind.CROSSOVER)
    assert crossover is not None
    assert crossover.suggest_only
    assert crossover.eq == ()
    assert crossover.notes[0].code == "crossover-shared-path"


def test_package_tables_and_effects_for_delays_faders_and_polarity():
    from churchtune.design.packages import (  # noqa: PLC0415 - the screen helpers under test
        Effect,
        EffectMetric,
        LevelChange,
        OutputDelayChange,
        Package,
        PolarityChange,
    )
    from churchtune.wizard.steps.proposal import _rows, effect_text  # noqa: PLC0415

    sub = SignalRef(SourceKind.MATRIX, 3)
    package = Package(
        PackageKind.ALIGNMENT,
        True,
        Risk.UNVERIFIED,
        delays=(OutputDelayChange(8, 0.0, 6.1),),
        levels=(LevelChange(sub, -20.0, -26.0),),
        polarities=(PolarityChange(sub, False, True),),
        effect=Effect(EffectMetric.SUB_MINUS_TOPS, 6.0, 0.0),
    )
    keys = [cell.key for row in _rows(package) for cell in row]
    assert keys[:3] == ["delay-of", "value-ms", "value-ms"]
    assert "fader-of" in keys
    assert keys[-3:] == ["polarity-of", "polarity-normal", "polarity-inverted"]
    louder = effect_text(package)
    assert louder is not None
    assert louder.key == "effect-sub-louder"
    quieter = effect_text(replace(package, effect=Effect(EffectMetric.SUB_MINUS_TOPS, -3.0, 0.0)))
    assert quieter is not None
    assert quieter.key == "effect-sub-quieter"
    assert quieter.args["db"] == 3.0
    gone = effect_text(replace(package, effect=Effect(EffectMetric.MAX_EXCESS, 2.1, -0.3)))
    assert gone is not None
    assert gone.key == "effect-max-excess"
    assert gone.args == {"before_db": 2.1, "after_db": 0.0}  # a hump never sticks out by -0.3 dB


def test_a_prediction_needs_junction_points_from_the_analysis(hall, analysis):
    from churchtune.design.packages import predicted_analysis  # noqa: PLC0415

    junction = {f"other{i}": j for i, j in enumerate(junction_of(hall).values())}
    with pytest.raises(ValueError, match="not points of the analysis"):
        predicted_analysis(analysis, junction, {k: j.both for k, j in junction.items()}, FS)


@pytest.mark.parametrize(
    ("rms", "peak", "metric"),
    [
        ((1.96, 1.76), (4.72, 4.0), EffectMetric.RMS_DEVIATION),  # exactly 0.2 dB, not 0.1999
        ((1.96, 1.77), (4.72, 4.0), None),
        ((1.96, 1.80), (4.72, 1.68), EffectMetric.MAX_EXCESS),  # a hump fixed, the RMS moves little
        ((1.96, 1.97), (4.72, 1.68), None),  # never when the overall deviation gets worse
        ((1.96, 1.96), (4.72, 3.73), None),
    ],
)
def test_a_system_eq_is_proposed_for_its_rms_or_its_peak(rms, peak, metric):
    prediction = Prediction(rms[0], rms[1], peak[0], peak[1], (35.0, 10000.0))
    effect = system_eq_effect(prediction)
    if metric is None:
        assert effect is None
    else:
        assert effect is not None
        assert effect.metric == metric
        expected = rms if metric == EffectMetric.RMS_DEVIATION else peak
        assert (effect.before_db, effect.after_db) == expected
