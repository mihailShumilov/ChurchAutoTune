"""Lifting stored files to the current schema version.

Version 0 is the file shape of the original single-hall tool, which had no version field.
"""

from collections.abc import Callable, Mapping
from typing import Any

from churchtune.errors import UnsupportedSchema
from churchtune.storage.models import SCHEMA_VERSION

LEGACY_CODE = "legacy"
Raw = dict[str, Any]


def _legacy_text(text: object) -> Raw:
    return {"code": LEGACY_CODE, "params": {"text": str(text)}}


def _session_v0(data: Raw) -> Raw:
    return dict(data)


def _point_meta_v0(data: Raw) -> Raw:
    migrated = dict(data)
    migrated["warnings"] = [_legacy_text(w) for w in data.get("warnings", [])]
    return migrated


def _junction_meta_v0(data: Raw) -> Raw:
    return dict(data)


def _undo_v0(data: Raw) -> Raw:
    migrated = dict(data)
    migrated.setdefault("scope", "main")
    migrated.setdefault("created", "")
    return migrated


def _pending_v0(data: Raw) -> Raw:
    return dict(data)


def _proposal_v0(data: Raw) -> Raw:
    measured = {b["band"]: b for b in data["eq_at_measurement"]["bands"]}
    bands = []
    for band in data["bands"]:
        entry = {k: v for k, v in band.items() if k != "now"}
        entry["was"] = measured[band["band"]]
        entry["reason"] = _legacy_text(band.get("reason", ""))
        bands.append(entry)
    skipped = [{**s, "reason": _legacy_text(s.get("reason", ""))} for s in data.get("skipped", [])]
    target = {k: v for k, v in data.get("target", {}).items() if k != "description"}
    migrated = {k: v for k, v in data.items() if k != "note"}
    migrated.update(bands=bands, skipped=skipped, target=target)
    migrated.setdefault("method", "greedy")
    return migrated


def _mic_calibration_v0(data: Raw) -> Raw:
    """Calibration files appeared with schema version 1; a file without a version is not ours."""
    raise UnsupportedSchema(
        f"a microphone calibration file without schema_version: {sorted(data)[:5]}"
    )


def _wizard_v0(data: Raw) -> Raw:
    """Wizard state files appeared with schema version 1; a file without a version is not ours."""
    raise UnsupportedSchema(f"a wizard state file without schema_version: {sorted(data)[:5]}")


def _snapshot_v0(data: Raw) -> Raw:
    """Snapshot files appeared with schema version 1; a file without a version is not ours."""
    raise UnsupportedSchema(f"a snapshot file without schema_version: {sorted(data)[:5]}")


def _packages_v0(data: Raw) -> Raw:
    """Package files appeared with schema version 1; a file without a version is not ours."""
    raise UnsupportedSchema(f"a proposal packages file without schema_version: {sorted(data)[:5]}")


def _plan_v0(data: Raw) -> Raw:
    """Point plans appeared with schema version 1; a file without a version is not ours."""
    raise UnsupportedSchema(f"a point plan file without schema_version: {sorted(data)[:5]}")


_FROM_V0: Mapping[str, Callable[[Raw], Raw]] = {
    "packages": _packages_v0,
    "plan": _plan_v0,
    "snapshot": _snapshot_v0,
    "mic_calibration": _mic_calibration_v0,
    "wizard": _wizard_v0,
    "session": _session_v0,
    "point_meta": _point_meta_v0,
    "junction_meta": _junction_meta_v0,
    "undo": _undo_v0,
    "pending": _pending_v0,
    "proposal": _proposal_v0,
}


def migrate(kind: str, data: Raw) -> Raw:
    """Return ``data`` in the current schema version.

    Args:
        kind: Which file this is: ``session``, ``point_meta``, ``junction_meta``, ``undo``,
            ``pending``, ``proposal``, ``snapshot``, ``mic_calibration``, ``wizard``,
            ``packages`` or ``plan``.
        data: The parsed JSON object.

    Returns:
        A new dict with ``schema_version`` set to the current version.

    Raises:
        UnsupportedSchema: The version is newer than this program knows, or not a number.
        KeyError: ``kind`` is unknown.
    """
    version = data.get("schema_version", 0)
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or not 0 <= version <= SCHEMA_VERSION
    ):
        raise UnsupportedSchema(f"unsupported schema_version {version!r} in a {kind} file")
    migrated = dict(data)
    if version == 0:
        migrated = _FROM_V0[kind](migrated)
    migrated["schema_version"] = SCHEMA_VERSION
    return migrated
