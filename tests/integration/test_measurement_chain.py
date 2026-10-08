"""Synthetic measurement chain: sweep -> simulated room (known modes and RT60) -> deconvolution
-> analysis -> EQ proposal. The analysis must find what was put into the room.

Ported from the original tool's ``test_measurement.py``. The original stored each point on disk
and loaded it back; here the chain runs in memory (storage has its own tests).
"""

import math
from dataclasses import replace

import numpy as np
import pytest

from churchtune.analysis.points import PointResponse, analyze_points
from churchtune.analysis.response import GRID_HZ
from churchtune.analysis.rt60 import rt60_octaves
from churchtune.audio.fake.backend import FakeBackend
from churchtune.audio.fake.room import DEFAULT_RT60_S, demo_rooms, synthetic_room
from churchtune.design.proposal import Action, CandidateKind, ReasonCode, TargetSpec
from churchtune.design.system_eq import design
from churchtune.design.system_eq_opt import optimize_design
from churchtune.design.target import EqDesignConfig
from churchtune.domain.eq import EqType
from churchtune.domain.eq_model import eq_response_db
from churchtune.measure.config import QualityGates, SweepConfig
from churchtune.measure.deconvolution import deconvolve, extract_ir
from churchtune.measure.gates import Verdict, WarningCode
from churchtune.measure.point import measure_point
from churchtune.measure.stimulus import build_stimulus, make_sweep
from churchtune.mixers.x32 import params as P  # noqa: N812 - short alias kept from the original
from churchtune.mixers.x32.nodes import parse_eq_from_dump
from tests.fixtures.paths import DUMP_2026_09_29

FS_HZ = 48000.0
SWEEP = SweepConfig()
GATES = QualityGates()
CFG = EqDesignConfig()
GRID = P.X32EqGrid()
EQ0 = parse_eq_from_dump(DUMP_2026_09_29.read_text(encoding="utf-8"))
assert EQ0 is not None


@pytest.fixture(scope="module")
def rooms():
    return demo_rooms(FS_HZ)


def measure_session(rooms, eq=EQ0, **backend_options):
    backend = FakeBackend(rooms, FS_HZ, eq_provider=lambda: eq, **backend_options)
    results = {}
    for point in rooms:
        backend.set_point(point)
        results[point] = measure_point(backend, fs_hz=FS_HZ, sweep=SWEEP, gates=GATES)
    return results


def analyse(results):
    return analyze_points({name: PointResponse(m.ir, m.fs_hz) for name, m in results.items()})


@pytest.fixture(scope="module")
def analysed(rooms):
    results = measure_session(rooms)
    return analyse(results), results


def test_deconvolution_latency_and_rt60(rooms):
    backend = FakeBackend(rooms, FS_HZ, latency_samples=2000)
    stimulus = build_stimulus(make_sweep(FS_HZ, SWEEP), FS_HZ, SWEEP.preroll_s, SWEEP.tail_s)

    rec, _ = backend.playrec(stimulus)
    ir_full = deconvolve(
        rec.astype(np.float64), stimulus[:, 0].astype(np.float64), FS_HZ, SWEEP.f1_hz, SWEEP.f2_hz
    )
    ir, peak_idx = extract_ir(ir_full, FS_HZ, SWEEP.ir_length_s, SWEEP.ir_pre_ms)

    assert peak_idx == pytest.approx(2000 + int(0.012 * 48000), abs=3)  # latency + direct sound
    decays = rt60_octaves(ir, FS_HZ)
    for centre in (500, 1000, 2000, 4000):
        assert decays[centre].t20_s == pytest.approx(DEFAULT_RT60_S[centre], rel=0.12), centre
        assert decays[centre].t30_s == pytest.approx(DEFAULT_RT60_S[centre], rel=0.12), centre


def test_quality_ok(analysed):
    _, results = analysed
    for measurement in results.values():
        quality = measurement.quality
        assert quality.verdict is Verdict.OK, quality
        assert quality.snr_db > 40
        assert quality.pnr_db > 45


def test_analysis_finds_modes_and_rt60(analysed):
    an, _ = analysed
    grid, avg = an.grid_hz, an.avg_db
    for mode_hz in (63, 120):  # room modes common to all points
        near = (grid > mode_hz / 1.2) & (grid < mode_hz * 1.2)
        peak_hz = grid[near][np.argmax(avg[near])]
        assert abs(math.log2(peak_hz / mode_hz)) < 1 / 6, (mode_hz, peak_hz)
        assert avg[near].max() > 6
    for centre in (500, 1000, 2000):
        assert an.rt60[centre].t20.mean_s == pytest.approx(DEFAULT_RT60_S[centre], rel=0.12)
    notch = (grid > 750) & (grid < 850)  # the narrow common notch is visible
    assert avg[notch].min() < -5


