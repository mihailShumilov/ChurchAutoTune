"""Auto-mapping microphones by a scratch on the simulated hall."""

import numpy as np
import pytest

from churchtune.audio.fake.hall import FakeHall, FakeMic
from churchtune.measure.mic_mapping import MappingOutcome, find_scratch

FS = 48000.0
INPUTS = list(range(1, 33))


def _listen(hall, inputs=INPUTS):
    return hall.record_multi(2.0, inputs).samples


@pytest.fixture
def hall():
    return FakeHall(FS, {10: FakeMic(), 11: FakeMic(), 12: FakeMic(), 13: FakeMic()})


def test_each_scratch_finds_its_input(hall):
    found = []
    for usb in (12, 10, 13, 11):
        hall.scratch(usb)
        result = find_scratch(_listen(hall), INPUTS, FS, taken=found)
        assert result.outcome is MappingOutcome.FOUND
        assert result.usb_input == usb
        assert result.rise_db > 20
        found.append(usb)


def test_no_scratch_means_nothing(hall):
    result = find_scratch(_listen(hall), INPUTS, FS)
    assert result.outcome is MappingOutcome.NOTHING
    assert result.usb_input is None


def test_scratching_a_mapped_mic_again_is_reported(hall):
    hall.scratch(10)
    result = find_scratch(_listen(hall), INPUTS, FS, taken=[10])
    assert result.outcome is MappingOutcome.ALREADY_MAPPED
    assert result.usb_input == 10


def test_two_mics_at_once_are_ambiguous(hall):
    hall.scratch(10)
    hall.scratch(11)
    result = find_scratch(_listen(hall), INPUTS, FS)
    assert result.outcome is MappingOutcome.AMBIGUOUS


def test_a_clipping_scratch_is_flagged(hall):
    hall.scratch(13, level_dbfs=3.0)
    result = find_scratch(_listen(hall), INPUTS, FS)
    assert result.usb_input == 13
    assert result.clipped


def test_column_count_must_match():
    with pytest.raises(ValueError, match="columns"):
        find_scratch(np.zeros((100, 3)), [1, 2], FS)


def test_a_stray_sample_on_a_silent_input_does_not_beat_a_real_scratch():
    rng = np.random.default_rng(1)
    n = int(2 * FS)
    mic = rng.normal(scale=1e-4, size=n)  # -80 dBFS self-noise
    mic[int(0.5 * FS) : int(0.75 * FS)] += rng.normal(scale=0.1, size=int(0.25 * FS))  # scratch
    silent = np.zeros(n)
    silent[1000] = 3e-5
    result = find_scratch(np.stack([mic, silent], axis=1), [10, 25], FS)
    assert result.outcome is MappingOutcome.FOUND
    assert result.usb_input == 10
