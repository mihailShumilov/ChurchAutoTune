"""Limiter, fake backends, synthetic rooms and the backend factory.

Never weaken the limiter and dev-safe tests in this file.
"""

import math
from dataclasses import replace

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.extra import numpy as npst

from churchtune.audio.base import LIMIT, limit
from churchtune.audio.factory import open_backend, open_multi_backend
from churchtune.audio.fake.backend import ConsoleRoomBackend, FakeBackend
from churchtune.audio.fake.hall import FakeHall
from churchtune.audio.fake.room import demo_rooms, speaker_rooms, synthetic_room
from churchtune.domain.console import ConsoleState, MatrixState, OutputDelay
from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.domain.levels import HARD_LIMIT_DBFS, peak_dbfs, rms_dbfs
from churchtune.errors import DevSafeViolation
from churchtune.measure.config import SweepConfig
from churchtune.measure.stimulus import build_stimulus, make_sweep

FS_HZ = 48000.0
FLAT = SystemEq(True, tuple(Band(i, EqType.PEQ, 1000.0, 0.0, 2.0) for i in range(1, 7)))
IMPULSE_ROOM = np.zeros(4800)
IMPULSE_ROOM[0] = 1.0


def stereo(mono):
    return np.stack([mono, mono], axis=1).astype(np.float32)


def tone(freq_hz, level, n=48000):
    return (level * np.sin(2 * np.pi * freq_hz * np.arange(n) / FS_HZ)).astype(np.float64)


# ------------------------------------------------------------ limiter
def test_limit_is_minus_six_dbfs():
    assert pytest.approx(10 ** (-6 / 20)) == LIMIT
    assert 20 * math.log10(LIMIT) == pytest.approx(HARD_LIMIT_DBFS)


def test_limiter_clips_full_scale_input_to_the_hard_limit():
    full_scale = stereo(np.array([1.0, -1.0, 0.9, -0.9, 5.0, -5.0, np.inf, -np.inf]))

    limited = limit(full_scale)

    assert limited.dtype == np.float32
    assert np.max(np.abs(limited)) == pytest.approx(LIMIT)
    np.testing.assert_allclose(np.abs(limited), LIMIT, rtol=1e-6)


def test_limiter_leaves_quiet_signals_untouched():
    quiet = stereo(np.linspace(-0.4, 0.4, 101))

    np.testing.assert_array_equal(limit(quiet), quiet)


def test_limiter_turns_nan_into_silence():
    assert np.array_equal(limit(np.array([np.nan, 0.1], dtype=np.float32)), [0.0, np.float32(0.1)])


@given(
    block=st.one_of(
        npst.arrays(
            np.float64,
            npst.array_shapes(max_dims=2, max_side=8),
            elements=st.floats(allow_nan=True, allow_infinity=True),
        ),
        npst.arrays(
            np.float32,
            npst.array_shapes(max_dims=2, max_side=8),
            elements=st.floats(width=32, allow_nan=True, allow_infinity=True),
        ),
        npst.arrays(np.int16, npst.array_shapes(max_dims=2, max_side=8)),
    )
)
def test_limiter_output_is_float32_same_shape_and_never_above_the_limit(block):
    limited = limit(block)

    assert limited.dtype == np.float32
    assert limited.shape == block.shape
    assert np.all(np.isfinite(limited))
    assert np.all(np.abs(limited) <= np.float32(LIMIT))


@pytest.mark.parametrize("shape", [(100,), (100, 3), (100, 1)])
def test_simulators_refuse_a_buffer_that_is_not_two_channels(shape):
    backend = FakeBackend({"p1": IMPULSE_ROOM}, FS_HZ)

    with pytest.raises(ValueError, match=r"\[n, 2\]"):
        backend.playrec(np.zeros(shape, dtype=np.float32))

    assert backend.played == []


# ------------------------------------------------------------ fake backend
def test_fake_backend_never_lets_more_than_the_hard_limit_leave_the_computer():
    backend = FakeBackend({"p1": IMPULSE_ROOM}, FS_HZ, gain_db=0.0, noise_dbfs=-200.0)
    full_scale = stereo(np.ones(4800))

    rec, flags = backend.playrec(full_scale)

    assert flags == []
    assert np.max(np.abs(backend.played[0])) == pytest.approx(LIMIT)
    # Impulse room, 0 dB path: what the mic hears is the limited signal, delayed by the latency.
    assert np.max(np.abs(rec)) == pytest.approx(LIMIT, rel=1e-5)


