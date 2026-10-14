"""``apply-packages`` and its undo end to end against the emulator (SPEC S12, ADR 0010).

Never weaken, skip or xfail these tests: they are part of the safety suite.
"""

import json
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from churchtune.cli.context import EXIT_OK, EXIT_REFUSED, EXIT_USER
from churchtune.cli.main import app
from churchtune.domain.eq import EqType, SystemEq
from churchtune.mixers.x32.nodes import read_console_state
from churchtune.storage import files
from churchtune.storage.models import (
    EqModel,
    LevelModel,
    PackageModel,
    PackagesFile,
    PathEqModel,
    PathRefModel,
)
from tests.emulator.x32_emulator import LOOPBACK
from tests.fixtures.venues import REFERENCE_VENUE_TOML, SUB_MATRIX

WRITE_PREFIXES = ("/main/st/eq/", f"/mtx/{SUB_MATRIX:02d}/", "/mtx/01/", "/mtx/02/")


@pytest.fixture
def home(tmp_path):
    (tmp_path / "venue.toml").write_text(REFERENCE_VENUE_TOML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def run(emu, home):
    def _run(argv, answers=()):
        return CliRunner().invoke(
            app,
            ["--console", f"{LOOPBACK}:{emu.port}", "--audio", "fake", "--home", str(home), *argv],
            input="".join(f"{a}\n" for a in answers),
            catch_exceptions=False,
        )

    return _run


def cut(eq: SystemEq) -> SystemEq:
    """A mid cut on band 3, like a system EQ package would propose."""
    return eq.with_band(replace(eq.band(3), type=EqType.PEQ, freq_hz=480.0, gain_db=-6.0, q=1.5))


def write_proposal(home, client, *, eq=True, level_db=None, **extra) -> str:
    state = read_console_state(client)
    packages = []
    if eq:
        path_eq = PathEqModel(
            path=PathRefModel(kind="main_l"),
            before=EqModel.from_domain(state.main_eq),
            after=EqModel.from_domain(cut(state.main_eq)),
        )
        packages.append(
            PackageModel(
                **{
                    "kind": "system_eq",
                    "suggest_only": False,
                    "risk": "low",
                    "selected": True,
                    "eq": (path_eq,),
                    **extra,
                }
            )
        )
    if level_db is not None:
        before = state.matrices[SUB_MATRIX].fader_db
        level = LevelModel(
            path=PathRefModel(kind="matrix", number=SUB_MATRIX),
            before_db=before,
            after_db=before + level_db,
        )
        packages.append(
            PackageModel(
                kind="sub_level", suggest_only=False, risk="low", selected=True, levels=(level,)
            )
        )
    stored = PackagesFile(
        created="2026-10-14T10:00:00", session="s", fc_hz=100.0, packages=tuple(packages)
    )
    path = home / "proposal.json"
    files.write_json(path, stored)
    return str(path)


def undo_files(home):
    return sorted((home / "undo").glob("undo_pkg_*.json"))


def test_apply_then_undo_roundtrip(emu, home, run, client):
    original = emu.main_eq()
    original_fader = read_console_state(client).matrices[SUB_MATRIX].fader_db
    proposal = write_proposal(home, client, level_db=-3.0)
    result = run(["apply-packages", proposal], answers=["применить"])
    assert result.exit_code == EXIT_OK, result.output
    assert emu.writes
    assert all(a.startswith(WRITE_PREFIXES) for a, _ in emu.writes), emu.writes
    assert emu.main_eq().band(3).gain_db == pytest.approx(-6.0, abs=0.13)
    after_fader = read_console_state(client).matrices[SUB_MATRIX].fader_db
    assert after_fader == pytest.approx(original_fader - 3.0, abs=0.2)
    # The fader goes down before the EQ is touched.
    assert emu.writes[0][0] == f"/mtx/{SUB_MATRIX:02d}/mix/fader"
    (undo,) = undo_files(home)
    stored = files.read_undo(undo)
    assert stored.what == "packages"
    assert all(c.scope is not None for c in stored.changes)
    assert len(stored.results) == len(stored.changes)
    assert all(r.ok for r in stored.results)
    assert run(["undo", str(undo)], answers=["нет"]).exit_code == EXIT_USER
    back = run(["undo", str(undo)], answers=["вернуть"])
    assert back.exit_code == EXIT_OK, back.output
    assert emu.main_eq().band(3) == original.band(3)
    restored = read_console_state(client).matrices[SUB_MATRIX].fader_db
    assert restored == pytest.approx(original_fader, abs=0.2)
    assert emu.unknown == []


def test_a_wrong_word_writes_nothing(emu, home, run, client):
    proposal = write_proposal(home, client)
    result = run(["apply-packages", proposal], answers=["y"])
    assert result.exit_code == EXIT_USER
    assert emu.writes == []
    assert undo_files(home) == []


def test_dry_run_writes_nothing(emu, home, run, client):
    result = run(["apply-packages", write_proposal(home, client), "--dry-run"])
    assert result.exit_code == EXIT_OK, result.output
    assert "/main/st/eq/3/g" in result.output
    assert emu.writes == []


def test_a_console_changed_since_the_measurement_is_refused(emu, home, run, client):
    proposal = write_proposal(home, client)
    emu.hand_change("/main/st/eq/3/g", 0.4)  # someone turned a knob since
    result = run(["apply-packages", proposal], answers=["применить"])
    assert result.exit_code == EXIT_REFUSED
    assert "changed since" in result.output
    assert emu.writes == []


def test_suggestions_and_unselected_packages_are_never_written(emu, home, run, client):
    proposal = write_proposal(home, client, suggest_only=True)
    result = run(["apply-packages", proposal], answers=["применить"])
    assert result.exit_code == EXIT_OK
    assert emu.writes == []


def test_a_tampered_proposal_is_refused_by_the_policy(emu, home, run, client):
    proposal = write_proposal(home, client)
    data = json.loads((home / "proposal.json").read_text(encoding="utf-8"))
    data["packages"][0]["eq"][0]["after"]["bands"][2]["gain"] = 12.0  # a boost past the limit
    (home / "proposal.json").write_text(json.dumps(data), encoding="utf-8")
    result = run(["apply-packages", proposal], answers=["применить"])
    assert result.exit_code == EXIT_REFUSED, result.output
    assert emu.writes == []


def test_undo_leaves_a_hand_change_alone(emu, home, run, client):
    proposal = write_proposal(home, client, level_db=-3.0)
    assert run(["apply-packages", proposal], answers=["применить"]).exit_code == EXIT_OK
    emu.hand_change("/main/st/eq/3/g", 0.4)  # the volunteer moved the cut afterwards
    hand = emu.main_eq().band(3)
    emu.writes.clear()
    (undo,) = undo_files(home)
    result = run(["undo", str(undo)], answers=["вернуть"])
    assert result.exit_code == EXIT_OK, result.output
    assert "/main/st/eq/3/g" in result.output.split("руками")[1]
    assert emu.main_eq().band(3).gain_db == hand.gain_db
    assert all(not a.endswith("/eq/3/g") for a, _ in emu.writes)


def test_an_undo_file_with_a_foreign_scope_is_refused(emu, home, run, client):
    proposal = write_proposal(home, client)
    assert run(["apply-packages", proposal], answers=["применить"]).exit_code == EXIT_OK
    (undo,) = undo_files(home)
    data = json.loads(undo.read_text(encoding="utf-8"))
    data["changes"][0]["scope"] = "sub"
    undo.write_text(json.dumps(data), encoding="utf-8")
    emu.writes.clear()
    result = run(["undo", str(undo)], answers=["вернуть"])
    assert result.exit_code == EXIT_REFUSED
    assert emu.writes == []
