"""The real-device backend, driven by a stand-in for the ``sounddevice`` module.

No audio hardware is touched: the stand-in runs the stream callback synchronously.
Never weaken the limiter and abort tests in this file.
"""

import numpy as np
import pytest

from churchtune.audio.base import LIMIT
from churchtune.audio.sounddevice_backend import BLOCK_FRAMES, SoundDeviceBackend
from churchtune.errors import AudioDeviceError, DevSafeViolation, MeasurementAborted

FS_HZ = 48000.0
MIC_INPUT = 10
DEVICES = [
    {"name": "MacBook Pro Speakers", "max_input_channels": 0, "max_output_channels": 2, "default_samplerate": 48000.0},
    {"name": "X-USB", "max_input_channels": 32, "max_output_channels": 32, "default_samplerate": 48000.0},
    {"name": "Tiny Interface", "max_input_channels": 2, "max_output_channels": 2, "default_samplerate": 44100.0},
]  # fmt: skip


class CallbackStop(Exception):
    pass


class CallbackAbort(Exception):
    pass


class FakeStream:
    """Runs one callback block each time ``active`` is polled."""

    def __init__(self, module, callback, **settings):
        self.module = module
        self.callback = callback
        self.settings = settings
        self.started = False
        self.finished = False
        self.aborted = False
        self.closed = False
        self.output = []  # every block the callback wrote to the device
        self.swallowed = 0
        self.block = 0

    def start(self):
        self.started = True
        if self.module.interrupt_on_start:
            raise KeyboardInterrupt

    @property
    def active(self):
        if self.finished:
            return False
        indata = np.zeros((BLOCK_FRAMES, self.settings["channels"][0]), dtype=np.float32)
        indata[:, MIC_INPUT - 1] = self.module.mic_level
        outdata = np.full((BLOCK_FRAMES, 2), 9.0, dtype=np.float32)
        status = self.module.statuses.pop(0) if self.module.statuses else ""
        try:
            self.callback(indata, outdata, BLOCK_FRAMES, None, status)
        except (CallbackStop, CallbackAbort):
            self.finished = True
        except Exception:
            # Like the real library: any other exception is swallowed and the stream ends.
            self.finished = True
            self.swallowed += 1
        self.output.append(outdata.copy())
        self.block += 1
        return True

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True


class FakeSoundDevice:
    CallbackStop = CallbackStop
    CallbackAbort = CallbackAbort

    def __init__(self, devices=DEVICES):
        self.devices = devices
        self.streams = []
        self.statuses = []
        self.mic_level = 0.25
        self.unsupported = False
        self.interrupt_on_start = False
        self.checked = []

    def query_devices(self):
        return self.devices

    def check_input_settings(self, **settings):
        self.checked.append(("in", settings))
        if self.unsupported:
            raise RuntimeError("Invalid sample rate")

    def check_output_settings(self, **settings):
        self.checked.append(("out", settings))

    def Stream(self, callback, **settings):  # noqa: N802 - mirrors sounddevice.Stream
        stream = FakeStream(self, callback, **settings)
        self.streams.append(stream)
        return stream


@pytest.fixture
def sd():
    return FakeSoundDevice()


def make(sd, sleep=None, name="x-usb"):
    return SoundDeviceBackend(name, FS_HZ, MIC_INPUT, sd=sd, sleep=sleep or (lambda seconds: None))


def stereo(n, level):
    return np.full((n, 2), level, dtype=np.float32)


# ------------------------------------------------------------ device discovery
def test_finds_the_device_by_case_insensitive_name_part(sd):
    backend = make(sd)

    assert backend.index == 1
    assert backend.describe() == "X-USB (#1): 32 in / 32 out, default 48000 Hz"
    assert backend.name == "sounddevice"
    backend.set_point("p1")


def test_missing_device_lists_what_is_available(sd):
    with pytest.raises(AudioDeviceError, match="not found") as excinfo:
        make(sd, name="DN32-USB")

    assert "X-USB" in excinfo.value.context["available"]


