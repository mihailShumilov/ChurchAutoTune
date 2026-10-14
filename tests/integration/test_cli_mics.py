"""M2 commands on the simulated rig: devices, mics map / calibrate, noise, pathcheck."""

from datetime import datetime

import pytest
import typer
from typer.testing import CliRunner

from churchtune.audio.fake.hall import FakeHall, FakeMic
from churchtune.cli.context import EXIT_OK, EXIT_USER
from churchtune.cli.main import app
from churchtune.cli.mics import parse_inputs
from churchtune.storage.files import reusable_mic_calibration
from churchtune.storage.paths import Workspace


def invoke(home, *argv, answers=()):
    return CliRunner().invoke(
        app,
        ["--home", str(home), "--audio", "fake", *argv],
        input="".join(f"{a}\n" for a in answers),
        catch_exceptions=False,
    )


def test_devices_in_dev_safe_lists_only_the_simulator(tmp_path):
    result = invoke(tmp_path, "devices")
    assert result.exit_code == EXIT_OK
    assert "fake" in result.output


def test_map_finds_four_mics(tmp_path):
    result = invoke(tmp_path, "mics", "map", "--count", "4", answers=[""] * 4)
    assert result.exit_code == EXIT_OK, result.output
    assert "--inputs 12,10,13,11" in result.output


def test_map_gives_up_when_nothing_is_heard(tmp_path):
    result = invoke(tmp_path, "mics", "map", "--count", "1", "--inputs", "20-24", answers=["", "n"])
    assert result.exit_code == EXIT_USER
    assert "ничего не услышал" in result.output


def test_map_refuses_too_many_mics(tmp_path):
    assert invoke(tmp_path, "mics", "map", "--count", "9").exit_code == EXIT_USER


def test_calibrate_saves_and_reuses(tmp_path):
    first = invoke(tmp_path, "mics", "calibrate", "--inputs", "10-13", answers=["y"])
    assert first.exit_code == EXIT_OK, first.output
    assert "calibration.json" in first.output
    second = invoke(tmp_path, "mics", "calibrate", "--inputs", "10-13")
    assert second.exit_code == EXIT_OK
    assert "уже откалиброван" in second.output


def test_calibrate_needs_two_mics_and_a_confirmation(tmp_path):
    assert invoke(tmp_path, "mics", "calibrate", "--inputs", "10").exit_code == EXIT_USER
    assert (
        invoke(tmp_path, "mics", "calibrate", "--inputs", "10,11", answers=["n"]).exit_code
        == EXIT_USER
    )


def test_noise_lists_every_mic(tmp_path):
    noise = invoke(tmp_path, "noise", "--inputs", "10,11")
    assert noise.exit_code == EXIT_OK
    assert "USB in 11" in noise.output


def test_pathcheck_hears_the_rig(tmp_path):
    assert invoke(tmp_path, "pathcheck", "--inputs", "10,11", answers=["y"]).exit_code == EXIT_OK


def test_pathcheck_reports_an_empty_input(tmp_path):
    missing = invoke(tmp_path, "pathcheck", "--inputs", "10,20", answers=["y"])
    assert missing.exit_code == EXIT_USER
    assert "не слышно" in missing.output


def test_parse_inputs():
    assert parse_inputs("10,12-14") == [10, 12, 13, 14]
    with pytest.raises(typer.BadParameter):
        parse_inputs("ten")


@pytest.fixture
def calibrated(tmp_path):
    invoke(tmp_path, "mics", "calibrate", "--inputs", "10-13", answers=["y"])
    return Workspace(tmp_path), next((tmp_path / "mics").iterdir()).name


def test_a_recent_calibration_is_reused(calibrated):
    workspace, set_id = calibrated
    assert reusable_mic_calibration(workspace, set_id, datetime.now()) is not None


def test_an_old_or_future_calibration_is_not_reused(calibrated):
    workspace, set_id = calibrated
    assert reusable_mic_calibration(workspace, set_id, datetime(2099, 1, 1)) is None
    assert reusable_mic_calibration(workspace, set_id, datetime(2000, 1, 1)) is None


def test_an_unknown_set_has_no_calibration(tmp_path):
    assert reusable_mic_calibration(Workspace(tmp_path), "abc123", datetime.now()) is None


def test_a_set_id_must_be_alphanumeric(tmp_path):
    with pytest.raises(ValueError, match="alphanumeric"):
        Workspace(tmp_path).mic_calibration_file("../x")


def test_a_pair_with_one_inverted_mic_fails_and_is_not_reused(tmp_path, monkeypatch):
    def rig(fs_hz):
        hall = FakeHall(fs_hz, {10: FakeMic(), 11: FakeMic(inverted=True)}, gain_db=-6.0)
        hall.place(10, "p1")
        hall.place(11, "p2")
        return hall

    monkeypatch.setattr("churchtune.audio.factory.demo_rig", rig)
    first = invoke(tmp_path, "mics", "calibrate", "--inputs", "10,11", answers=["y"])
    assert first.exit_code == EXIT_USER, first.output
    second = invoke(tmp_path, "mics", "calibrate", "--inputs", "10,11", answers=["n"])
    assert "уже откалиброван" not in second.output