def test_fake_backend_run_stream_limits_every_block():
    backend = FakeBackend({"p1": IMPULSE_ROOM}, FS_HZ)
    blocks = [np.ones((1024, 2), dtype=np.float32), np.ones((1024, 2), dtype=np.float32) * -3, None]
    heard, ticks = [], []

    backend.run_stream(lambda frames: blocks.pop(0), heard.append, lambda: ticks.append(1))

    assert len(backend.played) == 2
    assert all(np.max(np.abs(block)) == pytest.approx(LIMIT) for block in backend.played)
    assert len(heard) == 2
    assert len(ticks) == 2
    assert heard[0].shape == (1024,)


def test_fake_backend_applies_latency_gain_and_noise():
    backend = FakeBackend(
        {"p1": IMPULSE_ROOM}, FS_HZ, latency_samples=100, gain_db=-20.0, noise_dbfs=-200.0
    )
    click = np.zeros(4800)
    click[10] = 0.4

    rec, _ = backend.playrec(stereo(click))

    assert int(np.argmax(np.abs(rec))) == 110
    assert rec[110] == pytest.approx(0.04, rel=1e-5)


def test_fake_backend_noise_floor_has_the_configured_level():
    backend = FakeBackend({"p1": IMPULSE_ROOM}, FS_HZ, noise_dbfs=-60.0)

    # 48000 samples of white noise: the RMS estimate is within 0.1 dB.
    assert rms_dbfs(backend.record(1.0)) == pytest.approx(-60.0, abs=0.1)


def test_fake_backend_applies_the_console_eq_from_the_provider():
    cut = FLAT.with_band(Band(3, EqType.PEQ, 1000.0, -9.0, 1.5))
    current = [FLAT]
    backend = FakeBackend(
        {"p1": IMPULSE_ROOM}, FS_HZ, gain_db=0.0, noise_dbfs=-200.0, eq_provider=lambda: current[0]
    )
    signal_in = stereo(tone(1000.0, 0.2))

    flat_rec, _ = backend.playrec(signal_in)
    current[0] = cut
    cut_rec, _ = backend.playrec(signal_in)
    current[0] = cut.with_on(False)
    off_rec, _ = backend.playrec(signal_in)

    steady = slice(24000, 48000)
    assert rms_dbfs(cut_rec[steady]) - rms_dbfs(flat_rec[steady]) == pytest.approx(-9.0, abs=0.05)
    assert rms_dbfs(off_rec[steady]) == pytest.approx(rms_dbfs(flat_rec[steady]), abs=0.01)


def test_fake_backend_clips_the_mic_like_an_adc():
    backend = FakeBackend({"p1": IMPULSE_ROOM}, FS_HZ, gain_db=30.0, noise_dbfs=-200.0)

    rec, _ = backend.playrec(stereo(tone(1000.0, 0.4)))

    assert np.max(np.abs(rec)) == 1.0


@pytest.mark.parametrize(
    ("name", "expected"), [("p2", "p2"), ("p2-left", "p2"), ("p9-nowhere", "p1"), ("", "p1")]
)
def test_fake_backend_set_point_selects_the_room(name, expected):
    backend = FakeBackend({"p1": IMPULSE_ROOM, "p2": IMPULSE_ROOM}, FS_HZ)

    backend.set_point(name)

    assert backend.point == expected


def test_fake_backend_runs_events_in_sample_order():
    backend = FakeBackend({"p1": IMPULSE_ROOM}, FS_HZ)
    order = []

    backend.playrec(
        stereo(np.zeros(1000)),
        events=[(800, lambda: order.append("b")), (100, lambda: order.append("a"))],
    )

    assert order == ["a", "b"]


def test_fake_backend_describes_itself():
    backend = FakeBackend(demo_rooms(FS_HZ), FS_HZ)

    assert backend.describe() == "SIMULATOR: synthetic room, 6 points"
    assert backend.check_samplerate() == FS_HZ
    assert backend.name == "simulator"