def test_device_with_too_few_channels_is_refused(sd):
    with pytest.raises(AudioDeviceError, match="too few channels"):
        make(sd, name="tiny")


@pytest.mark.parametrize("name", ["", "   ", "\t"])
def test_empty_device_name_is_refused_instead_of_matching_everything(sd, name):
    with pytest.raises(AudioDeviceError, match="empty"):
        make(sd, name=name)


def test_a_name_that_matches_several_devices_is_refused(sd):
    sd.devices = [*DEVICES, {**DEVICES[1], "name": "X-USB (2)"}]

    with pytest.raises(AudioDeviceError, match="matches several devices"):
        make(sd, name="x-u")


def test_an_exact_name_wins_over_other_partial_matches(sd):
    sd.devices = [{**DEVICES[1], "name": "X-USB (2)"}, *DEVICES]

    assert make(sd, name="X-USB").index == 2


def test_mic_input_must_be_positive(sd):
    with pytest.raises(AudioDeviceError, match="mic input"):
        SoundDeviceBackend("x-usb", FS_HZ, 0, sd=sd)


def test_check_samplerate_asks_for_the_stream_format_and_returns_the_default_rate(sd):
    assert make(sd).check_samplerate() == 48000.0
    assert sd.checked[0] == (
        "in",
        {"device": 1, "channels": MIC_INPUT, "samplerate": FS_HZ, "dtype": "float32"},
    )
    assert sd.checked[1][1]["channels"] == 2


def test_check_samplerate_reports_an_unsupported_rate(sd):
    sd.unsupported = True

    with pytest.raises(AudioDeviceError, match="not supported"):
        make(sd).check_samplerate()


# ------------------------------------------------------------ playrec
def test_playrec_opens_one_duplex_stream_on_the_device(sd):
    make(sd).playrec(stereo(100, 0.1))

    settings = sd.streams[0].settings
    assert settings["device"] == (1, 1)
    assert settings["channels"] == (MIC_INPUT, 2)
    assert settings["samplerate"] == FS_HZ
    assert settings["blocksize"] == BLOCK_FRAMES
    assert len(sd.streams) == 1


def test_playrec_plays_the_buffer_then_silence_and_records_the_mic_channel(sd):
    n = BLOCK_FRAMES + 100

    rec, flags = make(sd).playrec(stereo(n, 0.1))

    played = np.concatenate(sd.streams[0].output)
    np.testing.assert_allclose(played[:n], 0.1)
    assert np.all(played[n:] == 0)  # the rest of the last block is zeroed, not left as garbage
    assert rec.shape == (n,)
    np.testing.assert_allclose(rec, 0.25)
    assert flags == []
    assert sd.streams[0].closed
    assert not sd.streams[0].aborted


def test_playrec_limits_a_full_scale_buffer_inside_the_callback(sd):
    make(sd).playrec(stereo(3000, 1.0))

    played = np.concatenate(sd.streams[0].output)
    assert np.max(np.abs(played)) == pytest.approx(LIMIT)
    assert np.max(np.abs(played)) <= np.float32(LIMIT)


def test_playrec_limits_negative_and_non_finite_samples(sd):
    out = stereo(BLOCK_FRAMES, 0.0)
    out[0] = [-4.0, np.inf]
    out[1] = [np.nan, -np.inf]

    make(sd).playrec(out)

    played = sd.streams[0].output[0]
    np.testing.assert_allclose(played[0], [-LIMIT, LIMIT], rtol=1e-6)
    np.testing.assert_allclose(played[1], [0.0, -LIMIT], rtol=1e-6)


def test_playrec_collects_stream_status_flags(sd):
    sd.statuses = ["", "input overflow"]

    _, flags = make(sd).playrec(stereo(3 * BLOCK_FRAMES, 0.1))

    assert flags == ["input overflow"]


