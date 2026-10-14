"""Console snapshot files: versioned, written atomically, newest first."""

import json
from datetime import datetime

import pytest

from churchtune.errors import UnsupportedSchema
from churchtune.storage.files import latest_snapshot, read_snapshot, write_snapshot
from churchtune.storage.models import SnapshotFile
from churchtune.storage.paths import Workspace

STORED = SnapshotFile(
    taken_at="2026-10-14T09:00:00",
    family="x32",
    model="X32P",
    name="Hall",
    firmware="4.09",
    host="127.0.0.1",
    raw={"mtx/03/mix": "/mtx/03/mix ON -37.5"},
)


def test_write_read_and_latest(tmp_path):
    workspace = Workspace(tmp_path)
    assert latest_snapshot(workspace) is None
    first = write_snapshot(workspace, STORED, datetime(2026, 10, 14, 9, 0, 0))
    second = write_snapshot(workspace, STORED, datetime(2026, 10, 14, 9, 5, 0))
    assert first.name == "20261014_090000-snapshot.json"
    assert latest_snapshot(workspace) == second
    assert read_snapshot(second) == STORED


@pytest.mark.parametrize("version", [None, 2, "1"])
def test_unknown_versions_are_refused(tmp_path, version):
    data = STORED.model_dump(mode="json")
    if version is None:
        del data["schema_version"]
    else:
        data["schema_version"] = version
    path = tmp_path / "x-snapshot.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(UnsupportedSchema):
        read_snapshot(path)
