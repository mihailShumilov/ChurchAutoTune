"""Level ramp and sweep-level search (SPEC S8): every stop condition, then on the simulated hall."""

import threading

import numpy as np
import pytest

from churchtune.audio.fake.hall import BLOCK_FRAMES, FakeHall, FakeMic
from churchtune.domain.levels import HARD_LIMIT_DBFS, peak_dbfs
from churchtune.errors import MeasurementAborted
from churchtune.measure.config import SweepConfig
from churchtune.measure.level import (
    LevelRamp,
    RampConfig,
    RampReading,
    RampStop,
    SweepLevelSearch,
    SweepStop,
    TrialSweep,
    find_sweep_level,
    run_level_ramp,
)
from churchtune.measure.stimulus import build_stimulus, make_sweep

FS = 48000.0
QUIET = RampReading(mic_rms_dbfs=-60.0, peak_dbfs=-50.0)
# SPEC M2: silence within 50 ms of a stop.
MAX_STOP_S = 0.050


def test_ramp_rises_two_db_per_step_while_quiet():
    nxt = LevelRamp.start().step(QUIET)
    assert nxt.stopped is None
    assert nxt.output_dbfs == pytest.approx(-48.0)


@pytest.mark.parametrize(
    ("reading", "reason"),
    [
        (RampReading(-15.0, -5.0), RampStop.TARGET),
        (RampReading(-10.0, -3.0), RampStop.ABOVE_TARGET),
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


def test_no_response_looks_at_the_last_twelve_db_not_the_first_step():
    ramp = LevelRamp.start()
    mic = -60.0
    for _ in range(4):  # the mic follows for a while ...
        ramp = ramp.step(RampReading(mic, mic + 10))
        mic += 2.0
    for _ in range(6):  # ... then the path drops out
        ramp = ramp.step(RampReading(mic, mic + 10))
    assert ramp.stopped is RampStop.NO_RESPONSE


def test_ramp_never_passes_the_hard_output_limit():
    ramp = LevelRamp.start(RampConfig(start_dbfs=-8.0))
    rising = RampReading(-40.0, -30.0)
    ramp = ramp.step(rising)
    assert ramp.output_dbfs == pytest.approx(HARD_LIMIT_DBFS)  # exactly the limit is allowed
    assert ramp.step(rising).stopped is RampStop.OUTPUT_LIMIT


@pytest.mark.parametrize(
    "settings",
    [{"start_dbfs": -3.0}, {"step_db": 0.0}, {"step_db": float("nan")}, {"step_s": 0.1}],
)
def test_ramp_config_refuses_unsafe_settings(settings):
    with pytest.raises(ValueError, match="ramp"):
        RampConfig(**settings)


def test_sweep_search_rises_while_quiet():
    state = SweepLevelSearch().step(-20.0)
    assert state.stopped is None
    assert state.level_dbfs == pytest.approx(-27.0)


def test_sweep_search_finds_the_window():
    assert SweepLevelSearch().step(-9.0).stopped is SweepStop.FOUND


def test_sweep_search_steps_down_and_checks_again_when_too_loud():
    state = SweepLevelSearch().step(-2.0)
    assert state.stopped is None
    assert state.level_dbfs == pytest.approx(-33.0)
    state = state.step(-3.5)  # still too loud
    assert state.level_dbfs == pytest.approx(-36.0)
    assert state.step(-7.0).stopped is SweepStop.FOUND


def test_sweep_search_gives_up_at_the_minimum_level():
    state = SweepLevelSearch(level_dbfs=-48.0).step(-1.0)
    assert state.stopped is SweepStop.TOO_LOUD_AT_MIN


def test_sweep_search_stops_when_the_mic_does_not_follow():
    state = SweepLevelSearch()
    for _ in range(4):
        state = state.step(-60.0)
    assert state.step(-60.0).stopped is SweepStop.NO_RESPONSE


def test_sweep_search_respects_the_ramp_level():
    state = SweepLevelSearch(level_dbfs=-20.0, max_level_dbfs=-19.0).step(-30.0)
    assert state.stopped is SweepStop.OUTPUT_LIMIT


def _hall(gain_db=10.0, mics=None):
    hall = FakeHall(FS, mics or {10: FakeMic(), 11: FakeMic()}, gain_db=gain_db)
    for number, usb in enumerate(hall.mics, start=1):
        hall.place(usb, f"p{number}")
    return hall


def test_ramp_on_the_hall_reaches_the_target():
    hall = _hall()
    steps = []
    ramp, reading = run_level_ramp(hall, [10, 11], 10, FS, on_step=lambda r, m: steps.append(m))
    assert ramp.stopped is RampStop.TARGET
    assert RampConfig().target_low_dbfs <= reading.mic_rms_dbfs <= RampConfig().target_high_dbfs
    assert len(steps) > 3
    assert len(steps[-1].levels_dbfs) == 2
    assert max(float(abs(b).max()) for b in hall.played) <= 10 ** (HARD_LIMIT_DBFS / 20) + 1e-6


def test_ramp_on_the_hall_detects_a_dead_microphone():
    hall = _hall(mics={11: FakeMic()})
    ramp, _ = run_level_ramp(hall, [10, 11], 10, FS)  # input 10 has no microphone
    assert ramp.stopped is RampStop.NO_RESPONSE


def test_operators_stop_silences_the_ramp_within_50_ms():
    hall = _hall()
    stop = threading.Event()
    played_at_stop = []

    def press_stop_after_three_steps(ramp, reading):
        if len(ramp.history) == 3:
            played_at_stop.append(sum(len(b) for b in hall.played))
            stop.set()

    ramp, _ = run_level_ramp(
        hall, [10, 11], 10, FS, stop=stop, on_step=press_stop_after_three_steps
    )
    assert ramp.stopped is RampStop.USER
    after = np.concatenate(hall.played)[played_at_stop[0] :]
    sounding = np.nonzero(np.any(after != 0, axis=1))[0]
    last = int(sounding.max()) + 1 if len(sounding) else 0
    assert last / FS <= MAX_STOP_S
    assert BLOCK_FRAMES / FS <= MAX_STOP_S


def test_a_stop_before_the_start_plays_almost_nothing():
    hall = _hall()
    stop = threading.Event()
    stop.set()
    ramp, _ = run_level_ramp(hall, [10, 11], 10, FS, stop=stop)
    assert ramp.stopped is RampStop.USER
    assert sum(len(b) for b in hall.played) / FS <= MAX_STOP_S


def test_ramp_needs_the_reference_among_the_inputs():
    with pytest.raises(ValueError, match="reference"):
        run_level_ramp(_hall(), [11], 10, FS)


def _trial_peak(hall, level_dbfs):
    trial = TrialSweep()
    config = SweepConfig(
        duration_s=trial.duration_s, preroll_s=trial.preroll_s, tail_s=trial.tail_s
    )
    out = build_stimulus(make_sweep(FS, config, level_dbfs), FS, config.preroll_s, config.tail_s)
    return peak_dbfs(hall.playrec_multi(out, [10]).samples)


def test_sweep_level_on_the_hall_lands_in_the_window():
    hall = _hall(gain_db=12.0, mics={10: FakeMic()})
    state = find_sweep_level(hall, [10], FS)
    assert state.stopped is SweepStop.FOUND
    assert -12.5 <= _trial_peak(hall, state.level_dbfs) <= -6.0


def test_sweep_level_on_a_hot_hall_comes_down_to_a_safe_level():
    hall = _hall(gain_db=30.0, mics={10: FakeMic()})
    state = find_sweep_level(hall, [10], FS)
    assert state.stopped in (SweepStop.FOUND, SweepStop.TOO_LOUD_AT_MIN)
    if state.stopped is SweepStop.FOUND:
        assert _trial_peak(hall, state.level_dbfs) <= -6.0


def test_sweep_level_on_a_deaf_mic_stops_early():
    hall = _hall(gain_db=-10.0, mics={11: FakeMic()})  # nothing on input 10: unplugged
    state = find_sweep_level(hall, [10, 11], FS, reference=10)
    assert state.stopped is SweepStop.NO_RESPONSE
    assert state.level_dbfs <= -18.0


def test_sweep_level_search_stops_on_request():
    stop = threading.Event()
    stop.set()
    with pytest.raises(MeasurementAborted):
        find_sweep_level(_hall(), [10], FS, stop=stop)


def test_sweep_level_reference_must_be_recorded():
    with pytest.raises(ValueError, match="reference"):
        find_sweep_level(_hall(), [10], FS, reference=11)
