"""Speakers one by one (SPEC S7): heard, polarity, side, mutes restored."""

import threading

import pytest

from churchtune.audio.fake.hall import FakeHall, FakeMic
from churchtune.errors import MeasurementAborted
from churchtune.measure.config import SweepConfig
from churchtune.measure.speakers import (
    Polarity,
    Side,
    Speaker,
    compare_polarity,
    measure_speakers_one_by_one,
    side_from_arrival,
)

FS = 48000.0
SWEEP = SweepConfig(duration_s=1.5, tail_s=1.0)


class Console:
    def __init__(self, invert_right=False, dead=()):
        self.alone = None
        self.calls = []
        self.invert_right = invert_right
        self.dead = set(dead)

    def switch(self, speaker):
        self.calls.append(speaker)
        self.alone = speaker

    def gains(self):
        gains = {"top_l": 1.0, "top_r": -1.0 if self.invert_right else 1.0, "sub": 1.0}
        if self.alone is not None:
            gains = {k: (v if k == self.alone.value else 0.0) for k, v in gains.items()}
        return {k: (0.0 if k in self.dead else v) for k, v in gains.items()}


def _run(console, speakers=tuple(Speaker), stop=None):
    hall = FakeHall(FS, {10: FakeMic(), 11: FakeMic()}, speaker_gains=console.gains)
    hall.place(10, "p2")
    hall.place(11, "p3")
    return measure_speakers_one_by_one(
        hall, [10, 11], console.switch, speakers, fs_hz=FS, sweep=SWEEP, level_dbfs=-24.0, stop=stop
    )


def test_every_speaker_is_heard_and_the_mutes_are_restored():
    console = Console()
    checks = _run(console)
    assert [c.speaker for c in checks] == list(Speaker)
    assert all(all(c.heard.values()) for c in checks)
    assert console.calls[-1] is None


def test_a_silent_speaker_is_reported():
    checks = _run(Console(dead={"sub"}))
    sub = next(c for c in checks if c.speaker is Speaker.SUB)
    assert not any(sub.heard.values())


def test_tops_with_the_same_polarity():
    checks = {c.speaker: c for c in _run(Console(), (Speaker.TOP_L, Speaker.TOP_R))}
    left, right = checks[Speaker.TOP_L].irs[10], checks[Speaker.TOP_R].irs[10]
    assert compare_polarity(left, right, FS) is Polarity.SAME


def test_a_right_top_with_swapped_polarity():
    checks = {
        c.speaker: c for c in _run(Console(invert_right=True), (Speaker.TOP_L, Speaker.TOP_R))
    }
    left, right = checks[Speaker.TOP_L].irs[10], checks[Speaker.TOP_R].irs[10]
    assert compare_polarity(left, right, FS) is Polarity.INVERTED


def test_stop_restores_the_mutes():
    console = Console()
    stop = threading.Event()
    stop.set()
    with pytest.raises(MeasurementAborted):
        _run(console, stop=stop)
    assert console.calls[-1] is None


def test_bad_arguments_are_refused_before_any_mute():
    console = Console()
    hall = FakeHall(FS, {10: FakeMic()})
    with pytest.raises(ValueError, match="speaker check"):
        measure_speakers_one_by_one(hall, [10, 10], console.switch, list(Speaker), fs_hz=FS,
                                    sweep=SWEEP, level_dbfs=-24.0)  # fmt: skip
    assert console.calls == []


@pytest.mark.parametrize(
    ("left_ms", "right_ms", "side"),
    [(20.0, 21.5, Side.LEFT), (21.5, 20.0, Side.RIGHT), (20.0, 20.1, None)],
)
def test_side_from_arrival(left_ms, right_ms, side):
    assert side_from_arrival(left_ms, right_ms) is side
