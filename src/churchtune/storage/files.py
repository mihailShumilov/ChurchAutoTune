"""Reading and writing ChurchTune's files: sessions, points, undo files, proposals.

JSON files are written atomically and flushed to disk, because an undo file must exist before
the first console write.
"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import soundfile

from churchtune.domain.arrays import FloatArray
from churchtune.storage.migrations import migrate
from churchtune.storage.models import (
    FileModel,
    JunctionMeta,
    PendingMutes,
    PointMeta,
    ProposalFile,
    SessionFile,
    SnapshotFile,
    UndoFile,
)
from churchtune.storage.paths import SNAPSHOT_SUFFIX, Workspace

SESSION_FILE = "session.json"
META_FILE = "meta.json"
IR_FILE = "ir.wav"
RECORDING_FILE = "recording.wav"
FR_FILE = "fr.csv"
JUNCTION_BLOCKS = ("sub", "tops", "all")
EQ_CHANGED_WARNING = "EQ changed during session"


def write_json(path: Path, model: FileModel) -> None:
    """Write a model as JSON: to a temporary file, flushed to disk, then renamed into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=False)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    _sync_directory(path.parent)


def _sync_directory(directory: Path) -> None:
    """Flush the directory entry, so the renamed file survives a power cut where supported."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass  # some file systems do not support fsync on a directory
    finally:
        os.close(descriptor)


def _read_json(path: Path, kind: str) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not hold a JSON object")  # noqa: TRY004 - a content error
    return migrate(kind, data)


# ------------------------------------------------------------ sessions
@dataclass(frozen=True, slots=True)
class SessionStatus:
    """Outcome of opening a session for measuring.

    Attributes:
        path: The session directory.
        created: True when the session did not exist before.
        eq_changed: True when the session exists and was started under a different EQ.
    """

    path: Path
    created: bool
    eq_changed: bool


def read_session(session_dir: Path) -> SessionFile:
    """Read ``session.json`` of a session directory."""
    return SessionFile.model_validate(_read_json(session_dir / SESSION_FILE, "session"))


def ensure_session(workspace: Workspace, session: SessionFile, now_text: str) -> SessionStatus:
    """Create the session, or check an existing one against the EQ measured with now.

    Measurements of one session must be taken under one EQ. When the session exists and its
    EQ differs from ``session.eq_at_measurement`` (and both are known), a warning with the
    time is added to its file.

    Args:
        workspace: Where sessions live.
        session: The session as it would be created.
        now_text: Current time for the warning.
    """
    path = workspace.measurements / session.name
    path.mkdir(parents=True, exist_ok=True)
    if not (path / SESSION_FILE).exists():
        write_json(path / SESSION_FILE, session)
        return SessionStatus(path, created=True, eq_changed=False)
    stored = read_session(path)
    changed = (
        session.eq_at_measurement is not None
        and stored.eq_at_measurement is not None
        and stored.eq_at_measurement != session.eq_at_measurement
    )
    if changed:
        warnings = (*stored.warnings, f"{EQ_CHANGED_WARNING} at {now_text}")
        write_json(path / SESSION_FILE, stored.model_copy(update={"warnings": warnings}))
    return SessionStatus(path, created=False, eq_changed=changed)


def list_sessions(workspace: Workspace) -> list[Path]:
    """Session directories, oldest first by their creation time."""
    if not workspace.measurements.exists():
        return []
    sessions = [p for p in workspace.measurements.iterdir() if (p / SESSION_FILE).exists()]
    return sorted(sessions, key=lambda p: read_session(p).created)


def latest_session(workspace: Workspace, kind: str = "baseline") -> Path | None:
    """The newest session of a kind, or None."""
    for path in reversed(list_sessions(workspace)):
        if read_session(path).kind == kind:
            return path
    return None


# ------------------------------------------------------------ points
@dataclass(frozen=True, slots=True)
class StoredPoint:
    """A measured point as stored.

    Attributes:
        ir: Impulse response, ``float64[n]``.
        fs_hz: Its sample rate.
        meta: Quality numbers, or None when ``meta.json`` is missing.
    """

    ir: FloatArray
    fs_hz: float
    meta: PointMeta | None


def write_wav(path: Path, samples: FloatArray, fs_hz: float) -> None:
    """Write mono samples as a 32-bit float WAV file."""
    soundfile.write(str(path), np.asarray(samples, dtype=np.float32), round(fs_hz), subtype="FLOAT")


def read_wav(path: Path) -> tuple[FloatArray, float]:
    """Read a WAV file as ``(float64 samples, sample rate)``."""
    samples, fs_hz = soundfile.read(str(path), dtype="float64")
    return np.asarray(samples, dtype=np.float64), float(fs_hz)


def save_point(  # noqa: PLR0913 - one point is these files
    session_dir: Path,
    meta: PointMeta,
    *,
    recording: FloatArray,
    ir: FloatArray,
    fs_hz: float,
    grid_hz: FloatArray,
    response_db: FloatArray,
) -> Path:
    """Store one measured point: recording, impulse response, raw response and quality."""
    directory = session_dir / meta.point
    directory.mkdir(parents=True, exist_ok=True)
    write_wav(directory / RECORDING_FILE, recording, fs_hz)
    write_wav(directory / IR_FILE, ir, fs_hz)
    np.savetxt(
        directory / FR_FILE,
        np.column_stack([grid_hz, response_db]),
        delimiter=",",
        fmt="%.3f",
        header="freq_hz,level_db_1_6oct_raw",
        comments="",
    )
    write_json(directory / META_FILE, meta)
    return directory


def point_names(session_dir: Path) -> list[str]:
    """Names of the points of a session that have an impulse response, sorted."""
    return sorted(p.name for p in session_dir.iterdir() if p.is_dir() and (p / IR_FILE).exists())


def read_point_meta(point_dir: Path) -> PointMeta | None:
    """Read ``meta.json`` of a point, or None when it is missing."""
    if not (point_dir / META_FILE).exists():
        return None
    return PointMeta.model_validate(_read_json(point_dir / META_FILE, "point_meta"))


def load_points(session_dir: Path) -> dict[str, StoredPoint]:
    """Load every point of a session that has an impulse response."""
    points = {}
    for name in point_names(session_dir):
        ir, fs_hz = read_wav(session_dir / name / IR_FILE)
        points[name] = StoredPoint(ir, fs_hz, read_point_meta(session_dir / name))
    return points


# ------------------------------------------------------------ junction points
@dataclass(frozen=True, slots=True)
class StoredJunctionPoint:
    """A sub / top junction point as stored: three responses on one time base."""

    sub: FloatArray
    tops: FloatArray
    both: FloatArray
    fs_hz: float
    meta: JunctionMeta


def save_junction_point(
    session_dir: Path,
    meta: JunctionMeta,
    irs: tuple[FloatArray, FloatArray, FloatArray],
    fs_hz: float,
) -> Path:
    """Store the first half of each block's circular response (the causal part) and the meta."""
    directory = session_dir / meta.point
    directory.mkdir(parents=True, exist_ok=True)
    half = len(irs[0]) // 2
    for name, ir in zip(JUNCTION_BLOCKS, irs, strict=True):
        write_wav(directory / f"ir_{name}.wav", ir[:half], fs_hz)
    write_json(directory / META_FILE, meta)
    return directory