def test_proposal_rules(analysed):
    an, _ = analysed
    proposal = design(an, EQ0, GRID, FS_HZ)
    bands = proposal.bands
    assert len(bands) == 6
    assert bands[0].band.type == EqType.BU18  # HPF locked
    assert bands[0].action is Action.KEEP
    assert bands[0].reason.code is ReasonCode.LOCKED_CUT_FILTER
    active = [b for b in bands if b.action is not Action.KEEP]
    assert active
    cuts = [b for b in active if b.band.gain_db < 0]
    for mode_hz in (63, 120):
        assert any(abs(math.log2(b.band.freq_hz / mode_hz)) < 0.25 for b in cuts), (mode_hz, active)
    for b in bands:
        assert b.band.gain_db <= CFG.max_boost_db + 1e-9
        assert b.band.gain_db >= -15
        if b.band.freq_hz > CFG.approx_above_hz and b.action is not Action.KEEP:
            assert b.approximate
            assert b.reason.params["approximate"] is True
    # the narrow notch at 800 Hz is never boosted
    assert not any(
        b.band.gain_db > 0 and abs(math.log2(b.band.freq_hz / 800)) < 0.3 for b in active
    )
    assert any(s.kind is CandidateKind.BOOST and 700 < s.freq_hz < 900 for s in proposal.skipped)
    # the local mode at 1250 Hz (only p3) is not cut
    assert not any(abs(math.log2(b.band.freq_hz / 1250)) < 0.2 for b in cuts)
    assert proposal.prediction.rms_dev_after_db < proposal.prediction.rms_dev_before_db
    # values are on the console grid
    for b in active:
        assert P.snap_freq_hz(b.band.freq_hz) == pytest.approx(b.band.freq_hz)
        assert P.snap_gain_db(b.band.gain_db) == pytest.approx(b.band.gain_db)


def test_existing_eq_is_accounted_for(rooms):
    """The same room measured with a different console EQ gives nearly the same result."""
    eq_flat = EQ0
    for band in EQ0.bands[1:]:
        eq_flat = eq_flat.with_band(replace(band, gain_db=0.0))
    with_eq = design(analyse(measure_session(rooms, eq=EQ0)), EQ0, GRID, FS_HZ)
    with_flat = design(analyse(measure_session(rooms, eq=eq_flat)), eq_flat, GRID, FS_HZ)

    response_a = eq_response_db(with_eq.to_eq(), GRID_HZ, FS_HZ)
    response_b = eq_response_db(with_flat.to_eq(), GRID_HZ, FS_HZ)

    compared = (GRID_HZ > 40) & (GRID_HZ < 10000)
    assert np.sqrt(np.mean((response_a - response_b)[compared] ** 2)) < 2.0


def test_clipping_is_detected(rooms):
    quality = measure_session({"p1": rooms["p1"]}, gain_db=30)["p1"].quality

    assert quality.verdict is Verdict.BAD
    assert quality.clipped_samples > 0
    assert any(w.code is WarningCode.CLIPPING for w in quality.warnings)


def test_noisy_room_is_flagged(rooms):
    quality = measure_session({"p1": rooms["p1"]}, noise_dbfs=-40)["p1"].quality

    assert quality.verdict in (Verdict.WARN, Verdict.BAD)
    assert any(w.code in (WarningCode.LOW_SNR, WarningCode.LOW_PNR) for w in quality.warnings)


def test_broad_common_dip_gets_limited_boost():
    """A broad -6 dB dip in every point may be boosted, but never above +3 dB."""
    rooms = {
        f"p{i}": synthetic_room(np.random.default_rng(300 + i), FS_HZ, broad=(2500, -7.0, 0.5))
        for i in range(5)
    }
    an = analyse(measure_session(rooms))

    proposal = design(an, EQ0, GRID, FS_HZ, target=TargetSpec(tilt_db_per_oct=0.0))

    boosts = [b for b in proposal.bands if b.action is not Action.KEEP and b.band.gain_db > 0]
    assert boosts, proposal.skipped
    assert all(b.band.gain_db <= 3.0 for b in boosts)
    assert any(1200 < b.band.freq_hz < 5000 for b in boosts)


def test_optimize_design_rules(analysed):
    an, _ = analysed
    optimised = optimize_design(an, EQ0, GRID, FS_HZ)
    greedy = design(an, EQ0, GRID, FS_HZ)
    bands = optimised.bands
    assert bands[0].band.type == EqType.BU18
    assert bands[0].action is Action.KEEP
    for b in bands[1:]:
        assert -9.0 - 1e-9 <= b.band.gain_db <= 3.0 + 1e-9
        if b.band.gain_db > 0:
            assert b.band.q <= 1.45
        assert P.snap_gain_db(b.band.gain_db) == pytest.approx(b.band.gain_db)
    # the existing narrow notch 1k97 -3.5 Q6.4 keeps at least half
    notch = bands[3].band
    assert notch.gain_db <= -1.75 + 1e-9
    assert notch.freq_hz == pytest.approx(EQ0.band(4).freq_hz, rel=0.01)
    assert notch.q == pytest.approx(EQ0.band(4).q, rel=0.01)
    cuts = [b.band for b in bands if b.band.gain_db < 0]
    for mode_hz in (63, 120):
        assert any(abs(math.log2(c.freq_hz / mode_hz)) < 0.35 for c in cuts), (mode_hz, bands)
    assert not any(b.band.gain_db > 0 and abs(math.log2(b.band.freq_hz / 800)) < 0.3 for b in bands)
    prediction = optimised.prediction
    assert prediction.max_dev_after_db < prediction.max_dev_before_db
    assert prediction.rms_dev_after_db <= greedy.prediction.rms_dev_after_db + 0.3
