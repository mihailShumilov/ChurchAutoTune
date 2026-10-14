"""The whole wizard on the emulator and the simulated hall (SPEC §14.1 E2E, M3 acceptance)."""

import threading
from datetime import datetime

import pytest

from churchtune.audio.fake.hall import FakeHall, FakeMic
from churchtune.cli.wizard import X32ConsolePort
from churchtune.measure.config import SweepConfig
from churchtune.storage.files import load_points, read_session_mic_calibration, read_wizard_state
from churchtune.storage.paths import Workspace
from churchtune.wizard.protocol import Answer
from churchtune.wizard.run import latest_session, resume, run_wizard, start
from churchtune.wizard.services import AudioDeviceInfo, Services
from churchtune_emulator.x32 import X32Emulator
from tests.e2e.scripted import QUIT, ScriptedPrompter

FS = 48000.0
SWEEP = SweepConfig(duration_s=1.5, tail_s=1.0)


class HallAudio:
    def __init__(self, hall):
        self.hall = hall

    def devices(self):
        return [AudioDeviceInfo("fake", ())]

    def open(self, name):
        return self.hall


@pytest.fixture
def rig(tmp_path):
    emulator = X32Emulator().start()
    hall = FakeHall(FS, {10: FakeMic(), 11: FakeMic(sensitivity_db=1.5)}, gain_db=10.0)
    hall.place(10, "p1")
    hall.place(11, "p2")
    console = X32ConsolePort()
    services = Services(
        console=console,
        audio=HallAudio(hall),
        workspace=Workspace(tmp_path),
        now=lambda: datetime(2026, 10, 14, 10, 0, 0),
        stop=threading.Event(),
        dev_safe=True,
        sweep=SWEEP,
        sleep=lambda _s: None,
    )
    yield emulator, hall, services
    console.close()
    emulator.stop()


def _volunteer(emulator, hall, **extra_rules):
    rules = {
        "step-welcome-venue-name": "Test church",
        "step-connect-none": f"127.0.0.1:{emulator.port}",
        "step-equipment-mic-count": "2",
        **extra_rules,
    }
    return ScriptedPrompter(hall, rules, mute=emulator.press_mute, scratch_order=[11, 10])


def test_full_goal_with_two_microphones(rig):
    emulator, hall, services = rig
    volunteer = _volunteer(emulator, hall)
    result = start(volunteer, services, "ru")
    assert result.finished, volunteer.shown_keys()[-5:]
    state = result.state
    assert state.venue == "test-church"
    assert state.console_model == "X32P"
    assert state.roles["main 6"] == "sub"
    assert set(state.mic_inputs) == {10, 11}
    assert state.calibration_set is not None
    assert len(state.measured) == 8
    # Step numbering counts only the applicable steps; choosing two microphones adds the
    # calibration step, so the total grows from 12 to 13 at that point (SPEC §7).
    assert [h.total for h in volunteer.headers] == [12] * 6 + [13] * 7
    assert [h.number for h in volunteer.headers] == list(range(1, 14))
    session = latest_session(services)
    assert session is not None
    assert read_wizard_state(session).completed[-1] == "finish"
    assert len(load_points(session)) == 8
    assert (
        "finding-spread-small" in volunteer.shown_keys()
        or "finding-spread-large" in volunteer.shown_keys()
    )
    assert volunteer.meters
    assert emulator.writes == []  # the wizard of M3 never writes to the console


def test_quit_and_resume_continues_after_the_last_step(rig):
    emulator, hall, services = rig
    first = _volunteer(emulator, hall, **{"step-level-what": QUIT})
    result = start(first, services, "en")
    assert not result.finished
    assert result.state.completed[-1] == "path"
    session = latest_session(services)
    assert session is not None
    second = _volunteer(emulator, hall)
    resumed = resume(second, services, session)
    assert resumed.finished
    assert second.headers[0].title.key == "step-level-title"
    assert "step-welcome-what" not in second.shown_keys()


def test_one_microphone_skips_the_calibration(rig):
    emulator, hall, services = rig
    volunteer = _volunteer(emulator, hall, **{"step-equipment-mic-count": "1"})
    volunteer.scratch_order = [10]
    result = start(volunteer, services, "uk")
    assert result.finished
    assert "step-calibration-title" not in [h.title.key for h in volunteer.headers]
    assert {h.total for h in volunteer.headers} == {12}


