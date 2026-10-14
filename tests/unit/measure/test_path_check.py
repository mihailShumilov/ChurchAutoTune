"""Path check and speaker polarity on the simulated hall (SPEC S7)."""

import numpy as np
import pytest

from churchtune.audio.fake.hall import FakeHall, FakeMic
from churchtune.errors import LevelError
from churchtune.measure.config import QualityGates, SweepConfig
from churchtune.measure.multi import measure_round
from churchtune.measure.path_check import check_path, path_stimulus, same_polarity

FS = 48000.0


def test_heard_mic_reports_the_latency_and_a_dead_input_is_not_heard():
    hall = FakeHall(FS, {10: FakeMic()}, latency_samples=2400)
    hall.place(10, "p1")
    results = check_path(hall, [10, 11], FS)
    assert results[0].heard
    assert results[0].tone_db > 20
    # System latency (50 ms) plus the acoustic path of the point.
    assert 50 < results[0].latency_ms < 120
    assert not results[1].heard
    assert results[1].latency_ms is None


def test_muted_speakers_are_not_heard():
    hall = FakeHall(FS, {10: FakeMic()}, speaker_gains=lambda: {"top_l": 0, "top_r": 0, "sub": 0})
    hall.place(10, "p1")
    assert not check_path(hall, [10], FS)[0].heard


def test_check_signal_stays_quiet():
    out, _ = path_stimulus(FS)
    assert float(np.max(np.abs(out))) <= 10 ** (-40 / 20) + 1e-6
    with pytest.raises(LevelError):
        path_stimulus(FS, level_dbfs=0.0)


def _speaker_ir(left, right, inverted_right=False):
    gains = {"top_l": left, "top_r": -right if inverted_right else right, "sub": 0.0}
    hall = FakeHall(FS, {10: FakeMic()}, speaker_gains=lambda: gains)
    hall.place(10, "p3")
    sweep = SweepConfig(duration_s=1.5, tail_s=1.0)
    return (
        measure_round(hall, [10], fs_hz=FS, sweep=sweep, gates=QualityGates(), n_avg=1).mics[0].ir
    )


def test_polarity_of_left_and_right_tops():
    left = _speaker_ir(1.0, 0.0)
    right = _speaker_ir(0.0, 1.0)
    swapped = _speaker_ir(0.0, 1.0, inverted_right=True)
    assert same_polarity(left, right, FS)
    assert not same_polarity(left, swapped, FS)
