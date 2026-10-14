"""Names of the files in the home directory."""

from datetime import datetime

from churchtune.storage.paths import Workspace


def test_a_new_undo_file_never_takes_the_name_of_an_existing_one(tmp_path):
    workspace = Workspace(tmp_path)
    now = datetime(2026, 10, 14, 10, 0, 0)
    first = workspace.new_undo_path(None, now)
    assert first.name.startswith("undo_pkg_")
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("{}", encoding="utf-8")
    second = workspace.new_undo_path(None, now)
    assert second != first
    assert second.stem.endswith("_2")
    assert workspace.new_undo_path(None, now, revert=True).stem.endswith("_revert")