def test_playrec_runs_events_in_the_calling_thread_once_playback_passed_them(sd):
    fired = []
    backend = make(sd)

    def action(name):
        return lambda: fired.append((name, sd.streams[0].block))

    backend.playrec(
        stereo(4 * BLOCK_FRAMES, 0.1),
        events=[(3 * BLOCK_FRAMES, action("late")), (BLOCK_FRAMES, action("early"))],
    )

    # After block k the position is k * BLOCK_FRAMES.
    assert fired == [("early", 1), ("late", 3)]


def test_playrec_still_runs_an_event_that_lies_after_the_end_of_the_buffer(sd):
    fired = []

    make(sd).playrec(stereo(BLOCK_FRAMES, 0.1), events=[(10**9, lambda: fired.append("restore"))])

    assert fired == ["restore"]


def test_ctrl_c_aborts_the_stream_before_anything_else(sd):
    order = []

    def interrupted(seconds):
        raise KeyboardInterrupt

    backend = make(sd, sleep=interrupted)
    with pytest.raises(MeasurementAborted):
        backend.playrec(stereo(10 * BLOCK_FRAMES, 0.1), events=[(0, lambda: order.append("event"))])

    stream = sd.streams[0]
    assert stream.aborted
    assert stream.closed
    assert order == []  # no pending event runs after an abort


def test_any_other_error_during_playback_also_aborts_the_stream(sd):
    def broken():
        raise RuntimeError("mute switch failed")

    with pytest.raises(RuntimeError, match="mute switch failed"):
        make(sd).playrec(stereo(10 * BLOCK_FRAMES, 0.1), events=[(0, broken)])

    assert sd.streams[0].aborted
    assert sd.streams[0].closed


def test_ctrl_c_while_the_stream_is_starting_aborts_it(sd):
    sd.interrupt_on_start = True

    with pytest.raises(MeasurementAborted):
        make(sd).playrec(stereo(10 * BLOCK_FRAMES, 0.1))

    assert sd.streams[0].started
    assert sd.streams[0].aborted
    assert sd.streams[0].closed


def test_ctrl_c_raised_by_an_event_aborts_the_stream(sd):
    def interrupt():
        raise KeyboardInterrupt

    with pytest.raises(MeasurementAborted):
        make(sd).playrec(stereo(10 * BLOCK_FRAMES, 0.1), events=[(0, interrupt)])

    assert sd.streams[0].aborted


def test_stream_is_closed_even_when_abort_itself_fails(sd):
    sd.interrupt_on_start = True
    backend = make(sd)
    original_stream = backend._stream

    def stream_with_broken_abort(callback, *args):
        stream = original_stream(callback, *args)

        def broken_abort():
            raise RuntimeError("PortAudio error")

        stream.abort = broken_abort
        return stream

    backend._stream = stream_with_broken_abort

    with pytest.raises(RuntimeError, match="PortAudio error"):
        backend.playrec(stereo(BLOCK_FRAMES, 0.1))

    assert sd.streams[0].closed


@pytest.mark.parametrize("shape", [(1000,), (1000, 3), (1000, 1), (4, 1000, 2)])
def test_playrec_refuses_a_buffer_that_is_not_two_channels_before_opening_a_stream(sd, shape):
    with pytest.raises(AudioDeviceError, match=r"\[n, 2\]"):
        make(sd).playrec(np.zeros(shape, dtype=np.float32))

    assert sd.streams == []


def test_a_failure_inside_the_playrec_callback_is_silenced_reported_and_fires_no_events(
    sd, monkeypatch
):
    fired = []
    backend = make(sd)

    def broken(block):
        raise RuntimeError("limiter exploded")

    monkeypatch.setattr("churchtune.audio.sounddevice_backend.limit", broken)

    with pytest.raises(AudioDeviceError, match="audio callback failed"):
        backend.playrec(stereo(4 * BLOCK_FRAMES, 0.4), events=[(10**9, lambda: fired.append(1))])

    stream = sd.streams[0]
    assert stream.swallowed == 0  # converted into a callback abort, not swallowed
    assert np.all(stream.output[0] == 0)  # the failing block went out as silence
    assert stream.aborted
    assert stream.closed
    assert fired == []


