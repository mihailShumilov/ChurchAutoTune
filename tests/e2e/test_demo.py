"""``ctune --emulator``: the demo goes all the way, writes included, with nobody at the console."""

import threading
from datetime import datetime

from typer.testing import CliRunner

from churchtune.audio.fake.hall import demo_rig
from churchtune.cli.demo import prepare_demo
from churchtune.cli.main import app
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
SECTIONS = (
    "summary", "system", "quality", "response", "junction", "decay", "changes", "verify",
    "audit", "acoustics", "checklist", "appendix",
)  # fmt: skip


def _the_report_is_written_and_can_be_made_again(home, keys):
    """S14 writes the report (SPEC §10); ``ctune report`` makes it again from the files."""
    assert "step-finish-report" in keys
    workspace = Workspace(home)
    session = next(s for venue in workspace.venues() for s in workspace.sessions_of(venue))
    report = session / "report"
    page = (report / "report.html").read_text(encoding="utf-8")
    assert [f'<section id="{name}">' in page for name in SECTIONS] == [True] * len(SECTIONS)
    assert "http://" not in page
    assert "https://" not in page
    for missing in ("not measured separately", "There was no check measurement"):
        assert missing not in page  # the demo measured the junction and checked the result
    assert (report / "report.md").exists()
    assert (report / "report.json").exists()
    assert (report / "response-average.png").read_bytes().startswith(b"\x89PNG")
    runner = CliRunner()
    chat = runner.invoke(app, ["--home", str(home), "report", str(session), "--for-chat"])
    assert chat.exit_code == 0, chat.output
    assert 1000 < len(chat.output.encode()) <= 6000
    again = runner.invoke(app, ["--home", str(home), "report", str(session), "--lang", "uk"])
    assert again.exit_code == 0, again.output
    assert 'lang="uk"' in (report / "report.html").read_text(encoding="utf-8")


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
    console = X32ConsolePort()
    try:
        link = prepare_demo(emulator)
        now = lambda: datetime(2026, 10, 14, 10, 0, 0)  # noqa: E731 - one clock for both
        services = Services(
            console=console, audio=DemoAudio(link), workspace=Workspace(tmp_path), now=now,
            stop=threading.Event(), dev_safe=True, sweep=SweepConfig(duration_s=1.5, tail_s=1.0),
            sleep=lambda _s: None, writes=X32WritePort(console.last, now, Workspace(tmp_path)),
            words=Translator("en"), translate=Translator("en"),
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
        _the_report_is_written_and_can_be_made_again(tmp_path, keys)
    finally:
        console.close()
        emulator.stop()