# ------------------------------------------------------------ rooms
def test_synthetic_room_has_the_direct_sound_at_the_delay_and_is_normalised():
    room = synthetic_room(np.random.default_rng(1), FS_HZ, drr_db=20.0)

    assert len(room) == 3 * 48000
    assert int(np.argmax(np.abs(room))) == int(0.012 * FS_HZ)
    assert np.max(np.abs(room)) == pytest.approx(0.5)
    assert np.all(room[: int(0.012 * FS_HZ)] == 0)


def test_synthetic_room_is_reproducible_and_seed_dependent():
    a = synthetic_room(np.random.default_rng(7), FS_HZ)
    b = synthetic_room(np.random.default_rng(7), FS_HZ)
    c = synthetic_room(np.random.default_rng(8), FS_HZ)

    np.testing.assert_array_equal(a, b)
    assert not np.array_equal(a, c)


def test_synthetic_room_mode_shows_up_as_a_spectral_peak():
    plain = synthetic_room(np.random.default_rng(3), FS_HZ)
    with_mode = synthetic_room(np.random.default_rng(3), FS_HZ, modes=[(63.0, 0.02, 2.5)])

    def level_db(room, freq_hz):
        spectrum = np.abs(np.fft.rfft(room, 1 << 18))
        return 20 * np.log10(spectrum[round(freq_hz * (1 << 18) / FS_HZ)])

    assert level_db(with_mode, 63.0) - level_db(plain, 63.0) > 10
    assert abs(level_db(with_mode, 1000.0) - level_db(plain, 1000.0)) < 3


def test_demo_rooms_are_six_points_and_only_p3_has_the_local_mode():
    rooms = demo_rooms(FS_HZ)

    def level_db(room, freq_hz):
        spectrum = np.abs(np.fft.rfft(room, 1 << 18))
        return 20 * np.log10(spectrum[round(freq_hz * (1 << 18) / FS_HZ)])

    assert list(rooms) == ["p1", "p2", "p3", "p4", "p5", "p6"]
    others = np.mean([level_db(rooms[p], 1250.0) for p in ("p1", "p2", "p4", "p5", "p6")])
    assert level_db(rooms["p3"], 1250.0) - others > 6


def test_speaker_rooms_delay_the_sub_and_band_limit_both():
    tops, sub = speaker_rooms(FS_HZ, sub_extra_ms=7.0, drr_db=20.0)

    def level_db(room, freq_hz):
        spectrum = np.abs(np.fft.rfft(room, 1 << 18))
        return 20 * np.log10(spectrum[round(freq_hz * (1 << 18) / FS_HZ)])

    assert sorted(tops) == [1, 2]
    # Direct sound of the tops at 12 ms; the sub's low-passed arrival is 7 ms later.
    assert int(np.argmax(np.abs(tops[1]))) == pytest.approx(0.012 * FS_HZ, abs=24)
    assert int(np.argmax(np.abs(sub))) > 0.019 * FS_HZ - 24
    assert level_db(sub, 2000.0) < level_db(sub, 60.0) - 40
    assert level_db(tops[1], 20.0) < level_db(tops[1], 1000.0) - 15


# ------------------------------------------------------------ console room backend
def _state(sub_on=True, tops_on=True, sub_invert=False, sub_delay_on=False, sub_fader_db=0.0):
    matrices = {
        1: MatrixState(1, "Main L", FLAT, tops_on, 0.0, False, (8,)),
        2: MatrixState(2, "Main R", FLAT, tops_on, 0.0, False, (7,)),
        3: MatrixState(3, "Sub", FLAT, sub_on, sub_fader_db, sub_invert, (6,)),
    }
    delays = {o: OutputDelay(o, sub_delay_on and o == 6, 10.0) for o in (6, 7, 8)}
    return ConsoleState(FLAT, matrices, delays)


def _console_backend(state_fn, **kwargs):
    impulse_top = np.zeros(2400)
    impulse_top[0] = 1.0
    impulse_sub = np.zeros(2400)
    impulse_sub[0] = 1.0
    return ConsoleRoomBackend(
        state_fn,
        FS_HZ,
        sub_matrix=3,
        tops={1: impulse_top, 2: impulse_top},
        sub=impulse_sub,
        latency_samples=0,
        noise_dbfs=-200.0,
        sub_sens_db=0.0,
        **kwargs,
    )