def test_no_sound_until_the_measurement_channels_are_muted(rig):
    emulator, hall, services = rig
    pressed: list[int] = []

    def late_mute(channel):  # the volunteer forgets the first time
        pressed.append(channel)
        if len(pressed) > 2:
            emulator.press_mute(channel)

    volunteer = _volunteer(emulator, hall)
    volunteer.mute = late_mute
    assert start(volunteer, services, "en").finished
    keys = volunteer.shown_keys()
    assert "step-mics-still-open" in keys
    assert keys.index("step-mics-still-open") < keys.index("sound-warning")


def test_space_on_a_quiet_screen_does_not_cut_the_next_recording(rig):
    emulator, hall, services = rig

    def space_then_enter(_prompt):
        services.stop.set()  # Space pressed while reading the screen
        return Answer("")

    volunteer = _volunteer(emulator, hall, **{"step-mics-what": space_then_enter})
    assert start(volunteer, services, "en").finished


def test_every_sweep_round_is_announced_and_counted_down(rig):
    emulator, hall, services = rig
    waits: list[float] = []
    services.sleep = waits.append
    volunteer = _volunteer(emulator, hall)
    assert start(volunteer, services, "en").finished
    keys = volunteer.shown_keys()
    places = [i for i, k in enumerate(keys) if k == "step-points-place"]
    for index in places:
        assert "sound-warning" in keys[index:]
    rounds = sum(
        1 for prompt in volunteer.prompts if any(t.key == "step-points-place" for t in prompt.body)
    )
    assert rounds == 4  # eight places, two microphones
    # The calibration and every round count down three seconds.
    assert waits == [1.0] * 3 * (rounds + 1)


def test_stop_during_the_level_ramp_repeats_the_step(rig):
    emulator, hall, services = rig
    volunteer = _volunteer(emulator, hall)
    original = volunteer.meter

    def stop_after_a_few(levels):
        original(levels)
        if len(volunteer.meters) == 3:
            services.stop.set()  # Ctrl+C while the pink noise plays

    volunteer.meter = stop_after_a_few
    assert start(volunteer, services, "en").finished
    keys = volunteer.shown_keys()
    assert "step-level-ramp-user" in keys
    assert keys.count("step-level-ramp") == 2  # warned again before the second ramp


def test_stop_during_a_sweep_round_saves_and_resume_measures_the_rest(rig):
    emulator, hall, services = rig
    volunteer = _volunteer(emulator, hall)
    rounds = []

    def stop_in_the_second_round(label, done, total):
        if label.key == "sound-countdown" and done == 0:
            rounds.append(1)
            if len(rounds) == 3:  # calibration, round 1, round 2
                services.stop.set()

    volunteer.progress = stop_in_the_second_round
    first = start(volunteer, services, "en")
    assert not first.finished
    assert len(first.state.measured) == 2
    session = latest_session(services)
    assert session is not None
    second = _volunteer(emulator, hall)
    resumed = resume(second, services, session)
    assert resumed.finished
    assert len(resumed.state.measured) == 8
    keys = second.shown_keys()
    assert keys.index("sound-warning") < keys.index("step-points-done")


def test_a_console_lost_on_resume_offers_retry_then_saves(rig):
    emulator, hall, services = rig
    first = _volunteer(emulator, hall, **{"step-level-what": QUIT})
    start(first, services, "en")
    session = latest_session(services)
    assert session is not None
    emulator.stop()
    second = _volunteer(emulator, hall, **{"err-what-now": "quit"})
    resumed = resume(second, services, session)
    assert not resumed.finished
    assert "err-console-unreachable" in second.shown_keys()
    assert read_wizard_state(session).completed[-1] == "path"


def test_a_damaged_snapshot_on_resume_is_explained_not_a_crash(rig):
    emulator, hall, services = rig
    first = _volunteer(emulator, hall, **{"step-level-what": QUIT})
    start(first, services, "en")
    session = latest_session(services)
    assert session is not None
    state = read_wizard_state(session)
    snapshot = services.workspace.root / state.snapshot_file
    snapshot.write_text("{ not json", encoding="utf-8")
    second = _volunteer(emulator, hall, **{"err-what-now": "quit"})
    # Level does not read the snapshot; back to the path check, which does.
    state = state.model_copy(update={"completed": state.completed[:-1]})
    resumed = run_wizard(second, services, state, session)
    assert not resumed.finished
    assert "err-bad-file" in second.shown_keys()


def test_the_session_keeps_its_own_calibration(rig):
    emulator, hall, services = rig
    assert start(_volunteer(emulator, hall), services, "en").finished
    session = latest_session(services)
    assert session is not None
    assert read_session_mic_calibration(session) is not None
