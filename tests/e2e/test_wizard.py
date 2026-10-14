"""The whole wizard on the emulator and the simulated hall (SPEC §14.1 E2E, M3 acceptance)."""

import threading
from datetime import datetime

import pytest

from churchtune.audio.fake.hall import FakeHall, FakeMic
from churchtune.cli.package_writes import X32WritePort
from churchtune.cli.wizard import X32ConsolePort
from churchtune.i18n import Translator
from churchtune.measure.config import SweepConfig
from churchtune.storage.files import (
    load_points,
    read_packages,
    read_plan,
    read_session_mic_calibration,
    read_undo,
    read_wizard_state,
)
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
    hall.main_eq = emulator.main_eq  # the hall hears what is written to Main LR
    console = X32ConsolePort()
    now = lambda: datetime(2026, 10, 14, 10, 0, 0)  # noqa: E731 - one clock for both
    services = Services(
        console=console,
        audio=HallAudio(hall),
        workspace=Workspace(tmp_path),
        now=now,
        stop=threading.Event(),
        dev_safe=True,
        sweep=SWEEP,
        sleep=lambda _s: None,
        writes=X32WritePort(console.last, now),
        words=Translator("en"),
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
    # calibration step, so the total grows from 14 to 15 at that point (SPEC §7).
    assert [h.total for h in volunteer.headers] == [14] * 6 + [15] * 9
    assert [h.number for h in volunteer.headers] == list(range(1, 16))
    session = latest_session(services)
    assert session is not None
    assert read_wizard_state(session).completed[-1] == "finish"
    assert len(load_points(session)) == 8
    assert (
        "finding-spread-small" in volunteer.shown_keys()
        or "finding-spread-large" in volunteer.shown_keys()
    )
    assert volunteer.meters
    assert emulator.writes == []  # Enter at the typed word cancels: nothing is written
    assert "apply-cancelled" in volunteer.shown_keys()


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
    assert {h.total for h in volunteer.headers} == {14}


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


def test_the_results_step_proposes_packages_and_saves_the_choice(rig):
    emulator, hall, services = rig
    volunteer = _volunteer(emulator, hall, **{"package-question": "skip"})
    result = start(volunteer, services, "en")
    assert result.finished
    assert result.state.proposal_file is not None
    stored = read_packages(services.workspace.root / result.state.proposal_file)
    assert stored.packages
    assert not any(p.selected for p in stored.packages)  # the volunteer skipped every one
    kinds = [p.kind for p in stored.packages]
    assert "system_eq" in kinds
    assert "crossover" not in kinds  # never set blind: the wizard does not measure sub/tops yet
    assert [n.code for n in stored.notes] == ["junction-not-measured"]
    assert read_plan(services.workspace, result.state.venue) is not None
    assert len(result.state.junction_points) == 4
    keys = volunteer.shown_keys()
    assert "proposal-what" in keys or "proposal-nothing" in keys
    assert emulator.writes == []  # proposals are never written in M4


def test_kept_packages_are_marked_selected(rig):
    emulator, hall, services = rig
    result = start(_volunteer(emulator, hall), services, "en")
    stored = read_packages(services.workspace.root / result.state.proposal_file)
    assert all(p.selected for p in stored.packages)


def test_leaving_the_crossover_out_redesigns_the_eq_without_it(rig):
    emulator, hall, services = rig

    def leave_out_the_crossover(prompt):
        keys = [text.key for text in prompt.body]
        return Answer("skip" if "package-crossover" in keys else "keep")

    volunteer = _volunteer(emulator, hall, **{"package-question": leave_out_the_crossover})
    result = start(volunteer, services, "en")
    stored = read_packages(services.workspace.root / result.state.proposal_file)
    for package in stored.packages:
        assert package.kind != "crossover"
        assert "crossover" not in package.requires


def test_a_plan_without_a_hall_size_keeps_the_stored_plan(rig):
    emulator, hall, services = rig
    first = _volunteer(emulator, hall, **{"step-equipment-hall": "14 x 22"})
    start(first, services, "en")
    stored = read_plan(services.workspace, "test-church")
    assert stored is not None
    assert (stored.width_m, stored.depth_m) == (14.0, 22.0)
    second = _volunteer(emulator, hall, **{"step-welcome-venue": "test-church"})
    start(second, services, "en")
    again = read_plan(services.workspace, "test-church")
    assert again == stored


def _main_eq_writes(emulator):
    return [address for address, _ in emulator.writes if address.startswith("/main/st/eq/")]


def test_typing_the_word_writes_the_chosen_packages_with_an_undo_file(rig):
    emulator, hall, services = rig
    volunteer = _volunteer(emulator, hall, **{"apply-type": "apply"})
    result = start(volunteer, services, "en")
    assert result.finished
    keys = volunteer.shown_keys()
    assert "apply-done" in keys
    assert "apply-scene-not-saved" in keys
    assert _main_eq_writes(emulator)
    assert result.state.applied_undo is not None
    undo = read_undo(services.workspace.root / result.state.applied_undo)
    assert undo.what == "packages"
    assert len(undo.results) == len(undo.changes)
    assert all(r.ok for r in undo.results)
    assert emulator.unknown == []
    # S13 measured again and judged against the same points.
    assert result.state.verify_verdict in ("better", "no_change")
    assert len(result.state.verify_points) == 4
    assert set(result.state.verify_measured) == set(result.state.verify_points)
    assert "verify-chart" in keys


def test_a_check_that_got_worse_offers_the_way_back(rig):
    emulator, hall, services = rig

    def spoil_it(prompt):
        emulator.hand_change("/main/st/eq/5/g", 0.7)  # someone boosts +6 dB after the write
        emulator.hand_change("/main/st/eq/5/f", 0.6)
        return Answer("")

    volunteer = _volunteer(
        emulator,
        hall,
        **{
            "apply-type": "apply",
            "apply-done": spoil_it,
            "undo-type": "undo",
            "step-points-bad": "accept",
        },
    )
    result = start(volunteer, services, "en")
    assert result.finished
    keys = volunteer.shown_keys()
    assert result.state.verify_verdict == "worse"
    assert "verify-offer-undo" in keys
    assert "undo-done" in keys or "undo-nothing" in keys


def test_a_console_changed_after_the_proposal_is_refused(rig):
    emulator, hall, services = rig

    def turn_a_knob(prompt):
        emulator.hand_change("/main/st/eq/3/g", 0.9)  # someone boosts band 3 meanwhile
        return Answer("")

    volunteer = _volunteer(
        emulator, hall, **{"step-results-next-apply": turn_a_knob, "apply-type": "apply"}
    )
    result = start(volunteer, services, "en")
    assert result.finished
    assert "apply-console-changed" in volunteer.shown_keys()
    assert emulator.writes == []
    assert result.state.applied_undo is None


def test_a_write_that_does_not_read_back_stops_and_offers_the_way_back(rig):
    emulator, hall, services = rig
    volunteer = _volunteer(emulator, hall, **{"apply-type": "apply", "undo-type": "undo"})

    def ignore_main_eq(prompt):
        emulator.ignored.update(f"/main/st/eq/{n}/{k}" for n in range(1, 7) for k in "fgq")
        return Answer("apply")

    volunteer.rules["apply-type"] = ignore_main_eq
    result = start(volunteer, services, "en")
    assert result.finished
    keys = volunteer.shown_keys()
    assert "apply-readback-failed" in keys
    assert "apply-offer-undo" in keys or "apply-offer-check" in keys
    assert result.state.applied_undo is not None
    assert "undo-done" in keys or "undo-nothing" in keys or "undo-incomplete" in keys


def test_a_typo_in_the_word_asks_again_instead_of_ending(rig):
    emulator, hall, services = rig
    answers = iter(["aplly", "apply"])
    volunteer = _volunteer(emulator, hall, **{"apply-type": lambda _p: Answer(next(answers))})
    result = start(volunteer, services, "ru")
    assert result.finished
    keys = volunteer.shown_keys()
    assert "apply-word-mismatch" in keys
    assert "apply-done" in keys
    assert result.state.applied_state == "applied"


def test_after_the_way_back_the_last_screen_does_not_ask_to_save_the_scene(rig):
    emulator, hall, services = rig

    def spoil_it(prompt):
        emulator.hand_change("/main/st/eq/5/g", 0.7)
        emulator.hand_change("/main/st/eq/5/f", 0.6)
        return Answer("")

    volunteer = _volunteer(
        emulator, hall,
        **{"apply-type": "apply", "apply-done": spoil_it, "undo-type": "undo",
           "step-points-bad": "accept"},
    )  # fmt: skip
    result = start(volunteer, services, "uk")
    keys = volunteer.shown_keys()
    if result.state.applied_state == "reverted":
        assert "step-finish-reverted" in keys
        assert "step-finish-scene" not in keys
    else:  # the hand change itself was left alone: something written stays
        assert "step-finish-scene" in keys