def test_console_backend_sums_tops_and_sub_and_respects_mutes():
    click = np.zeros(2400)
    click[100] = 0.1

    def heard(**state):
        rec, _ = _console_backend(lambda: _state(**state)).playrec(stereo(click))
        return rec[100]

    assert heard() == pytest.approx(0.3, rel=1e-5)  # two tops and the sub, all unity
    assert heard(sub_on=False) == pytest.approx(0.2, rel=1e-5)
    assert heard(tops_on=False) == pytest.approx(0.1, rel=1e-5)
    assert heard(sub_invert=True) == pytest.approx(0.1, rel=1e-5)  # 0.2 - 0.1
    assert heard(sub_fader_db=-math.inf) == pytest.approx(0.2, rel=1e-5)
    assert heard(sub_fader_db=-6.0206) == pytest.approx(0.25, rel=1e-4)


def test_console_backend_applies_the_output_delay_only_when_it_is_on():
    click = np.zeros(2400)
    click[100] = 0.1
    backend = _console_backend(lambda: _state(tops_on=False, sub_delay_on=True))

    rec, _ = backend.playrec(stereo(click))

    assert int(np.argmax(np.abs(rec))) == 100 + 480  # 10 ms at 48 kHz


def test_console_backend_rereads_the_console_at_every_event_boundary():
    state = {"sub_on": True, "tops_on": False}
    backend = _console_backend(lambda: _state(**state))
    clicks = np.zeros(3000)
    clicks[[100, 1100, 2100]] = 0.1
    log = []

    def mute_sub():
        log.append("mute")
        state["sub_on"] = False

    def unmute_sub():
        log.append("unmute")
        state["sub_on"] = True

    rec, _ = backend.playrec(stereo(clicks), events=[(2000, unmute_sub), (1000, mute_sub)])

    assert log == ["mute", "unmute"]
    assert rec[100] == pytest.approx(0.1, rel=1e-5)
    assert abs(rec[1100]) < 1e-9
    assert rec[2100] == pytest.approx(0.1, rel=1e-5)


def test_console_backend_limits_what_leaves_the_computer():
    backend = _console_backend(_state)

    blocks = [np.ones((1024, 2), dtype=np.float32), None]

    backend.playrec(stereo(np.ones(2400)))
    backend.run_stream(lambda frames: blocks.pop(0), lambda block: None)

    assert all(np.max(np.abs(block)) == pytest.approx(LIMIT) for block in backend.played)
    assert len(backend.played) == 2


def test_console_backend_uses_a_default_hall_when_no_rooms_are_given():
    backend = ConsoleRoomBackend(_state, FS_HZ, sub_matrix=3)

    assert sorted(backend.tops) == [1, 2]
    assert backend.describe().startswith("SIMULATOR")
    assert backend.check_samplerate() == FS_HZ
    assert len(backend.record(0.1)) == 4800
    backend.set_point("p1")


def test_console_backend_main_eq_and_matrix_eq_shape_the_sound():
    sub_low_pass = FLAT.with_band(Band(6, EqType.LR24, 100.0, 0.0, 2.0))
    base = _state(tops_on=False)
    filtered = replace(
        base, matrices={**base.matrices, 3: replace(base.matrices[3], eq=sub_low_pass)}
    )
    signal_in = stereo(tone(1000.0, 0.2))

    open_rec, _ = _console_backend(lambda: base).playrec(signal_in)
    lp_rec, _ = _console_backend(lambda: filtered).playrec(signal_in)

    # LR24 at 100 Hz is far more than 60 dB down at 1 kHz.
    assert rms_dbfs(lp_rec[24000:]) < rms_dbfs(open_rec[24000:]) - 60