def test_record_plays_silence(sd):
    rec = make(sd).record(0.05)

    assert rec.shape == (2400,)
    assert all(np.all(block == 0) for block in sd.streams[0].output)


# ------------------------------------------------------------ run_stream
def test_run_stream_limits_every_block_and_ends_in_silence(sd):
    blocks = [stereo(BLOCK_FRAMES, 0.2), stereo(BLOCK_FRAMES, 2.0), None]
    heard, ticks = [], []

    make(sd).run_stream(lambda frames: blocks.pop(0), heard.append, lambda: ticks.append(1))

    output = sd.streams[0].output
    np.testing.assert_allclose(output[0], 0.2)
    np.testing.assert_allclose(output[1], LIMIT, rtol=1e-6)
    assert np.all(output[2] == 0)
    assert len(heard) == 2
    np.testing.assert_allclose(heard[0], 0.25)
    assert ticks
    assert sd.streams[0].closed
    assert not sd.streams[0].aborted


def test_run_stream_ctrl_c_aborts_the_stream(sd):
    def interrupted(seconds):
        raise KeyboardInterrupt

    with pytest.raises(MeasurementAborted):
        make(sd, sleep=interrupted).run_stream(
            lambda frames: stereo(frames, 0.1), lambda block: None
        )

    assert sd.streams[0].aborted
    assert sd.streams[0].closed


def test_run_stream_aborts_the_stream_when_the_tick_fails(sd):
    def broken():
        raise RuntimeError("meter display failed")

    with pytest.raises(RuntimeError, match="meter display failed"):
        make(sd).run_stream(lambda frames: stereo(frames, 0.1), lambda block: None, broken)

    assert sd.streams[0].aborted
    assert sd.streams[0].closed


@pytest.mark.parametrize(
    "bad_block",
    [
        np.ones((512, 2), dtype=np.float32),
        np.ones((BLOCK_FRAMES,), dtype=np.float32),
        np.ones((BLOCK_FRAMES, 3), dtype=np.float32),
        np.ones((BLOCK_FRAMES, 1), dtype=np.float32),
        np.float32(1.0),
    ],
    ids=["short", "mono-1d", "three-channels", "one-channel", "scalar"],
)
def test_run_stream_refuses_a_block_of_the_wrong_shape_and_goes_silent(sd, bad_block):
    with pytest.raises(AudioDeviceError, match="audio callback failed"):
        make(sd).run_stream(lambda frames: bad_block, lambda block: None)

    stream = sd.streams[0]
    assert np.all(stream.output[0] == 0)
    assert stream.aborted
    assert stream.closed


@pytest.mark.parametrize("failing", ["next_block", "on_input"])
def test_run_stream_goes_silent_and_reports_when_a_callback_function_fails(sd, failing):
    def broken(*args):
        raise RuntimeError(f"{failing} failed")

    next_block = broken if failing == "next_block" else (lambda frames: stereo(frames, 0.3))
    on_input = broken if failing == "on_input" else (lambda block: None)

    with pytest.raises(AudioDeviceError, match=f"{failing} failed"):
        make(sd).run_stream(next_block, on_input)

    stream = sd.streams[0]
    assert stream.swallowed == 0
    assert np.all(stream.output[-1] == 0)
    assert stream.aborted
    assert stream.closed


