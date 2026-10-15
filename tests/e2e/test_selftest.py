"""``ctune selftest``: the installed program runs the whole wizard on its built-in emulator."""

import tempfile

import pytest
from typer.testing import CliRunner

from churchtune.cli import selftest
from churchtune.cli.main import app
from churchtune.platform.devsafe import DEV_SAFE_VAR


def test_the_self_check_passes_and_leaves_nothing_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    result = CliRunner().invoke(app, ["--home", str(tmp_path / "home"), "selftest", "--lang", "en"])
    assert result.exit_code == 0, result.output
    assert result.output.count("✓") == 5
    assert "✗" not in result.output
    assert "installed correctly" in result.output
    assert not list(tmp_path.glob("ctune-selftest-*"))
    assert not (tmp_path / "home").exists()  # the person's own data folder is not touched


def test_a_broken_installation_says_so_and_keeps_the_files(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    def broken(_home):
        raise ModuleNotFoundError("scipy")

    monkeypatch.setattr(selftest, "_run_wizard", broken)
    result = CliRunner().invoke(app, ["selftest", "--lang", "uk"])
    assert result.exit_code == 1
    assert "ModuleNotFoundError" in result.output
    assert "✗" in result.output
    assert "issue" in result.output
    assert len(list(tmp_path.glob("ctune-selftest-*"))) == 1


def test_the_self_check_switches_dev_safe_on(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.delenv(DEV_SAFE_VAR, raising=False)
    seen = []

    def spy(_home):
        import os  # noqa: PLC0415 - read at call time

        seen.append(os.environ.get(DEV_SAFE_VAR))
        raise RuntimeError("stop here")

    monkeypatch.setattr(selftest, "_run_wizard", spy)
    CliRunner().invoke(app, ["selftest", "--lang", "en"])
    assert seen == ["1"]


@pytest.mark.parametrize("lang", ["de", "EN"])
def test_an_unknown_language_is_refused(lang):
    result = CliRunner().invoke(app, ["selftest", "--lang", lang])
    assert result.exit_code == 1
