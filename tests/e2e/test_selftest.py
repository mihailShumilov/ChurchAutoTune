"""``ctune selftest``: the installed program runs the whole wizard on its built-in emulator."""

import os
import tempfile

import pytest
from typer.testing import CliRunner

from churchtune.cli import selftest
from churchtune.cli.main import app
from churchtune.cli.scripted import ScriptedPrompter
from churchtune.errors import DevSafeViolation
from churchtune.platform.devsafe import AGENT_VAR, DEV_SAFE_VAR


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """Temporary folders in tmp_path; the dev-safe variable restored after the test."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv(DEV_SAFE_VAR, "1")
    return tmp_path


def test_the_self_check_passes_and_leaves_nothing_behind(scratch):
    result = CliRunner().invoke(app, ["--home", str(scratch / "home"), "selftest", "--lang", "en"])
    assert result.exit_code == 0, result.output
    assert result.output.count("✓") == 5
    assert "✗" not in result.output
    assert "installed and works" in result.output
    assert not list(scratch.glob("ctune-selftest-*"))
    assert not (scratch / "home").exists()  # the person's own data folder is not touched


def test_the_persons_settings_play_no_part(scratch, monkeypatch):
    home = scratch / "home"
    home.mkdir()
    (home / "venue.toml").write_text("broken = [", encoding="utf-8")
    monkeypatch.setenv("CTUNE_HOME", str(home))
    monkeypatch.setenv("CTUNE_CONSOLE", ":not-an-address")
    seen = []
    monkeypatch.setattr(selftest, "checks", lambda *_a: seen.append(1) or [])
    monkeypatch.setattr(selftest, "_run_wizard", lambda _home: None)
    result = CliRunner().invoke(app, ["selftest", "--lang", "en"])
    assert result.exit_code == 0, result.output
    assert seen == [1]


def test_a_broken_installation_says_so_and_keeps_the_details(scratch, monkeypatch):
    def broken(_home):
        raise ModuleNotFoundError("scipy")

    monkeypatch.setattr(selftest, "_run_wizard", broken)
    result = CliRunner().invoke(app, ["selftest", "--lang", "uk"])
    assert result.exit_code == 1
    assert "ModuleNotFoundError" in result.output
    assert "✗ Майстер не дійшов до кінця." in result.output
    assert "github.com/mihailShumilov/ChurchAutoTune/issues" in result.output
    [kept] = scratch.glob("ctune-selftest-*")
    assert "ModuleNotFoundError: scipy" in (kept / selftest.ERROR_FILE).read_text(encoding="utf-8")


def test_the_self_check_switches_dev_safe_on(scratch, monkeypatch):
    monkeypatch.delenv(DEV_SAFE_VAR)
    seen = []

    def spy(_home):
        seen.append(os.environ.get(DEV_SAFE_VAR))
        raise RuntimeError("stop here")

    monkeypatch.setattr(selftest, "_run_wizard", spy)
    CliRunner().invoke(app, ["selftest", "--lang", "en"])
    assert seen == ["1"]


def test_the_wizard_of_the_self_check_refuses_to_run_outside_dev_safe(monkeypatch, tmp_path):
    monkeypatch.delenv(DEV_SAFE_VAR, raising=False)
    monkeypatch.delenv(AGENT_VAR, raising=False)
    with pytest.raises(DevSafeViolation):
        selftest._run_wizard(tmp_path)
    with pytest.raises(DevSafeViolation):
        ScriptedPrompter(hall=None)  # type: ignore[arg-type]


def test_the_emulator_option_switches_dev_safe_on(monkeypatch, tmp_path):
    monkeypatch.delenv(DEV_SAFE_VAR, raising=False)
    CliRunner().invoke(app, ["--emulator", "--home", str(tmp_path), "report"])
    assert os.environ.get(DEV_SAFE_VAR) == "1"  # no discovery broadcast can find a real console


@pytest.mark.parametrize("lang", ["de", "EN"])
def test_an_unknown_language_is_refused(lang):
    result = CliRunner().invoke(app, ["selftest", "--lang", lang])
    assert result.exit_code == 1
    assert "en, ru" in result.output
