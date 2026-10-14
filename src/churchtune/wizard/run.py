"""Starting and resuming a wizard session (SPEC §6.2: ``ctune`` and ``ctune resume``)."""

from datetime import datetime
from pathlib import Path

from churchtune.storage.files import read_wizard_state, write_wizard_state
from churchtune.storage.models import WizardStateFile
from churchtune.wizard.engine import Engine, RunResult
from churchtune.wizard.protocol import Prompter
from churchtune.wizard.services import Services, WizardContext
from churchtune.wizard.state import StateBox
from churchtune.wizard.steps import all_steps

PENDING_SESSION = "unsaved"


def new_state(lang: str, now: datetime) -> WizardStateFile:
    """The state before step S0 has chosen a venue."""
    return WizardStateFile(
        venue="", venue_name="", lang=lang, created=now.isoformat(timespec="seconds")
    )


def run_wizard(
    prompter: Prompter, services: Services, state: WizardStateFile, session_dir: Path
) -> RunResult:
    """Run the wizard from ``state``; the state is saved in ``session_dir`` after every step."""
    ctx = WizardContext(prompter, StateBox(state), services, session_dir)

    def save(current: WizardStateFile) -> None:
        write_wizard_state(ctx.session_dir, current)

    return Engine(all_steps(), ctx, save).run()


def start(prompter: Prompter, services: Services, lang: str) -> RunResult:
    """A new session; step S0 picks the venue and the session directory."""
    state = new_state(lang, services.now())
    return run_wizard(prompter, services, state, services.workspace.root / PENDING_SESSION)


def resume(prompter: Prompter, services: Services, session_dir: Path) -> RunResult:
    """Continue a saved session after its last completed step.

    Raises:
        ValueError: ``session_dir`` holds no valid wizard state.
    """
    return run_wizard(prompter, services, read_wizard_state(session_dir), session_dir)


def latest_session(services: Services) -> Path | None:
    """The newest wizard session of any venue, or None."""
    workspace = services.workspace
    sessions = [s for venue in workspace.venues() for s in workspace.sessions_of(venue)]
    return max(sessions, key=lambda p: p.name, default=None)
