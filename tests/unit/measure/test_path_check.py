"""Path check on the simulated hall (SPEC S7)."""

import numpy as np
import pytest

from churchtune.audio.fake.hall import FakeHall, FakeMic
from churchtune.errors import LevelError
from churchtune.measure.path_check import check_path, path_stimulus

FS = 48000.0


def test_heard_mic_reports_the_latency():
    hall = FakeHall(FS, {10: FakeMic()}, latency_samples=2400)
    hall.place(10, "p1")
    result = check_path(hall, [10], FS)[0]
    assert result.heard
    assert result.tone_db > 20
    # System latency (50 ms) plus the acoustic path of the point.
    assert 50 < result.latency_ms < 120


def test_an_input_without_a_microphone_is_not_heard():
    hall = FakeHall(FS, {10: FakeMic()})
    hall.place(10, "p1")
    result = check_path(hall, [10, 11], FS)[1]
    assert not result.heard
    assert result.latency_ms is None


def test_a_room_null_at_1_khz_does_not_hide_the_speakers():
    null_at_1k = ((800.0, 0.0), (1000.0, -15.0), (1250.0, 0.0))
    hall = FakeHall(FS, {10: FakeMic(shape=null_at_1k)})
    hall.place(10, "p2")
    assert check_path(hall, [10], FS)[0].heard


def test_muted_speakers_are_not_heard():
    hall = FakeHall(FS, {10: FakeMic()}, speaker_gains=lambda: {"top_l": 0, "top_r": 0, "sub": 0})
    hall.place(10, "p1")
    assert not check_path(hall, [10], FS)[0].heard


def test_check_signal_stays_quiet():
    out, _ = path_stimulus(FS)
    assert float(np.max(np.abs(out))) <= 10 ** (-40 / 20) + 1e-6
    with pytest.raises(LevelError):
        path_stimulus(FS, level_dbfs=0.0)
