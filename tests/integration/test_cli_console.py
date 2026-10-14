"""``ctune doctor``, ``ctune snapshot`` and ``ctune emulator`` against the emulator (SPEC M1)."""

import pytest
from typer.testing import CliRunner

from churchtune.cli.context import EXIT_OK, EXIT_UNREACHABLE
from churchtune.cli.main import app
from churchtune.mixers.x32.snapshot import parse_snapshot
from churchtune.storage.files import latest_snapshot, read_snapshot
from churchtune.storage.models import SnapshotFile
from churchtune.storage.paths import Workspace
from churchtune_emulator.x32 import X32Emulator
from tests.emulator.x32_emulator import LOOPBACK
from tests.fixtures.topologies import all_fixtures


def invoke(*argv, home):
    return CliRunner().invoke(app, ["--home", str(home), *argv], catch_exceptions=False)


def test_snapshot_saves_a_file_that_parses_back(emu, tmp_path):
    result = invoke("--console", f"{LOOPBACK}:{emu.port}", "snapshot", home=tmp_path)
    assert result.exit_code == EXIT_OK, result.output
    assert "снимок" in result.output
    path = latest_snapshot(Workspace(tmp_path))
    assert path is not None
    stored = read_snapshot(path)
    assert stored.model == "X32P"
    snapshot = parse_snapshot(stored.identity(), stored.raw, stored.taken_at)
    assert snapshot.matrices[3].name == "Sub buffer"
    assert SnapshotFile.from_snapshot(snapshot) == stored
    assert emu.writes == []


def test_doctor_on_the_in_process_emulator(tmp_path):
    result = invoke("--emulator", "doctor", home=tmp_path)
    assert result.exit_code == EXIT_OK, result.output
    out = result.output
    for text in ("X32P", "прошивка 4.09", "связь", "Sub buffer", "портал левый", "Вариант"):
        assert text in out
    assert "USB in 10" in out
    assert "только подсказки" in out
    assert latest_snapshot(Workspace(tmp_path)) is not None


def test_doctor_from_a_saved_snapshot(emu, tmp_path):
    saved = invoke("--console", f"{LOOPBACK}:{emu.port}", "snapshot", home=tmp_path)
    assert saved.exit_code == EXIT_OK
    path = latest_snapshot(Workspace(tmp_path))
    result = invoke("doctor", "--from-file", str(path), home=tmp_path)
    assert result.exit_code == EXIT_OK
    assert "саб" in result.output


@pytest.mark.parametrize(
    "fixture",
    [f for f in all_fixtures() if f.name in ("sub-on-mono", "m32-ukrainian-names")],
    ids=lambda f: f.name,
)
def test_doctor_explains_suggest_only_layouts(fixture, tmp_path):
    emu = X32Emulator(raw=fixture.raw, model=fixture.model).start()
    try:
        result = invoke("--console", f"{LOOPBACK}:{emu.port}", "doctor", home=tmp_path)
    finally:
        emu.stop()
    assert result.exit_code == EXIT_OK, result.output
    if fixture.model.startswith("M32"):
        assert "программа может записывать: ничего" in result.output
    else:
        assert "Main Mono" in result.output


def test_doctor_without_a_console_in_dev_safe_explains_and_exits_2(tmp_path):
    result = invoke("doctor", home=tmp_path)
    assert result.exit_code == EXIT_UNREACHABLE
    assert "--emulator" in result.output


def test_doctor_with_a_silent_console_exits_2(tmp_path):
    emu = X32Emulator().start()
    port = emu.port
    emu.stop()
    result = invoke("--console", f"{LOOPBACK}:{port}", "doctor", home=tmp_path)
    assert result.exit_code == EXIT_UNREACHABLE


def test_snapshot_with_a_silent_console_exits_2(tmp_path):
    emu = X32Emulator().start()
    port = emu.port
    emu.stop()
    result = invoke("--console", f"{LOOPBACK}:{port}", "snapshot", home=tmp_path)
    assert result.exit_code == EXIT_UNREACHABLE


def test_emulator_command_serves_until_interrupted(monkeypatch, tmp_path):
    served = []

    def fake_serve(self):
        served.append(self.port)
        self.sock.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(X32Emulator, "serve_forever", fake_serve)
    result = invoke("emulator", "--model", "M32", "--latency-ms", "5", home=tmp_path)
    assert result.exit_code == EXIT_OK
    assert "эмулятор M32 на 127.0.0.1:" in result.output
    assert served