def load_junction_points(session_dir: Path) -> dict[str, StoredJunctionPoint]:
    """Load every junction point of a session."""
    points = {}
    directories = sorted(p for p in session_dir.iterdir() if (p / "ir_sub.wav").exists())
    for directory in directories:
        responses = [read_wav(directory / f"ir_{name}.wav") for name in JUNCTION_BLOCKS]
        meta = JunctionMeta.model_validate(_read_json(directory / META_FILE, "junction_meta"))
        points[directory.name] = StoredJunctionPoint(
            responses[0][0], responses[1][0], responses[2][0], responses[0][1], meta
        )
    return points


# ------------------------------------------------------------ microphone calibration
def load_mic_cal(path: Path) -> tuple[FloatArray, FloatArray] | None:
    """Read a calibration file with ``freq dB`` per line (spaces or commas).

    Returns:
        ``(freqs_hz, response_db)``, or None when the file is missing or holds no numbers.
        Lines that are not two numbers are skipped.
    """
    if not path.exists():
        return None
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.replace(",", " ").split()
        try:
            rows.append((float(parts[0]), float(parts[1])))
        except (ValueError, IndexError):
            continue
    if not rows:
        return None
    table = np.array(rows, dtype=np.float64)
    return table[:, 0], table[:, 1]


# ------------------------------------------------------------ undo, pending mutes, proposals
def read_undo(path: Path) -> UndoFile:
    """Read an undo file (any supported version).

    Raises:
        ValueError: The file is not valid (pydantic's ``ValidationError`` is a ``ValueError``).
    """
    return UndoFile.model_validate(_read_json(path, "undo"))


def write_pending_mutes(workspace: Workspace, pending: PendingMutes) -> None:
    """Record the matrix switch states to restore, before the first temporary mute."""
    write_json(workspace.pending_mutes_file, pending)


def read_pending_mutes(workspace: Workspace) -> PendingMutes | None:
    """The switch states waiting to be restored, or None."""
    path = workspace.pending_mutes_file
    if not path.exists():
        return None
    return PendingMutes.model_validate(_read_json(path, "pending"))


def clear_pending_mutes(workspace: Workspace) -> None:
    """Remove the pending file once every switch was restored and read back."""
    workspace.pending_mutes_file.unlink(missing_ok=True)


def read_proposal(path: Path) -> ProposalFile:
    """Read a proposal file (any supported version)."""
    return ProposalFile.model_validate(_read_json(path, "proposal"))


def session_levels(session_dir: Path) -> Mapping[str, PointMeta]:
    """Meta of every point of a session that has one."""
    metas = {}
    for name in point_names(session_dir):
        meta = read_point_meta(session_dir / name)
        if meta is not None:
            metas[name] = meta
    return metas


# ------------------------------------------------------------ console snapshots
def write_snapshot(workspace: Workspace, snapshot: SnapshotFile, now: datetime) -> Path:
    """Store a snapshot under a new time-stamped name and return its path."""
    path = workspace.snapshot_path(now)
    write_json(path, snapshot)
    return path


def read_snapshot(path: Path) -> SnapshotFile:
    """Read a snapshot file.

    Raises:
        ValueError: The file is not a valid snapshot file.
    """
    return SnapshotFile.model_validate(_read_json(path, "snapshot"))


def latest_snapshot(workspace: Workspace) -> Path | None:
    """The newest snapshot file, or None."""
    files = sorted(workspace.console_dir.glob(f"*{SNAPSHOT_SUFFIX}"))
    return files[-1] if files else None
