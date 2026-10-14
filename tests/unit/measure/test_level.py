"""Level ramp and sweep-level search (SPEC S8): every stop condition, then on the simulated hall."""

import threading

import pytest

from churchtune.audio.fake.hall import FakeHall, FakeMic
from churchtune.domain.levels import HARD_LIMIT_DBFS
from churchtune.errors import MeasurementAborted
from churchtune.measure.level import (
    LevelRamp,
    RampConfig,
    RampReading,
    RampStop,
    SweepLevelSearch,
    SweepStop,
    find_sweep_level,
    run_level_ramp,
)

FS = 48000.0
QUIET = RampReading(mic_rms_dbfs=-60.0, peak_dbfs=-50.0)


def test_ramp_rises_two_db_per_step_while_quiet():
    ramp = LevelRamp.start()
    nxt = ramp.step(QUIET)
    assert nxt.stopped is None
    assert nxt.output_dbfs == pytest.approx(-48.0)


@pytest.mark.parametrize(
    ("reading", "reason"),
    [
        (RampReading(-15.0, -5.0), RampStop.TARGET),
        (RampReading(-30.0, -0.2), RampStop.CLIP),
        (RampReading(-4.0, -1.0), RampStop.JUMP),
    ],
)
def test_ramp_stops_on_the_microphone(reading, reason):
    assert LevelRamp.start().step(reading).stopped is reason


def test_ramp_stops_when_the_operator_stops():
    assert LevelRamp.start().step(QUIET, user_stop=True).stopped is RampStop.USER


def test_ramp_stops_when_the_mic_does_not_follow_the_output():
    ramp = LevelRamp.start()
    for _ in range(6):  # 12 dB up, the mic stays where it was
        ramp = ramp.step(QUIET)
    assert ramp.step(QUIET).stopped is RampStop.NO_RESPONSE


def test_ramp_never_passes_the_hard_output_limit():
    ramp = LevelRamp.start(RampConfig(start_dbfs=-8.0))
    rising = RampReading(-40.0, -30.0)
    ramp = ramp.step(rising)
    assert ramp.output_dbfs == pytest.approx(HARD_LIMIT_DBFS)  # exactly the limit is allowed
    ramp = ramp.step(rising)
    assert ramp.stopped is RampStop.OUTPUT_LIMIT
    assert ramp.output_dbfs <= HARD_LIMIT_DBFS


@pytest.mark.parametrize(
    ("peak", "stopped", "level"),
    [(-20.0, None, -27.0), (-9.0, SweepStop.FOUND, -30.0), (-3.0, SweepStop.TOO_LOUD, -33.0)],
)
def test_sweep_search_steps(peak, stopped, level):
    state = SweepLevelSearch().step(peak)
    assert state.stopped is stopped
    assert state.level_dbfs == pytest.approx(level)


def test_sweep_search_stops_at_the_output_limit():
    state = SweepLevelSearch(level_dbfs=-7.0).step(-30.0)
    assert state.stopped is SweepStop.OUTPUT_LIMIT
    assert state.level_dbfs <= HARD_LIMIT_DBFS


def _hall(**mic):
    hall = FakeHall(FS, {10: FakeMic(**mic), 11: FakeMic()})
    hall.place(10, "p1")
    hall.place(11, "p2")
    return hall


def test_ramp_on_the_hall_reaches_the_target():
    hall = FakeHall(FS, {10: FakeMic(), 11: FakeMic()}, gain_db=10.0)
    hall.place(10, "p1")
    hall.place(11, "p2")
    steps = []
    ramp, reading = run_level_ramp(hall, [10, 11], 10, FS, on_step=lambda r, m: steps.append(r))
    assert ramp.stopped is RampStop.TARGET
    assert reading.mic_rms_dbfs >= RampConfig().target_low_dbfs
    assert len(steps) > 3
    assert max(float(abs(b).max()) for b in hall.played) <= 10 ** (HARD_LIMIT_DBFS / 20) + 1e-6


def test_ramp_on_the_hall_detects_a_dead_microphone():
    hall = FakeHall(FS, {11: FakeMic()})
    hall.place(11, "p2")
    ramp, _ = run_level_ramp(hall, [10, 11], 10, FS)  # input 10 has no microphone
    assert ramp.stopped is RampStop.NO_RESPONSE


def test_ramp_stops_quietly_on_the_operators_stop():
    stop = threading.Event()
    stop.set()
    ramp, _ = run_level_ramp(_hall(), [10, 11], 10, FS, stop=stop)
    assert ramp.stopped is RampStop.USER


def test_ramp_needs_the_reference_among_the_inputs():
    with pytest.raises(ValueError, match="reference"):
        run_level_ramp(_hall(), [11], 10, FS)


def test_sweep_level_on_the_hall():
    hall = FakeHall(FS, {10: FakeMic()}, gain_db=12.0)
    hall.place(10, "p1")
    state = find_sweep_level(hall, [10], FS)
    assert state.stopped in (SweepStop.FOUND, SweepStop.TOO_LOUD)
    assert state.level_dbfs <= HARD_LIMIT_DBFS


def test_sweep_level_on_a_quiet_hall_stops_at_the_limit():
    hall = FakeHall(FS, {10: FakeMic()}, gain_db=-40.0)
    hall.place(10, "p1")
    assert find_sweep_level(hall, [10], FS).stopped is SweepStop.OUTPUT_LIMIT


def test_sweep_level_search_stops_on_request():
    stop = threading.Event()
    stop.set()
    with pytest.raises(MeasurementAborted):
        find_sweep_level(_hall(), [10], FS, stop=stop)
