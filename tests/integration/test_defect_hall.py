"""M4 acceptance (SPEC §15): on a hall with known defects the packages fix the junction dip, the
500 Hz hump and the 50 Hz mode, and leave a narrow positional null alone.

The simulator gives the speakers' own responses. "Measuring" passes them through the EQ the
console has; "applying" a proposal replaces that EQ by the proposed one (it is never layered on
top), adds the delays, polarity and level, and the hall is listened to again.
"""

from dataclasses import replace

import numpy as np
import pytest
from scipy import signal

from churchtune.analysis.points import PointResponse, analyze_points
from churchtune.analysis.response import GRID_HZ
from churchtune.audio.fake.defects import Defects, PointIrs, defect_hall, shifted
from churchtune.design.alignment import JunctionPoint, analyze_alignment
from churchtune.design.packages import (
    DesignContext,
    PackageKind,
    Packages,
    build_packages,
)
from churchtune.design.target import target_curve
from churchtune.domain.console_paths import strip_eq
from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.domain.eq_model import band_sos
from churchtune.domain.snapshot import ConsoleSnapshot, SignalRef, SourceKind
from churchtune.mixers.x32.params import X32EqGrid
from churchtune.mixers.x32.topology import infer_topology
from tests.fixtures.topologies import TOPOLOGIES, load

FS = 48000.0
GRID = X32EqGrid()
MAIN = SignalRef(SourceKind.MAIN_L)
SUB = SignalRef(SourceKind.MATRIX, 3)
TOPS = SignalRef(SourceKind.MATRIX, 1)
TARGET = target_curve(GRID_HZ, -1.0, 1000.0, 3.0, 100.0)


def flat(eq: tuple[Band, ...]) -> tuple[Band, ...]:
    """The same bands with no effect (cut filters become 0 dB peaking bands)."""
    return tuple(
        Band(b.band, EqType.PEQ, b.freq_hz, 0.0, b.q) if not b.adjustable else replace(b, gain_db=0)
        for b in eq
    )


REAL = load(TOPOLOGIES / "hall-2026-10-02.toml").snapshot()


def flat_snapshot() -> ConsoleSnapshot:
    """The reference hall with every speaker path EQ flat: no crossover, no system EQ."""
    matrices = {
        n: replace(m, eq=flat(m.eq), eq_on=False, invert=False) for n, m in REAL.matrices.items()
    }
    return replace(REAL, main=replace(REAL.main, eq=flat(REAL.main.eq)), matrices=matrices)


def through(ir: np.ndarray, eq: SystemEq) -> np.ndarray:
    if not eq.on:
        return ir
    for band in eq.bands:
        sos = band_sos(band, FS)
        if sos is not None:
            ir = signal.sosfilt(sos, ir)
    return np.asarray(ir)


def console_eqs(snapshot: ConsoleSnapshot) -> dict[SignalRef, SystemEq]:
    strips = {MAIN: snapshot.main, SUB: snapshot.matrices[3], TOPS: snapshot.matrices[1]}
    return {ref: strip_eq(strip) for ref, strip in strips.items()}


def measured(natural: dict[str, PointIrs], eqs: dict[SignalRef, SystemEq]) -> dict[str, PointIrs]:
    """What the microphones hear through the console's EQ."""
    return {
        name: PointIrs(
            tops=through(through(h.tops, eqs[TOPS]), eqs[MAIN]),
            sub=through(through(h.sub, eqs[SUB]), eqs[MAIN]),
        )
        for name, h in natural.items()
    }


def design(snapshot: ConsoleSnapshot, hall: dict[str, PointIrs]) -> Packages:
    analysis = analyze_points({p: PointResponse(h.both, FS) for p, h in hall.items()})
    junction = {p: JunctionPoint(sub=h.sub, tops=h.tops, both=h.both) for p, h in hall.items()}
    return build_packages(
        DesignContext(analysis, snapshot, infer_topology(snapshot), GRID, FS, junction)
    )


def listen(
    natural: dict[str, PointIrs], packages: Packages, snapshot: ConsoleSnapshot
) -> dict[str, PointIrs]:
    """The hall after every package (suggestions included: the person sets those by hand)."""
    eqs = console_eqs(snapshot)
    for package in packages.packages:
        for path in package.eq:
            if path.path in eqs:
                eqs[path.path] = path.after
    sub_gain, sub_sign, sub_ms, tops_ms = 1.0, 1.0, 0.0, 0.0
    for package in packages.packages:
        if package.levels:
            sub_gain = 10 ** ((package.levels[0].after_db - package.levels[0].before_db) / 20)
        if package.polarities:
            sub_sign = -1.0
        if package.delay_side == "sub":
            sub_ms = package.delay_ms
        elif package.delay_side == "tops":
            tops_ms = package.delay_ms
    heard = measured(natural, eqs)
    return {
        name: PointIrs(
            tops=shifted(h.tops, tops_ms * 1e-3 * FS),
            sub=shifted(h.sub, sub_ms * 1e-3 * FS) * sub_gain * sub_sign,
        )
        for name, h in heard.items()
    }


