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

WIZARD_FILE = "session.json"  # the wizard state inside a session directory

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

    def venue_dir(self, slug: str) -> Path:
        """Directory of one venue (SPEC §6.4)."""
        if not slug or not all(c.isalnum() or c == "-" for c in slug):
            raise ValueError(f"a venue slug has letters, digits and dashes, got {slug!r}")
        return self.root / "venues" / slug

    def session_dir(self, slug: str, now: datetime, kind: str) -> Path:
        """A new wizard session directory of a venue."""
        return self.venue_dir(slug) / "sessions" / f"{now.strftime(TIMESTAMP_FORMAT)}-{kind}"

    def sessions_of(self, slug: str) -> list[Path]:
        """Session directories of a venue, oldest first."""
        root = self.venue_dir(slug) / "sessions"
        return sorted(p for p in root.glob("*") if (p / WIZARD_FILE).exists())

    def venues(self) -> list[str]:
        """Slugs of the known venues."""
        root = self.root / "venues"
        return sorted(p.name for p in root.glob("*") if p.is_dir())

    def mic_calibration_file(self, set_id: str) -> Path:
        """Calibration of one microphone set (SPEC §6.4)."""
        if not set_id.isalnum():
            raise ValueError(f"a microphone set id is alphanumeric, got {set_id!r}")
        return self.root / "mics" / set_id / "calibration.json"

    @property
    def console_dir(self) -> Path:
        """Console snapshots. M3 moves them under ``venues/<slug>/console`` (SPEC §6.4)."""
        return self.root / "console"

    def snapshot_path(self, now: datetime, venue: str | None = None) -> Path:
        """Name of a new snapshot file taken at ``now``; never the name of an existing file.

        With a venue the file goes to ``venues/<slug>/console`` (SPEC §6.4).
        """
        directory = self.venue_dir(venue) / "console" if venue else self.console_dir
        stamp = now.strftime(TIMESTAMP_FORMAT)
        path = directory / f"{stamp}{SNAPSHOT_SUFFIX}"
        number = 2
        while path.exists():
            path = directory / f"{stamp}_{number}{SNAPSHOT_SUFFIX}"
            number += 1
        return path

    def undo_path(self, scope: WriteScope | None, now: datetime, *, revert: bool = False) -> Path:
        """Name of a new undo file for a plan applied at ``now`` (``None``: applied packages)."""
        prefix = {WriteScope.SUB: "undo_sub_", None: "undo_pkg_"}.get(scope, "undo_")
        suffix = "_revert" if revert else ""
        return self.undo / f"{prefix}{now.strftime(TIMESTAMP_FORMAT)}{suffix}.json"

    def new_undo_path(
        self, scope: WriteScope | None, now: datetime, *, revert: bool = False
    ) -> Path:
        """Like :meth:`undo_path`, never the name of a file that exists (``_2``, ``_3``, ...)."""
        path = candidate = self.undo_path(scope, now, revert=revert)
        counter = 1
        while candidate.exists():
            counter += 1
            candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        return candidate


def workspace_from_env(
    home: Path | None = None, environ: Mapping[str, str] | None = None
) -> Workspace:
    """Resolve the home directory: an explicit path, else ``CTUNE_HOME``, else ``~/ChurchTune``."""
    env = os.environ if environ is None else environ
    if home is not None:
        return Workspace(home.expanduser())
    from_env = env.get(HOME_VAR, "").strip()
    return Workspace(Path(from_env).expanduser() if from_env else DEFAULT_HOME.expanduser())