def test_run_stream_ctrl_c_while_starting_or_in_the_tick_aborts_the_stream(sd):
    def interrupt():
        raise KeyboardInterrupt

    with pytest.raises(MeasurementAborted):
        make(sd).run_stream(lambda frames: stereo(frames, 0.1), lambda block: None, interrupt)
    sd.interrupt_on_start = True
    with pytest.raises(MeasurementAborted):
        make(sd).run_stream(lambda frames: stereo(frames, 0.1), lambda block: None)

    assert all(stream.aborted and stream.closed for stream in sd.streams)


# ------------------------------------------------------------ dev-safe
class _ImportSentinel:
    def __getattr__(self, name):
        raise AssertionError("the real sounddevice module was touched")


@pytest.mark.parametrize(
    "environ", [{"CTUNE_DEV_SAFE": "1"}, {"CTUNE_DEV_SAFE": "0", "CLAUDECODE": "1"}]
)
def test_constructing_the_backend_directly_is_refused_in_dev_safe(monkeypatch, environ):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", _ImportSentinel())

    with pytest.raises(DevSafeViolation, match="real audio devices"):
        SoundDeviceBackend("X-USB", FS_HZ, MIC_INPUT)


def test_missing_portaudio_is_reported_as_an_audio_device_error(monkeypatch):
    def unavailable():
        raise AudioDeviceError("sounddevice / PortAudio is not available: no library")

    monkeypatch.setattr("churchtune.audio.sounddevice_backend.load_sounddevice", unavailable)

    with pytest.raises(AudioDeviceError, match="not available"):
        SoundDeviceBackend("x-usb", FS_HZ, MIC_INPUT)


# ------------------------------------------------------------ several inputs, stop flag
class Flag:
    def __init__(self, after_polls=0):
        self.polls = 0
        self.after = after_polls

    def is_set(self):
        self.polls += 1
        return self.polls > self.after


def test_playrec_multi_records_the_requested_inputs_in_order(sd):
    backend = make(sd)
    recording = backend.playrec_multi(stereo(2 * BLOCK_FRAMES, 0.1), [MIC_INPUT, 2])
    assert recording.samples.shape == (2 * BLOCK_FRAMES, 2)
    assert np.all(recording.channel(0) == np.float32(sd.mic_level))
    assert np.all(recording.channel(1) == 0)
    assert sd.streams[0].settings["channels"] == (max(MIC_INPUT, 2), 2)
    assert backend.input_count == 32


def test_playrec_multi_refuses_inputs_outside_the_device_before_opening_a_stream(sd):
    backend = make(sd)
    with pytest.raises(AudioDeviceError, match="outside"):
        backend.playrec_multi(stereo(BLOCK_FRAMES, 0.1), [33])
    with pytest.raises(AudioDeviceError, match="repeat"):
        backend.playrec_multi(stereo(BLOCK_FRAMES, 0.1), [3, 3])
    assert sd.streams == []


def test_stop_flag_aborts_the_stream_and_reports(sd):
    backend = make(sd)
    with pytest.raises(MeasurementAborted):
        backend.playrec_multi(stereo(20 * BLOCK_FRAMES, 0.1), [MIC_INPUT], stop=Flag(after_polls=2))
    stream = sd.streams[0]
    assert stream.aborted
    assert stream.closed
    assert len(stream.output) < 20


def test_record_multi_plays_silence(sd):
    backend = make(sd)
    recording = backend.record_multi(BLOCK_FRAMES / FS_HZ, [MIC_INPUT])
    assert recording.samples.shape == (BLOCK_FRAMES, 1)
    assert all(np.all(block == 0) for block in sd.streams[0].output)


def test_run_stream_multi_hands_over_the_requested_columns(sd):
    backend = make(sd)
    blocks = iter([stereo(BLOCK_FRAMES, 0.1), stereo(BLOCK_FRAMES, 0.1)])
    seen = []
    backend.run_stream_multi(lambda frames: next(blocks, None), [2, MIC_INPUT], seen.append)
    assert seen[0].shape == (BLOCK_FRAMES, 2)
    assert np.all(seen[0][:, 1] == np.float32(sd.mic_level))
