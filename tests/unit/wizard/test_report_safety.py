"""The report reads undo files and writes only its own files (safety review of M6)."""

import shlex
from datetime import UTC, datetime
from pathlib import Path

import pytest

from churchtune.cli.package_writes import describe_undo
from churchtune.domain.changes import WriteScope
from churchtune.storage import files
from churchtune.storage.models import ChangeModel, ResultModel, UndoFile
from churchtune.storage.paths import DEFAULT_HOME, Workspace
from churchtune.wizard import reporting
from churchtune.wizard.labels import undo_command
from churchtune.wizard.run import new_state


def change(path: str, old: float, new: float) -> ChangeModel:
    return ChangeModel(path=path, old_value=old, new_value=new, old_display=str(old),
                       new_display=str(new), scope=WriteScope.SYSTEM_EQ)  # fmt: skip


def result(path: str, wanted: float, sent: bool = True) -> ResultModel:
    return ResultModel(path=path, wanted=wanted, readback=wanted, node=None, ok=sent, sent=sent)


def undo_file(tmp_path: Path, changes, results) -> Path:
    path = tmp_path / "undo" / "undo_pkg_test.json"
    undo = UndoFile(
        created="2026-10-15T10:00:00",
        ip="127.0.0.1",
        scope=WriteScope.SYSTEM_EQ,
        changes=tuple(changes),
        results=tuple(results),
    )
    files.write_json(path, undo)
    return path


def test_the_undo_command_names_the_console_and_quotes_the_paths(tmp_path):
    home = tmp_path / "my data"
    undo = home / "undo" / "it's.json"
    words = shlex.split(undo_command(home, undo, "127.0.0.1"))
    assert words == ["ctune", "--home", str(home), "--console", "127.0.0.1", "undo", str(undo)]


def test_the_undo_command_in_the_default_folder_needs_no_home():
    home = DEFAULT_HOME.expanduser()
    command = undo_command(home, home / "undo" / "undo_pkg_1.json", None)
    assert command == "ctune undo ~/ChurchTune/undo/undo_pkg_1.json"


def test_the_description_lists_what_was_sent_once_per_setting(tmp_path):
    gain = "/main/st/eq/2/g"
    mute = "/mtx/03/mix/on"
    never = "/main/st/eq/3/g"
    path = undo_file(
        tmp_path,
        [
            change(gain, 0.5, 0.45),
            change(mute, 1, 0),  # a brief mute ...
            change(gain, 0.45, 0.4),
            change(mute, 0, 1),  # ... and back: not a change
            change(never, 0.5, 0.3),  # planned, never sent
            change("/not/a/console/path", 0, 1),
        ],
        [
            result(gain, 0.45),
            result(mute, 0),
            result(gain, 0.4),
            result(mute, 1),
            result(never, 0.3, sent=False),
            result("/not/a/console/path", 1),
        ],
    )
    rows = describe_undo(path)
    assert [r.path for r in rows] == [gain]
    assert rows[0].before == "0.5"
    assert rows[0].after == "0.4"


def test_a_damaged_undo_file_is_refused(tmp_path):
    path = tmp_path / "undo" / "broken.json"
    path.parent.mkdir()
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError, match=r".*"):
        describe_undo(path)


@pytest.mark.parametrize("name", ["../x.png", "a/b.png", "..", ".", ""])
def test_report_files_are_plain_names_in_the_report_folder(tmp_path, name):
    with pytest.raises(ValueError, match="plain file name"):
        files.write_report_files(tmp_path / "report", {name: b"x"})


def test_an_undo_file_outside_the_undo_folder_is_not_used(tmp_path):
    workspace = Workspace(tmp_path)
    outside = tmp_path / "elsewhere.json"
    outside.write_text("{}", encoding="utf-8")
    state = new_state("en", datetime(2026, 10, 15, tzinfo=UTC)).model_copy(
        update={"applied_state": "applied", "applied_undo": "../elsewhere.json"}
    )
    assert reporting._undo_path(workspace, state) is None
