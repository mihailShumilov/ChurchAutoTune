"""``ctune selftest``: the installed package checks itself (SPEC §12).

The whole wizard runs against a console emulator started inside the program on 127.0.0.1 and a
simulated hall, with a scripted volunteer answering: reading the console, the microphones, the
measurement, the subwoofer junction, the proposals, the writes (to the emulator only), the check
measurement and the report. Dev-safe mode is switched on for the run, so no real console and no
sound card can be reached, and nothing is played.
"""

import os
import shutil
import tempfile
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from churchtune import __version__
from churchtune.audio.base import MultiChannelBackend
from churchtune.audio.fake.hall import FakeHall, demo_rig
from churchtune.cli.context import EXIT_OK, EXIT_USER, fail
from churchtune.cli.demo import HallLink, prepare_demo
from churchtune.cli.package_writes import X32WritePort
from churchtune.cli.scripted import Rule, ScriptedPrompter
from churchtune.cli.wizard import X32ConsolePort
from churchtune.errors import DevSafeViolation
from churchtune.i18n import LANGUAGES, Translator, detect_language
from churchtune.measure.config import SweepConfig
from churchtune.platform.devsafe import DEV_SAFE_VAR, is_dev_safe
from churchtune.storage.paths import Workspace
from churchtune.wizard.engine import RunResult
from churchtune.wizard.reporting import REPORT_DIR
from churchtune.wizard.run import start
from churchtune.wizard.services import AudioDeviceInfo, Services

FS_HZ = 48000.0
# Short sweeps: the check is about the program working, not about the simulated room.
SWEEP = SweepConfig(duration_s=1.5, tail_s=1.0)
# The wizard runs in English: its typed confirmation words are then known to the volunteer.
WIZARD_LANGUAGE = "en"
SCRATCH_ORDER = (12, 10, 13, 11)  # the order the volunteer touches the demo microphones in
ERROR_FILE = "selftest-error.txt"  # the traceback, kept for an issue


class _SimulatedAudio:
    """The only audio device of the self-check: one simulated hall, linked to the emulator.

    The same hall is opened every time and handed to the volunteer, so where the volunteer puts
    and scratches the microphones is what the measurements hear.
    """

    def __init__(self, hall: FakeHall, link: HallLink) -> None:
        self._hall = hall
        link(hall)

    def devices(self) -> list[AudioDeviceInfo]:
        return [AudioDeviceInfo("fake", ())]

    def open(self, name: str) -> MultiChannelBackend:  # noqa: ARG002 - one device only
        return self._hall


def _run_wizard(home: Path) -> RunResult:
    if not is_dev_safe():  # second line: the scripted volunteer types the confirmation words
        raise DevSafeViolation("the self-check runs only in dev-safe mode")
    from churchtune_emulator.x32 import X32Emulator  # noqa: PLC0415 - loaded only for the check

    emulator = X32Emulator().start()
    console = X32ConsolePort()
    try:
        hall = demo_rig(FS_HZ)
        hall.auto_scratch = []  # the volunteer scratches the microphones, in this order
        audio = _SimulatedAudio(hall, prepare_demo(emulator))
        workspace = Workspace(home)
        now = datetime.now
        words = Translator(WIZARD_LANGUAGE)
        services = Services(
            console=console, audio=audio, workspace=workspace, now=now, stop=threading.Event(),
            dev_safe=True, sweep=SWEEP, sleep=lambda _s: None,
            writes=X32WritePort(console.last, now, workspace), words=words, translate=words,
            language=WIZARD_LANGUAGE,
        )  # fmt: skip
        rules: dict[str, Rule] = {
            "step-welcome-venue-name": "Self-check",
            "step-connect-none": f"127.0.0.1:{emulator.port}",
            "junction-type": words("mute-word"),
            "apply-type": words("apply-word"),
        }
        volunteer = ScriptedPrompter(hall, rules, scratch_order=list(SCRATCH_ORDER))
        return start(volunteer, services, WIZARD_LANGUAGE)
    finally:
        console.close()
        emulator.stop()


def _report_written(home: Path) -> bool:
    workspace = Workspace(home)
    sessions = [s for venue in workspace.venues() for s in workspace.sessions_of(venue)]
    return any((s / REPORT_DIR / "report.html").is_file() for s in sessions)


def checks(result: RunResult, home: Path, tr: Translator) -> list[tuple[str, bool]]:
    """What the self-check verifies, as (the outcome in words, passed)."""
    state = result.state
    finished = result.finished
    junction = bool(state.junction_measured)
    applied = state.applied_state == "applied"
    verified = state.verify_verdict is not None
    report = _report_written(home)
    return [
        (tr("selftest-what-wizard") if finished else tr("selftest-fail-wizard"), finished),
        (tr("selftest-what-junction") if junction else tr("selftest-fail-junction"), junction),
        (tr("selftest-what-apply") if applied else tr("selftest-fail-apply"), applied),
        (tr("selftest-what-verify") if verified else tr("selftest-fail-verify"), verified),
        (tr("selftest-what-report") if report else tr("selftest-fail-report"), report),
    ]


def selftest_command(
    lang: Annotated[str | None, typer.Option("--lang", help="en, ru or uk.")] = None,
    keep: Annotated[
        bool, typer.Option("--keep", help="Keep the self-check files and print where they are.")
    ] = False,
) -> None:
    """Check that ChurchTune works here: the whole wizard on a built-in emulator, no sound."""
    language = lang or detect_language()
    if language not in LANGUAGES:
        fail(Translator(detect_language())("err-unknown-language"))
    tr = Translator(language)
    os.environ[DEV_SAFE_VAR] = "1"  # this process never reaches a real console or sound card
    typer.echo(tr("selftest-start", {"version": __version__}))
    home = Path(tempfile.mkdtemp(prefix="ctune-selftest-"))
    began = time.monotonic()
    try:
        results = checks(_run_wizard(home), home, tr)
    except Exception as error:  # an installation problem: say so plainly, keep the details
        (home / ERROR_FILE).write_text(traceback.format_exc(), encoding="utf-8")
        typer.echo(tr("selftest-error", {"error": type(error).__name__}))
        results = [(tr("selftest-fail-wizard"), False)]
        keep = True
    for what, passed in results:
        if passed:
            typer.echo(tr("selftest-ok", {"what": what}))
        else:
            typer.echo(tr("selftest-failed", {"what": what}))
    if keep:
        typer.echo(tr("selftest-kept", {"path": str(home)}))
    else:
        shutil.rmtree(home, ignore_errors=True)
    if all(passed for _, passed in results):
        typer.echo(tr("selftest-passed", {"seconds": round(time.monotonic() - began)}))
        raise typer.Exit(EXIT_OK)
    typer.echo(tr("selftest-not-passed"))
    raise typer.Exit(EXIT_USER)