def over_target(hall: dict[str, PointIrs], freq_hz: float) -> float:
    """How far the averaged response sits from the house curve at a frequency, dB."""
    analysis = analyze_points({p: PointResponse(h.both, FS) for p, h in hall.items()})
    i = int(np.argmin(np.abs(GRID_HZ - freq_hz)))
    return float(analysis.avg_db[i] - TARGET[i])


def zone_deviation(hall: dict[str, PointIrs], fc_hz: float) -> float:
    """Mean absolute deviation from the house curve in fc/2..2fc: a crossover hole shows here."""
    analysis = analyze_points({p: PointResponse(h.both, FS) for p, h in hall.items()})
    zone = (fc_hz / 2 <= GRID_HZ) & (fc_hz * 2 >= GRID_HZ)
    return float(np.mean(np.abs(analysis.avg_db[zone] - TARGET[zone])))


def cancellation(hall: dict[str, PointIrs], fc_hz: float) -> float:
    """Mean over points of the deepest cancellation in fc/2..2fc, dB.

    The sum against the power sum of sub and tops: 0 dB for unrelated sources, +3 dB for a
    perfect coherent sum, deeply negative where they cancel. Room dips are common to both and
    drop out, so this is the junction alone.
    """
    points = {p: JunctionPoint(sub=h.sub, tops=h.tops, both=h.both) for p, h in hall.items()}
    result = analyze_alignment(points, fc_hz, FS)
    depths = []
    for pa in result.points.values():
        power = 10 * np.log10(10 ** (pa.sub_db / 10) + 10 ** (pa.tops_db / 10))
        depths.append(float(np.min(pa.sum_now_db - power)))
    return float(np.mean(depths))


def sub_minus_tops(hall: dict[str, PointIrs], fc_hz: float) -> float:
    points = {p: JunctionPoint(sub=h.sub, tops=h.tops, both=h.both) for p, h in hall.items()}
    return analyze_alignment(points, fc_hz, FS).sub_minus_tops_at_fc_db


@pytest.fixture(scope="module")
def natural() -> dict[str, PointIrs]:
    return defect_hall(FS)


@pytest.fixture(scope="module")
def flat_case(natural):
    snapshot = flat_snapshot()
    hall = measured(natural, console_eqs(snapshot))
    packages = design(snapshot, hall)
    return snapshot, hall, packages, listen(natural, packages, snapshot)


def test_every_package_is_proposed_in_order(flat_case):
    _snapshot, _hall, packages, _after = flat_case
    kinds = [p.kind for p in packages.packages]
    assert kinds == list(PackageKind)  # crossover, alignment, sub level, system EQ
    alignment = packages.of(PackageKind.ALIGNMENT)
    assert alignment is not None
    assert alignment.delay_ms > 0
    assert alignment.requires == (PackageKind.CROSSOVER,)


def test_the_junction_stops_cancelling_and_has_no_hole(flat_case):
    _snapshot, hall, packages, after = flat_case
    assert cancellation(hall, packages.fc_hz) < -6.0
    assert cancellation(after, packages.fc_hz) > -3.0
    assert zone_deviation(after, packages.fc_hz) < 3.0


def test_the_sub_level_ends_close_to_the_tops(flat_case):
    _snapshot, hall, packages, after = flat_case
    assert sub_minus_tops(hall, packages.fc_hz) > 3.0
    assert abs(sub_minus_tops(after, packages.fc_hz)) < 2.0


@pytest.mark.parametrize("freq_hz", [500.0, 50.0])
def test_the_hump_and_the_mode_end_close_to_the_target(flat_case, freq_hz):
    _snapshot, hall, _packages, after = flat_case
    before = over_target(hall, freq_hz)
    assert before > 3.0
    assert abs(over_target(after, freq_hz)) < 2.5
    assert abs(over_target(after, freq_hz)) < before - 2.0


def test_nothing_aims_at_the_positional_null(flat_case):
    _snapshot, _hall, packages, _after = flat_case
    for package in packages.packages:
        for path in package.eq:
            for old, new in zip(path.before.bands, path.after.bands, strict=True):
                if old != new and new.adjustable and abs(new.gain_db) > 0.5:
                    assert abs(np.log2(new.freq_hz / 800.0)) > 1 / 3, (package.kind, new)