# ------------------------------------------------------------ a real stimulus through the simulator
def test_a_default_sweep_passes_the_fake_backend_unchanged_by_the_limiter():
    sweep_cfg = SweepConfig(duration_s=1.0, tail_s=0.5)
    stimulus = build_stimulus(
        make_sweep(FS_HZ, sweep_cfg), FS_HZ, sweep_cfg.preroll_s, sweep_cfg.tail_s
    )
    backend = FakeBackend(demo_rooms(FS_HZ), FS_HZ)

    backend.playrec(stimulus)

    np.testing.assert_array_equal(backend.played[0], stimulus)
    assert peak_dbfs(backend.played[0]) == pytest.approx(-24.0, abs=0.05)


# ------------------------------------------------------------ factory and dev-safe
def test_factory_opens_the_simulator_for_fake():
    backend = open_backend("fake", fs_hz=FS_HZ, mic_input=10, fake_noise_dbfs=-70.0)

    assert isinstance(backend, FakeBackend)
    assert backend.noise == pytest.approx(10 ** (-70 / 20))
    assert sorted(backend.rooms) == ["p1", "p2", "p3", "p4", "p5", "p6"]


@pytest.mark.parametrize("name", ["FAKE", " fake "])
def test_factory_accepts_fake_in_any_case(name):
    assert isinstance(open_backend(name, fs_hz=FS_HZ, mic_input=10), FakeBackend)


@pytest.mark.parametrize("name", ["X-USB", "MacBook Pro Speakers", "", "fake2", "default"])
def test_factory_refuses_real_devices_in_dev_safe_without_touching_them(monkeypatch, name):
    def forbidden(*args, **kwargs):
        raise AssertionError("a real audio device was opened")

    monkeypatch.setattr(
        "churchtune.audio.sounddevice_backend.SoundDeviceBackend.__init__", forbidden
    )
    monkeypatch.setattr("churchtune.audio.sounddevice_backend.load_sounddevice", forbidden)

    with pytest.raises(DevSafeViolation) as excinfo:
        open_backend(name, fs_hz=FS_HZ, mic_input=10)

    assert excinfo.value.context == {"audio": name}


def test_factory_refuses_real_devices_when_only_the_agent_variable_is_set(monkeypatch):
    monkeypatch.setenv("CTUNE_DEV_SAFE", "0")
    monkeypatch.setenv("CLAUDECODE", "1")

    with pytest.raises(DevSafeViolation):
        open_backend("X-USB", fs_hz=FS_HZ, mic_input=10)


def test_factory_opens_a_real_backend_only_outside_dev_safe(monkeypatch):
    opened = []

    class Recorder:
        def __init__(self, device_name, fs_hz, mic_input):
            opened.append((device_name, fs_hz, mic_input))

    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CTUNE_DEV_SAFE", raising=False)
    monkeypatch.setattr("churchtune.audio.sounddevice_backend.SoundDeviceBackend", Recorder)

    backend = open_backend("X-USB", fs_hz=FS_HZ, mic_input=10)

    assert isinstance(backend, Recorder)
    assert opened == [("X-USB", FS_HZ, 10)]


@pytest.mark.parametrize("name", ["X-USB", "", "fake2"])
def test_multichannel_factory_refuses_real_devices_in_dev_safe(monkeypatch, name):
    def forbidden(*args, **kwargs):
        raise AssertionError("a real audio device was opened")

    monkeypatch.setattr(
        "churchtune.audio.sounddevice_backend.SoundDeviceBackend.__init__", forbidden
    )
    monkeypatch.setattr("churchtune.audio.sounddevice_backend.load_sounddevice", forbidden)

    with pytest.raises(DevSafeViolation) as excinfo:
        open_multi_backend(name, fs_hz=FS_HZ)

    assert excinfo.value.context == {"audio": name}


def test_multichannel_factory_opens_the_rig_for_fake_and_a_device_outside_dev_safe(monkeypatch):
    assert isinstance(open_multi_backend(" FAKE ", fs_hz=FS_HZ), FakeHall)
    opened = []

    class Recorder:
        def __init__(self, device_name, fs_hz, mic_input):
            opened.append((device_name, fs_hz, mic_input))

    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CTUNE_DEV_SAFE", raising=False)
    monkeypatch.setattr("churchtune.audio.sounddevice_backend.SoundDeviceBackend", Recorder)
    open_multi_backend("X-USB", fs_hz=FS_HZ)
    assert opened == [("X-USB", FS_HZ, 1)]
