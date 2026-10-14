"""The simulated hall hears the console's Main LR EQ, also when the sound comes in blocks."""

import numpy as np
import pytest

from churchtune.audio.fake.hall import FakeHall, FakeMic
from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.domain.eq_model import eq_response_db

FS = 48000.0
EQ = SystemEq(True, tuple(Band(n, EqType.PEQ, 1000.0, 0.0, 1.4) for n in range(1, 7))).with_band(
    Band(1, EqType.PEQ, 50.8, -6.0, 3.7)
)


def test_a_narrow_low_cut_is_heard_in_blocks_as_in_one_piece():
    hall = FakeHall(FS, {10: FakeMic()}, main_eq=lambda: EQ)
    t = np.arange(int(FS * 2)) / FS
    tone = np.column_stack([np.sin(2 * np.pi * 50.8 * t)] * 2) * 0.1
    blocks = [hall._through_main_eq(tone[i : i + 1024]) for i in range(0, len(tone), 1024)]
    out = np.concatenate(blocks)[int(FS) :]  # after the filter settled
    gain_db = 20 * np.log10(np.sqrt(np.mean(out[:, 0] ** 2)) / np.sqrt(np.mean(tone[:, 0] ** 2)))
    expected = float(eq_response_db(EQ, np.array([50.8]), FS)[0])
    assert gain_db == pytest.approx(expected, abs=0.3)


def test_without_an_eq_or_with_it_off_the_sound_is_untouched():
    tone = np.ones((16, 2))
    assert FakeHall(FS, {})._through_main_eq(tone) is tone
    off = FakeHall(FS, {}, main_eq=lambda: EQ.with_on(False))
    assert off._through_main_eq(tone) is tone