def test_the_real_console_eq_is_replaced_not_layered():
    """H1: measured through the reference hall's own EQ, the prediction must not count that EQ
    twice: the level package stays small and the predicted junction matches what is heard."""
    natural_ok = defect_hall(FS, replace(Defects(), sub_gain_db=0.0))
    hall = measured(natural_ok, console_eqs(REAL))
    packages = design(REAL, hall)
    level = packages.of(PackageKind.SUB_LEVEL)
    if level is not None:
        change = level.levels[0].after_db - level.levels[0].before_db
        assert abs(change) <= 4.0
    after = listen(natural_ok, packages, REAL)
    assert abs(sub_minus_tops(after, packages.fc_hz)) < 3.0


def test_a_clean_hall_gets_no_eq_aimed_at_defects():
    clean = Defects(
        sub_extra_ms=None, sub_inverted=False, sub_gain_db=0.0, hump=None, mode=None, null=None
    )
    snapshot = flat_snapshot()
    packages = design(snapshot, measured(defect_hall(FS, clean), console_eqs(snapshot)))
    eq = packages.of(PackageKind.SYSTEM_EQ)
    if eq is not None:
        for path in eq.eq:
            for band in path.after.bands:
                if band.adjustable:
                    assert abs(band.gain_db) <= 3.0


@pytest.mark.parametrize(
    ("defects", "near_target_at_250_300"),
    [
        (replace(Defects(), sub_extra_ms=None, sub_inverted=False, sub_gain_db=0.0), True),
        (Defects(), False),  # a bad junction too: late, inverted and loud sub
    ],
    ids=["good-junction", "bad-junction"],
)
def test_without_a_junction_measurement_nothing_gets_worse(defects, near_target_at_250_300):
    """The M4 wizard path: no separate sub/tops measurement, the reference hall's own EQ. No
    crossover is set blind, and the system EQ makes no band of the main range worse."""
    natural_ok = defect_hall(FS, defects)
    hall = measured(natural_ok, console_eqs(REAL))
    analysis = analyze_points({p: PointResponse(h.both, FS) for p, h in hall.items()})
    packages = build_packages(DesignContext(analysis, REAL, infer_topology(REAL), GRID, FS))
    assert packages.of(PackageKind.CROSSOVER) is None
    assert [n.code for n in packages.notes] == ["junction-not-measured"]
    after = listen(natural_ok, packages, REAL)
    for freq_hz in (63, 80, 100, 125, 160, 250, 315, 500, 800, 1000, 2000):
        before_dev = abs(over_target(hall, freq_hz))
        assert abs(over_target(after, freq_hz)) <= before_dev + 1.5, freq_hz
    if near_target_at_250_300:
        for freq_hz in (250, 300):
            assert abs(over_target(after, freq_hz)) < 2.5
    assert over_target(after, 50.0) < over_target(hall, 50.0)


def eq_bands(packages: Packages) -> list[Band]:
    eq = packages.of(PackageKind.SYSTEM_EQ)
    assert eq is not None
    return [b for path in eq.eq for b in path.after.bands if b.adjustable and b.gain_db]


def test_floating_point_noise_does_not_change_the_proposal(flat_case):
    """The objective has near-equal minima: noise of 1e-7 dB (another platform's floating point)
    must still give the same system EQ, at most one console grid step apart."""
    snapshot, hall, packages, _after = flat_case
    analysis = analyze_points({p: PointResponse(h.both, FS) for p, h in hall.items()})
    junction = {p: JunctionPoint(sub=h.sub, tops=h.tops, both=h.both) for p, h in hall.items()}
    reference = sorted(eq_bands(packages), key=lambda b: b.freq_hz)
    for seed in range(8):
        rng = np.random.default_rng(seed)
        norm = {p: c + rng.normal(0.0, 1e-7, c.shape) for p, c in analysis.norm_db.items()}
        noisy = replace(analysis, norm_db=norm)
        context = DesignContext(noisy, snapshot, infer_topology(snapshot), GRID, FS, junction)
        bands = sorted(eq_bands(build_packages(context)), key=lambda b: b.freq_hz)
        assert len(bands) == len(reference), seed
        for band, ref in zip(bands, reference, strict=True):
            assert abs(np.log2(band.freq_hz / ref.freq_hz)) < 1 / 12, (seed, band, ref)
            assert abs(band.gain_db - ref.gain_db) <= 0.5, (seed, band, ref)
