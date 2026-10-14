"""Where ChurchTune keeps its files.

M0 keeps the flat layout of the original tool under one home directory; the venue / session
layout of SPEC §6.4 arrives with the wizard in M3.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from churchtune.domain.changes import WriteScope

HOME_VAR = "CTUNE_HOME"
DEFAULT_HOME = Path("~/ChurchTune")
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
SNAPSHOT_SUFFIX = "-snapshot.json"


@dataclass(frozen=True, slots=True)
class Workspace:
    """The home directory and the places inside it.

    Attributes:
        root: The home directory.
    """

    root: Path

    @property
    def measurements(self) -> Path:
        """Measurement sessions, one directory each."""
        return self.root / "measurements"

    @property
    def results(self) -> Path:
        """Reports and plots, one directory per session."""
        return self.root / "results"

    @property
    def backups(self) -> Path:
        """Read-only console dumps."""
        return self.root / "backups"

    @property
    def undo(self) -> Path:
        """Undo files."""
        return self.root / "undo"

    @property
    def proposal_file(self) -> Path:
        """The latest EQ proposal."""
        return self.root / "eq_proposal.json"

    @property
    def pending_mutes_file(self) -> Path:
        """Present while temporary matrix mutes have not been restored."""
        return self.root / "sub_mutes_pending.json"

    @property
    def mic_cal_file(self) -> Path:
        """Optional microphone calibration: ``freq dB`` per line."""
        return self.root / "mic_cal.txt"

    @property
    def console_dir(self) -> Path:
        """Console snapshots. M3 moves them under ``venues/<slug>/console`` (SPEC §6.4)."""
        return self.root / "console"

    def snapshot_path(self, now: datetime) -> Path:
        """Name of a new snapshot file taken at ``now``."""
        return self.console_dir / f"{now.strftime(TIMESTAMP_FORMAT)}{SNAPSHOT_SUFFIX}"

    def undo_path(self, scope: WriteScope, now: datetime, *, revert: bool = False) -> Path:
        """Name of a new undo file for a plan applied at ``now``."""
        prefix = "undo_sub_" if scope is WriteScope.SUB else "undo_"
        suffix = "_revert" if revert else ""
        return self.undo / f"{prefix}{now.strftime(TIMESTAMP_FORMAT)}{suffix}.json"


def workspace_from_env(
    home: Path | None = None, environ: Mapping[str, str] | None = None
) -> Workspace:
    """Resolve the home directory: an explicit path, else ``CTUNE_HOME``, else ``~/ChurchTune``."""
    env = os.environ if environ is None else environ
    if home is not None:
        return Workspace(home.expanduser())
    from_env = env.get(HOME_VAR, "").strip()
    return Workspace(Path(from_env).expanduser() if from_env else DEFAULT_HOME.expanduser())
