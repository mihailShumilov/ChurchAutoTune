"""Sub / top junction with several microphones and temporary mutes (SPEC S10, M2 acceptance)."""

import threading

import numpy as np
import pytest

from churchtune.audio.fake.hall import FakeHall, FakeMic
from churchtune.errors import ChurchTuneError, LevelError, MeasurementAborted
from churchtune.measure.config import SweepConfig
from churchtune.measure.junction import JunctionBlock, measure_junction_round

FS = 48000.0
SWEEP = SweepConfig(duration_s=1.5, tail_s=1.0)
GAINS = {
    JunctionBlock.SUB_ONLY: {"top_l": 0.0, "top_r": 0.0, "sub": 1.0},
    JunctionBlock.TOPS_ONLY: {"top_l": 1.0, "top_r": 1.0, "sub": 0.0},
    JunctionBlock.ORIGINAL: {"top_l": 1.0, "top_r": 1.0, "sub": 1.0},
}


class FakeConsole:
    """Applies the mute plan of each block; records every switch."""

    def __init__(self, fail_on=None):
        self.block = JunctionBlock.ORIGINAL
        self.switches = []
        self.fail_on = fail_on

    def switch(self, block):
        self.switches.append(block)
        if block is self.fail_on:
            self.fail_on = None
            raise ChurchTuneError("mute not confirmed")
        self.block = block

    def gains(self):
        return GAINS[self.block]


def _setup(**options):
    console = FakeConsole(**options)
    hall = FakeHall(FS, {10: FakeMic(), 11: FakeMic()}, speaker_gains=console.gains)
    hall.place(10, "p1")
    hall.place(11, "p4")
    return console, hall


def test_junction_round_measures_every_mic_and_restores():
    console, hall = _setup()
    mics = measure_junction_round(
        hall, [10, 11], console.switch, fs_hz=FS, sweep=SWEEP, level_dbfs=-24.0
    )
    assert [m.usb_input for m in mics] == [10, 11]
    assert console.switches == [
        JunctionBlock.SUB_ONLY,
        JunctionBlock.TOPS_ONLY,
        JunctionBlock.ORIGINAL,
        JunctionBlock.ORIGINAL,
    ]
    assert console.block is JunctionBlock.ORIGINAL
    for mic in mics:
        assert all(p > -60 for p in mic.peaks_dbfs)
    # The sub block carries almost no high frequencies, the tops block almost no lows.
    sub_spectrum = np.abs(np.fft.rfft(mics[0].sub))
    tops_spectrum = np.abs(np.fft.rfft(mics[0].tops))
    freqs = np.fft.rfftfreq(len(mics[0].sub), 1 / FS)
    highs = (freqs > 2000) & (freqs < 8000)
    assert np.mean(sub_spectrum[highs]) < 0.1 * np.mean(tops_spectrum[highs])


def test_stop_during_the_round_silences_within_50_ms_and_restores_the_mutes():
    """SPEC M2: Ctrl+C at any moment -> silence within 50 ms and the mutes restored."""
    console, hall = _setup()
    stop = threading.Event()
    original_gains = console.gains

    def gains_and_stop():
        if console.block is JunctionBlock.TOPS_ONLY:
            stop.set()  # the operator presses Ctrl+C while the tops block plays
        return original_gains()

    hall.speaker_gains = gains_and_stop
    with pytest.raises(MeasurementAborted):
        measure_junction_round(
            hall, [10, 11], console.switch, fs_hz=FS, sweep=SWEEP, level_dbfs=-24.0, stop=stop
        )
    assert console.block is JunctionBlock.ORIGINAL
    assert console.switches[-1] is JunctionBlock.ORIGINAL
    played = hall.played[-1]
    stopped_at = hall.stopped_at
    assert stopped_at is not None
    sounding = np.nonzero(np.any(played != 0, axis=1))[0]
    assert (sounding.max() - stopped_at) / FS <= 0.05 if len(sounding) else True
    assert not np.any(played[stopped_at:])


def test_a_failed_switch_still_restores():
    console, hall = _setup(fail_on=JunctionBlock.TOPS_ONLY)
    with pytest.raises(ChurchTuneError):
        measure_junction_round(hall, [10], console.switch, fs_hz=FS, sweep=SWEEP, level_dbfs=-24.0)
    assert console.switches[-1] is JunctionBlock.ORIGINAL
    assert console.block is JunctionBlock.ORIGINAL


def test_level_is_checked_before_any_mute():
    console, hall = _setup()
    with pytest.raises(LevelError):
        measure_junction_round(hall, [10], console.switch, fs_hz=FS, sweep=SWEEP, level_dbfs=-3.0)
    assert console.switches == []
