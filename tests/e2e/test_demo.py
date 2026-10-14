"""``ctune --emulator``: the demo goes all the way, writes included, with nobody at the console."""

import threading
from datetime import datetime

from churchtune.audio.fake.hall import demo_rig
from churchtune.cli.demo import prepare_demo
from churchtune.cli.package_writes import X32WritePort
from churchtune.cli.wizard import X32ConsolePort
from churchtune.i18n import Translator
from churchtune.measure.config import SweepConfig
from churchtune.storage.paths import Workspace
from churchtune.wizard.run import start
from churchtune.wizard.services import AudioDeviceInfo, Services
from churchtune_emulator.x32 import X32Emulator
from tests.e2e.scripted import ScriptedPrompter

FS = 48000.0


class DemoAudio:
    def __init__(self, link):
        self.link = link

    def devices(self):
        return [AudioDeviceInfo("fake", ())]

    def open(self, name):
        hall = demo_rig(FS)
        self.link(hall)
        return hall


def test_the_demo_reaches_the_writes_and_the_check(tmp_path):
    emulator = X32Emulator().start()
    try:
        link = prepare_demo(emulator)
        console = X32ConsolePort()
        now = lambda: datetime(2026, 10, 14, 10, 0, 0)  # noqa: E731 - one clock for both
        services = Services(
            console=console, audio=DemoAudio(link), workspace=Workspace(tmp_path), now=now,
            stop=threading.Event(), dev_safe=True, sweep=SweepConfig(duration_s=1.5, tail_s=1.0),
            sleep=lambda _s: None, writes=X32WritePort(console.last, now, Workspace(tmp_path)),
            words=Translator("en"),
        )  # fmt: skip
        hall = demo_rig(FS)  # the volunteer's hands: only for placing the microphones
        rules = {
            "step-welcome-venue-name": "Demo church",
            "step-connect-none": f"127.0.0.1:{emulator.port}",
            "junction-type": "mute",
            "apply-type": "apply",
        }
        volunteer = ScriptedPrompter(hall, rules, mute=None, scratch_order=[12, 10, 13, 11])
        result = start(volunteer, services, "en")
        keys = volunteer.shown_keys()
        assert result.finished, keys[-6:]
        assert "step-mics-still-open" not in keys  # the demo muted them
        assert result.state.junction_measured
        assert result.state.applied_state == "applied", [k for k in keys if k.startswith("apply")]
        assert result.state.verify_verdict is not None
        console.close()
    finally:
        emulator.stop()
