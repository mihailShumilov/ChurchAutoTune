"""The expert CLI end to end against the emulator and the simulated room.

Ported from the original tool's ``test_safety.py`` (integration part) and ``test_sub.py``.
Never weaken, skip or xfail the tests in this file: they are part of the safety suite.

Differences from the original that are deliberate:
  * exit codes follow SPEC §12 (1 user, 2 unreachable, 3 safety refusal, 4 readback mismatch);
  * a refusal is an exit code with a message, where the original called ``sys.exit(text)``;
  * the ``diag`` command and its test are gone together with its second socket;
  * writing output delay and polarity needs ``--write-unverified``.
"""

import copy
import functools
import json
from datetime import datetime

import numpy as np
import pytest
from typer.testing import CliRunner

from churchtune.audio.fake.backend import ConsoleRoomBackend
from churchtune.audio.fake.room import speaker_rooms
from churchtune.cli import apply as cli_apply
from churchtune.cli import main as cli_main
from churchtune.cli import sub as cli_sub
from churchtune.cli.context import (
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_READBACK,
    EXIT_REFUSED,
    EXIT_UNREACHABLE,
    EXIT_USER,
    FS_HZ,
)
from churchtune.cli.main import app
from churchtune.design.alignment import JunctionPoint, analyze_alignment
from churchtune.domain.changes import Change
from churchtune.errors import (
    ChurchTuneError,
    ConsoleUnreachable,
    DevSafeViolation,
    MeasurementAborted,
    UnsafeWrite,
)
from churchtune.mixers.x32 import params as P  # noqa: N812 - short alias kept from the original
from churchtune.mixers.x32.dump import dump_nodes
from churchtune.mixers.x32.nodes import read_console_state
from churchtune.storage import files
from churchtune.storage.paths import Workspace
from tests.emulator.x32_emulator import LOOPBACK
from tests.fixtures.paths import DUMP_2026_10_02
from tests.fixtures.venues import REFERENCE_VENUE_TOML, SUB_MATRIX

SUB_PREFIXES = ("/mtx/", "/outputs/main/")


@pytest.fixture
def home(tmp_path):
    (tmp_path / "venue.toml").write_text(REFERENCE_VENUE_TOML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def run(emu, home):
    """``run(["cmd", ...], answers=["y"])`` -> result with ``exit_code`` and ``output``."""

    def _run(argv, answers=()):
        return CliRunner().invoke(
            app,
            ["--console", f"{LOOPBACK}:{emu.port}", "--audio", "fake", "--home", str(home), *argv],
            input="".join(f"{a}\n" for a in answers),
            catch_exceptions=False,
        )

    return _run


def mix_on(emu):
    return {n: emu.mtx[n]["on"] for n in emu.mtx}


@pytest.fixture
def console_audio(monkeypatch, client):
    """A dry-ish hall (direct sound dominates) so the alignment problem has a clear answer; the
    sub arrives together with the tops, so LR24 leaves a phase mismatch for delay or polarity."""
    tops, sub = speaker_rooms(FS_HZ, drr_db=20.0, sub_extra_ms=0.0)
    backend = ConsoleRoomBackend(
        lambda: read_console_state(client), FS_HZ, sub_matrix=SUB_MATRIX, tops=tops, sub=sub
    )
    monkeypatch.setattr("churchtune.cli.sub.open_audio", lambda settings: backend)
    return backend


# ------------------------------------------------------------ read-only commands
def test_all_read_only_commands_never_write(emu, home, run):
    check = run(["check"])
    assert check.exit_code in (EXIT_OK, EXIT_USER)  # 1 = problems reported (phantom OFF in fixture)
    assert "+48V OFF" in check.output  # fixture: headamp 041 phantom OFF -> warned
    assert run(["backup"]).exit_code == EXIT_OK
    assert list((home / "backups").glob("*.txt"))
    assert run(["level", "--max-seconds", "0.5"]).exit_code == EXIT_OK
    for point in ("p1-center", "p2-left", "p3-right"):
        assert run(["measure", point, "-y", "--session", "s1"]).exit_code == EXIT_OK
    assert run(["analyze"]).exit_code == EXIT_OK
    assert run(["propose"]).exit_code == EXIT_OK
    proposal = home / "eq_proposal.json"
    assert proposal.exists()
    assert run(["export-for-chat"]).exit_code == EXIT_OK
    assert run(["apply", str(proposal), "--dry-run"]).exit_code == EXIT_OK
    assert run(["apply", str(proposal)], answers=["n"]).exit_code == EXIT_USER
    assert run(["apply", str(proposal)], answers=[""]).exit_code == EXIT_USER
    assert run(["apply", str(proposal)]).exit_code == EXIT_USER  # end of input is a no
    assert run(["verify", "-y"]).exit_code == EXIT_OK
    assert run(["sub", "show"]).exit_code == EXIT_OK
    assert emu.writes == [], emu.writes
    assert emu.unknown == [], emu.unknown
    assert not list((home / "undo").glob("*")) if (home / "undo").exists() else True


def test_backup_reads_the_same_nodes_in_the_same_order_as_the_original_dump_tool(run, home):
    assert run(["backup"]).exit_code == EXIT_OK

    lines = next((home / "backups").glob("*.txt")).read_text(encoding="utf-8").splitlines()

    original = [
        line.split(" ", 1)[0].lstrip("/")
        for line in DUMP_2026_10_02.read_text(encoding="utf-8").splitlines()
        if line.startswith("/")
    ]
    # The real console did not answer 12 of the nodes (fx-return preamps, some P16 outputs), so
    # the real dump is the node list minus those, in the same order.
    nodes = dump_nodes()
    assert len(nodes) == len(set(nodes)) == len(original) + 12
    remaining = iter(nodes)
    assert all(path in remaining for path in original), "order differs from the original dump"
    assert lines[0].startswith("# /xinfo 127.0.0.1 X32P-EMU")
    # The emulator answers every node, so the backup holds the whole list.
    assert [line.split(" ", 1)[0].lstrip("/") for line in lines[1:]] == nodes


def test_analysis_writes_report_and_plots(run, home):
    for point in ("p1", "p2"):
        assert run(["measure", point, "-y", "--session", "s1"]).exit_code == EXIT_OK

    result = run(["analyze", "s1"])

    assert result.exit_code == EXIT_OK
    assert "Сессия s1: 2 точек." in result.output
    out = home / "results" / "s1"
    assert sorted(p.name for p in out.iterdir()) == [
        "fr_average.png", "fr_points.png", "report.md", "rt60.png",
    ]  # fmt: skip
    report = (home / "results" / "report.md").read_text(encoding="utf-8")
    assert "# Отчёт замера — сессия `s1`" in report
    assert "Микрофон: ECM8000 (вход USB 10)." in report
    assert "](s1/fr_points.png)" in report
    session = files.read_session(home / "measurements" / "s1")
    assert session.eq_at_measurement is not None
    assert session.schema_version == 1


def test_measure_refuses_bad_point_names_and_levels_above_the_limit(emu, run):
    assert run(["measure", "p 1", "-y"]).exit_code == EXIT_USER
    assert run(["measure", "../x", "-y"]).exit_code == EXIT_USER
    loud = run(["measure", "p1", "-y", "--level", "-3"])
    assert loud.exit_code == EXIT_USER
    assert "ОТКАЗ" in loud.output
    assert run(["level", "--level", "-2", "--max-seconds", "0.2"]).exit_code == EXIT_USER
    assert run(["sub", "measure", "p1", "--level", "0"], answers=["y"]).exit_code == EXIT_USER
    assert emu.writes == []


def test_measure_asks_before_overwriting_a_point(run, home):
    assert run(["measure", "p1", "-y", "--session", "s"]).exit_code == EXIT_OK
    declined = run(["measure", "p1", "-y", "--session", "s"], answers=["n"])
    assert declined.exit_code == EXIT_USER
    assert "уже есть" in declined.output
    assert run(["measure", "p1", "-y", "--session", "s", "--force"]).exit_code == EXIT_OK


def test_unreachable_console_is_exit_code_2(emu, run, home):
    emu.drop_next = 10**6

    assert run(["backup"]).exit_code == EXIT_UNREACHABLE
    assert run(["sub", "show"]).exit_code == EXIT_UNREACHABLE


def test_a_lan_console_is_refused_in_dev_safe_before_any_packet(home, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("a socket was created")

    monkeypatch.setattr("churchtune.mixers.x32.transport.socket.socket", forbidden)

    with pytest.raises(DevSafeViolation):
        CliRunner().invoke(
            app,
            ["--console", "192.168.0.80", "--audio", "fake", "--home", str(home), "backup"],
            catch_exceptions=False,
        )


def test_a_real_audio_device_is_refused_in_dev_safe(run, home, emu):
    with pytest.raises(DevSafeViolation):
        CliRunner().invoke(
            app,
            ["--console", f"{LOOPBACK}:{emu.port}", "--audio", "X-USB", "--home", str(home),
             "measure", "p1", "-y"],
            catch_exceptions=False,
        )  # fmt: skip


# ------------------------------------------------------------ apply / undo
def close(a, b):
    for x, y in zip(a.bands, b.bands, strict=True):
        assert x.type == y.type
        assert x.gain_db == pytest.approx(y.gain_db, abs=0.01)
        assert x.freq_hz == pytest.approx(y.freq_hz, rel=1e-3)
        assert x.q == pytest.approx(y.q, rel=1e-3)
    return True


def test_apply_then_undo_roundtrip(emu, home, run):
    original = emu.main_eq()
    for point in ("p1", "p2", "p3", "p4", "p5", "p6"):
        assert run(["measure", point, "-y", "--session", "base"]).exit_code == EXIT_OK
    assert run(["propose"]).exit_code == EXIT_OK
    proposal = files.read_proposal(home / "eq_proposal.json")
    applied = run(["apply", str(home / "eq_proposal.json")], answers=["y"])
    assert applied.exit_code == EXIT_OK, applied.output
    assert emu.writes, "apply wrote nothing"
    assert all(address.startswith("/main/st/eq/") for address, _ in emu.writes), emu.writes
    now = emu.main_eq()
    for band, proposed in zip(now.bands, proposal.bands, strict=True):
        assert band.type == proposed.type
        assert band.gain_db == pytest.approx(proposed.gain, abs=0.13)
        assert band.freq_hz == pytest.approx(proposed.freq, rel=0.02)
    assert now.band(1) == original.band(1)  # HPF untouched
    undo = sorted((home / "undo").glob("undo_*.json"))[-1]
    stored = files.read_undo(undo)
    assert stored.eq_before.to_domain().band(2).gain_db == pytest.approx(original.band(2).gain_db)
    assert stored.eq_after is not None
    assert len(stored.results) == len(stored.changes)
    assert all(r.ok for r in stored.results)
    n_writes = len(emu.writes)
    # verify after apply: should be better than before (simulated room + console EQ model)
    assert run(["verify", "-y"]).exit_code == EXIT_OK
    summary = next((home / "results").glob("base-verify-*/verify_summary.txt")).read_text()
    before = float(summary.split("before ")[1].split(" dB")[0])
    after = float(summary.split("-> after ")[1].split(" dB")[0])
    assert after < before - 0.5, summary
    # undo, declined first
    assert run(["undo", str(undo)], answers=["n"]).exit_code == EXIT_USER
    assert len(emu.writes) == n_writes
    assert run(["undo", str(undo)], answers=["y"]).exit_code == EXIT_OK
    assert all(address.startswith("/main/st/eq/") for address, _ in emu.writes)
    assert close(emu.main_eq(), original)
    assert emu.unknown == []


def test_undo_file_is_on_disk_before_the_first_write(emu, home, run, monkeypatch):
    for point in ("p1", "p2", "p3"):
        run(["measure", point, "-y", "--session", "base"])
    run(["propose"])
    seen = []
    original_send = cli_apply.send_plan

    def spying_send(console, scope, changes, undo_path, confirmation, policy):
        seen.append((undo_path.exists(), len(emu.writes), files.read_undo(undo_path).changes))
        return original_send(console, scope, changes, undo_path, confirmation, policy)

    monkeypatch.setattr("churchtune.cli.apply.send_plan", spying_send)

    assert run(["apply", str(home / "eq_proposal.json")], answers=["y"]).exit_code == EXIT_OK

    exists, writes_before, stored_changes = seen[0]
    assert exists is True
    assert writes_before == 0
    assert len(stored_changes) == len(emu.writes)


def test_undo_restores_only_changed_params(emu, home, run):
    """The undo file lists one param; a manual change elsewhere must survive the undo."""
    old = emu.eq_x[2]["g"]
    new = P.quantize_gain(P.gain_to_norm(-2.0))
    emu.eq_x[2]["g"] = new  # as if an apply had written it
    undo = home / "u.json"
    undo.write_text(
        json.dumps(
            {"changes": [{"path": "/main/st/eq/2/g", "old_value": old, "new_value": new,
                          "old_display": "+0.00 dB", "new_display": "-2.00 dB"}]}
        ),
        encoding="utf-8",
    )  # fmt: skip
    # someone changes band 5 by hand on the console afterwards
    emu.eq_x[5]["g"] = P.quantize_gain(P.gain_to_norm(-1.0))

    assert run(["undo", str(undo)], answers=["y"]).exit_code == EXIT_OK

    eq = emu.main_eq()
    assert eq.band(2).gain_db == pytest.approx(P.norm_to_gain(old))
    assert eq.band(5).gain_db == pytest.approx(-1.0)  # untouched by undo
    assert [address for address, _ in emu.writes] == ["/main/st/eq/2/g"]


@pytest.mark.parametrize(
    "changes",
    [
        [{"path": "/main/st/eq/2/type", "old_value": -12}],
        [{"path": "/main/st/eq/2/type", "old_value": 99}],
        [{"path": "/main/st/eq/2/g", "old_value": 7.5}],
        [{"path": "/main/st/eq/2/g", "old_value": -0.1}],
        [{"path": "/ch/10/mix/on", "old_value": 1}],
        [{"path": "/main/st/mix/fader", "old_value": 0.75}],
        [{"path": "/main/st/eq/7/g", "old_value": 0.5}],
        [{"path": "/main/st/eq/2/x", "old_value": 0.5}],
        [{"path": "/main/st/eq/on", "old_value": 2}],
        [{"path": "/main/st/eq/on", "old_value": 0}],  # switching the EQ off
        [{"path": "/main/st/eq/1/f", "old_value": 0.3}],  # the high-pass band
        [{"path": "/main/st/eq/2/g", "old_value": 1.0}],  # +15 dB boost
        [{"path": "/main/st/eq/2/type", "old_value": 13}],  # LR24 on Main
    ],
)
def test_corrupt_undo_file_is_refused(emu, home, run, changes):
    bad = home / "bad.json"
    for change in changes:
        change.update(new_value=0.5, old_display="a", new_display="b")
    bad.write_text(json.dumps({"changes": changes}), encoding="utf-8")

    result = run(["undo", str(bad)], answers=["y"])

    assert result.exit_code == EXIT_REFUSED, result.output
    assert "ОТКАЗ" in result.output
    assert emu.writes == []


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", '{"changes": [{"path": 1}]}', '{"schema_version": 99, "changes": []}',
     '{"changes": [], "surprise": true}'],
)  # fmt: skip
def test_unreadable_undo_file_is_refused(emu, home, run, content):
    bad = home / "bad.json"
    bad.write_text(content, encoding="utf-8")

    assert run(["undo", str(bad)], answers=["y"]).exit_code == EXIT_REFUSED
    assert emu.writes == []


def test_corrupt_sub_undo_file_is_refused(emu, home, run):
    for path, value in [
        ("/ch/10/mix/on", 1),
        ("/main/st/mix/fader", 0.75),
        ("/mtx/03/mix/fader", 1.0),  # +10 dB
        ("/mtx/03/eq/3/type", 13),  # LR24 on a middle band
        ("/outputs/main/06/delay/time", 0.9),  # 450 ms
        ("/headamp/041/phantom", 1),
    ]:
        bad = home / "bad_sub.json"
        change = {
            "path": path,
            "old_value": value,
            "new_value": 0,
            "old_display": "a",
            "new_display": "b",
        }
        bad.write_text(json.dumps({"scope": "sub", "changes": [change]}), encoding="utf-8")

        result = run(["undo", str(bad)], answers=["y"])

        assert result.exit_code == EXIT_REFUSED, (path, result.output)
    assert emu.writes == []


def test_apply_reports_a_readback_mismatch_with_exit_code_4(emu, home, run, monkeypatch):
    for point in ("p1", "p2", "p3"):
        run(["measure", point, "-y", "--session", "base"])
    run(["propose"])
    # The console ignores gain writes on band 2: readback will not match.
    original_write = emu._write

    def stubborn(addr, args):
        if not addr.endswith("/g"):
            original_write(addr, args)

    monkeypatch.setattr(emu, "_write", stubborn)

    result = run(["apply", str(home / "eq_proposal.json")], answers=["y"])

    assert result.exit_code == EXIT_READBACK
    assert "откат: ctune undo" in result.output
    undo = files.read_undo(sorted((home / "undo").glob("undo_*.json"))[-1])
    assert undo.results[-1].ok is False
    assert len(undo.results) < len(undo.changes) or not all(r.ok for r in undo.results)


# ------------------------------------------------------------ sub / tops
def test_sub_full_flow_improves_crossover(emu, home, console_audio, run):
    original_mutes = mix_on(emu)
    assert run(["sub", "crossover"], answers=["y"]).exit_code == EXIT_OK
    assert emu.mtx[3]["bands"][6]["type"] == P.EQ_TYPES.index("LR24")
    assert (
        run(["sub", "measure", "p1-center", "--session", "before"], answers=["y"]).exit_code
        == EXIT_OK
    )
    assert mix_on(emu) == original_mutes, "mutes not restored"
    assert not Workspace(home).pending_mutes_file.exists()

    def analysed(session):
        points = files.load_junction_points(home / "measurements" / session)
        return analyze_alignment(
            {n: JunctionPoint(p.sub, p.tops, p.both) for n, p in points.items()}, 100.0, FS_HZ
        )

    before = analysed("before")
    aligned = run(["sub", "align", "--session", "before", "--write-unverified"], answers=["y"])
    assert aligned.exit_code == EXIT_OK, aligned.output
    assert (
        run(["sub", "measure", "p1-center", "--session", "after"], answers=["y"]).exit_code
        == EXIT_OK
    )
    after = analysed("after")
    p0, p1 = before.points["p1-center"], after.points["p1-center"]
    assert p1.all_measured_db.min() > p0.all_measured_db.min() + 6
    # the measured result matches the prediction made before applying
    assert np.sqrt(np.mean((p1.all_measured_db - p0.sum_best_db) ** 2)) < 1.5
    assert emu.writes
    assert not emu.foreign_writes(SUB_PREFIXES), emu.foreign_writes(SUB_PREFIXES)
    assert mix_on(emu) == original_mutes
    assert (home / "results" / "before" / "sub_align.png").exists()
    assert (
        "Кроссовер 100 Гц, точек: 1" in (home / "results" / "before" / "sub_align.txt").read_text()
    )


def test_sub_align_shows_unverified_writes_as_advice_by_default(emu, home, console_audio, run):
    assert run(["sub", "crossover"], answers=["y"]).exit_code == EXIT_OK
    assert run(["sub", "measure", "p1"], answers=["y"]).exit_code == EXIT_OK
    n0 = len(emu.writes)

    result = run(["sub", "align"], answers=["y"])

    assert result.exit_code == EXIT_REFUSED
    assert "не проверена на железе" in result.output
    assert "Выставьте на пульте руками" in result.output
    assert len(emu.writes) == n0


def test_sub_measure_declined_writes_nothing(emu, home, console_audio, run):
    assert run(["sub", "measure", "p1"], answers=["n"]).exit_code == EXIT_USER
    assert run(["sub", "crossover"], answers=["n"]).exit_code == EXIT_USER
    assert run(["sub", "crossover", "--dry-run"]).exit_code == EXIT_OK
    assert run(["sub", "show"]).exit_code == EXIT_OK
    assert emu.writes == []


def test_mutes_restored_when_measurement_aborts(emu, home, console_audio, run, monkeypatch):
    original_mutes = mix_on(emu)

    def boom(out, events=None):
        events[0][1]()  # mutes switched to block B ...
        raise MeasurementAborted("Ctrl+C")

    monkeypatch.setattr(console_audio, "playrec", boom)

    assert run(["sub", "measure", "p1"], answers=["y"]).exit_code == EXIT_INTERRUPTED
    assert mix_on(emu) == original_mutes
    assert not Workspace(home).pending_mutes_file.exists()
    assert any(address.endswith("/mix/on") for address, _ in emu.writes)


def test_failed_restore_leaves_pending_and_blocks(emu, home, console_audio, run, monkeypatch):
    original_mutes = mix_on(emu)

    def boom(out, events=None):
        emu.drop_next = 10**6  # the console stops answering mid-measurement
        raise RuntimeError("network gone")

    monkeypatch.setattr(console_audio, "playrec", boom)
    with pytest.raises(RuntimeError, match="network gone"):
        run(["sub", "measure", "p1"], answers=["y"])
    pending = Workspace(home).pending_mutes_file
    assert pending.exists()
    assert files.read_pending_mutes(Workspace(home)).mtx_on == {
        n: bool(v) for n, v in original_mutes.items() if n != 4
    }
    emu.drop_next = 0
    blocked = run(["sub", "align"])
    assert blocked.exit_code == EXIT_REFUSED  # refuses until restored
    assert "sub restore" in blocked.output
    assert run(["sub", "restore"], answers=["y"]).exit_code == EXIT_OK
    assert mix_on(emu) == original_mutes
    assert not pending.exists()


def test_pending_file_is_written_before_the_first_mute(emu, home, console_audio, run, monkeypatch):
    seen = []
    original_send = cli_sub.send_plan

    def spying_send(console, scope, changes, undo_path, confirmation, policy):
        seen.append((undo_path.exists(), len(emu.writes)))
        return original_send(console, scope, changes, undo_path, confirmation, policy)

    monkeypatch.setattr("churchtune.cli.sub.send_plan", spying_send)

    assert run(["sub", "measure", "p1"], answers=["y"]).exit_code == EXIT_OK

    assert seen[0] == (True, 0)
    assert all(exists for exists, _ in seen)


def test_sub_undo_restores_exactly(emu, home, run):
    before = copy.deepcopy(emu.mtx)
    assert run(["sub", "crossover"], answers=["y"]).exit_code == EXIT_OK
    undo = sorted((home / "undo").glob("undo_sub_*.json"))[-1]
    assert emu.mtx != before

    assert run(["undo", str(undo)], answers=["y"]).exit_code == EXIT_OK

    assert emu.mtx == before
    assert not emu.foreign_writes(SUB_PREFIXES)


def test_restore_dry_run_keeps_pending(emu, home, run):
    workspace = Workspace(home)
    files.write_pending_mutes(
        workspace, files.PendingMutes(created="t", mtx_on={1: True, 2: True, 3: True})
    )
    emu.mtx[1]["on"] = 0

    assert run(["sub", "restore", "--dry-run"]).exit_code == EXIT_OK
    assert workspace.pending_mutes_file.exists()
    assert emu.mtx[1]["on"] == 0
    assert run(["sub", "restore"], answers=["y"]).exit_code == EXIT_OK
    assert emu.mtx[1]["on"] == 1
    assert not workspace.pending_mutes_file.exists()
    assert run(["sub", "restore"]).exit_code == EXIT_OK  # nothing left to restore


def test_sub_undo_order_never_louder_midway(emu, home, run):
    assert run(["sub", "crossover"], answers=["y"]).exit_code == EXIT_OK
    undo = sorted((home / "undo").glob("undo_sub_*.json"))[-1]
    n0 = len(emu.writes)

    assert run(["undo", str(undo)], answers=["y"]).exit_code == EXIT_OK

    paths = [address for address, _ in emu.writes[n0:]]
    # restoring the old deep cuts (gain down) happens before the filters are switched back
    first_filter = min(i for i, p in enumerate(paths) if p.endswith(("/type", "/f")))
    for i, path in enumerate(paths):
        if path in ("/mtx/03/eq/3/g", "/mtx/03/eq/4/g", "/mtx/01/eq/2/g"):
            assert i < first_filter, paths
    # restoring the old +4.5 dB boost comes last
    assert paths.index("/mtx/03/eq/2/g") > first_filter


def test_measure_dry_run_refused(emu, home, run):
    result = run(["sub", "measure", "p1", "--dry-run"])

    assert result.exit_code == EXIT_USER
    assert "--dry-run здесь не применим" in result.output
    assert emu.writes == []


def test_only_level_changes_just_the_sub_fader(emu, home, console_audio, run):
    assert run(["sub", "crossover"], answers=["y"]).exit_code == EXIT_OK
    assert run(["sub", "measure", "p1"], answers=["y"]).exit_code == EXIT_OK
    n0 = len(emu.writes)

    run(["sub", "align", "--only-level"], answers=["y"])

    new = [address for address, _ in emu.writes[n0:]]
    assert set(new) <= {"/mtx/03/mix/fader"}, new


def test_level_match_with_no_alignment_gain_writes_the_fader_and_nothing_else(
    emu, home, run, monkeypatch
):
    """Regression for a defect of the original tool: with less than 1 dB to gain it said
    "delay NOT changed" but still wrote the delay and polarity it had found."""
    click = np.zeros(1 << 14)
    click[4800] = 1.0
    late = np.roll(click, 5) * 0.4  # 0.1 ms late and 8 dB down: nothing to gain, level off
    stored = files.StoredJunctionPoint(late, click, late + click, FS_HZ, meta=None)
    monkeypatch.setattr(
        "churchtune.cli.sub.files.load_junction_points", lambda path: {"p1": stored}
    )
    (home / "measurements" / "j").mkdir(parents=True)

    result = run(
        ["sub", "align", "--session", "j", "--level-match", "--write-unverified"], answers=["y"]
    )

    assert result.exit_code == EXIT_OK, result.output
    assert "задержку НЕ меняю" in result.output
    assert [address for address, _ in emu.writes] == ["/mtx/03/mix/fader"]


def test_sub_refuses_unknown_actions_slopes_and_roles(emu, run):
    assert run(["sub", "explode"]).exit_code == EXIT_USER
    assert run(["sub", "crossover", "--slope", "PEQ"]).exit_code == EXIT_USER
    assert run(["sub", "crossover", "--slope", "nonsense"]).exit_code == EXIT_USER
    assert emu.writes == []


@pytest.mark.parametrize("matrix", ["4", "7", "0"])
def test_a_matrix_without_an_output_cannot_be_given_a_role(emu, run, matrix):
    """Matrix 4 feeds no output and 7 does not exist: naming one never makes it writable."""
    for action in ("show", "crossover"):
        result = run(["sub", action, "--sub", matrix, "--mains", "1,2"], answers=["y"])

        assert result.exit_code == EXIT_USER, result.output
        assert "не удалось определить роли" in result.output
    assert emu.writes == []


# ------------------------------------------------------------ unverified writes: delay, polarity
def _aligned_with_delay_and_polarity(emu, run, home):
    """Crossover, one junction point and an alignment written with the explicit opt-in."""
    assert run(["sub", "crossover"], answers=["y"]).exit_code == EXIT_OK
    assert run(["sub", "measure", "p1"], answers=["y"]).exit_code == EXIT_OK
    before = (copy.deepcopy(emu.out_delay), copy.deepcopy(emu.mtx))
    n0 = len(emu.writes)
    result = run(["sub", "align", "--write-unverified"], answers=["y"])
    assert result.exit_code == EXIT_OK, result.output
    written = [address for address, _ in emu.writes[n0:]]
    undo = sorted((home / "undo").glob("undo_sub_*.json"))[-1]
    return before, written, undo, result


def test_align_marks_unverified_rows_and_writes_the_delay_time_before_the_switch(
    emu, home, console_audio, run
):
    _, written, _, result = _aligned_with_delay_and_polarity(emu, run, home)

    assert "НЕ ПРОВЕРЕНО НА ЖЕЛЕЗЕ" in result.output
    assert "--write-unverified" in result.output.split("Откат:")[-1]  # the undo hint is complete
    switches = [a for a in written if a.endswith("/delay/on")]
    assert switches, written  # the scenario must exercise a delay
    for address in switches:
        time_address = address.removesuffix("on") + "time"
        assert written.index(time_address) < written.index(address), written


def test_undo_of_delay_and_polarity_is_refused_without_the_explicit_flag(
    emu, home, console_audio, run
):
    _, _, undo, _ = _aligned_with_delay_and_polarity(emu, run, home)
    after = (copy.deepcopy(emu.out_delay), copy.deepcopy(emu.mtx))
    n0 = len(emu.writes)

    result = run(["undo", str(undo)], answers=["y"])

    assert result.exit_code == EXIT_REFUSED, result.output
    assert "не проверена на железе" in result.output
    assert "Верните на пульте руками" in result.output
    assert "--write-unverified" in result.output
    assert len(emu.writes) == n0
    assert (emu.out_delay, emu.mtx) == after


def test_undo_with_the_flag_restores_delay_and_polarity_switch_off_first(
    emu, home, console_audio, run
):
    before, written, undo, _ = _aligned_with_delay_and_polarity(emu, run, home)
    n0 = len(emu.writes)

    result = run(["undo", str(undo), "--write-unverified"], answers=["y"])

    assert result.exit_code == EXIT_OK, result.output
    assert "НЕ ПРОВЕРЕНО НА ЖЕЛЕЗЕ" in result.output
    assert (emu.out_delay, emu.mtx) == before
    restored = [address for address, _ in emu.writes[n0:]]
    assert sorted(restored) == sorted(written)
    switches = [a for a in restored if a.endswith("/delay/on")]
    assert switches, restored
    for address in switches:
        time_address = address.removesuffix("on") + "time"
        assert restored.index(address) < restored.index(time_address), restored
    assert not emu.foreign_writes(SUB_PREFIXES)


def test_a_hand_made_sub_undo_with_delay_or_polarity_is_refused_without_the_flag(emu, home, run):
    for path, value in [
        ("/outputs/main/06/delay/time", 0.01),
        ("/outputs/main/06/delay/on", 1),
        ("/mtx/03/preamp/invert", 1),
    ]:
        crafted = home / "crafted.json"
        change = {
            "path": path, "old_value": value, "new_value": 0, "old_display": "a", "new_display": "b",
        }  # fmt: skip
        fader = {**change, "path": "/mtx/03/mix/fader", "old_value": 0.25}
        crafted.write_text(json.dumps({"scope": "sub", "changes": [fader, change]}), "utf-8")

        result = run(["undo", str(crafted)], answers=["y"])

        assert result.exit_code == EXIT_REFUSED, (path, result.output)
    assert emu.writes == []
    assert emu.queries == []  # refused before the console is asked anything


def test_the_flag_does_not_lift_the_gate_for_a_file_without_unverified_lines(emu, home, run):
    """With the flag but no delay / polarity line in the file, the policy stays strict."""
    seen = []
    original = cli_apply.send_plan

    def spy(console, scope, changes, undo_path, confirmation, policy):
        seen.append(policy.allow_unverified_writes)
        return original(console, scope, changes, undo_path, confirmation, policy)

    assert run(["sub", "crossover"], answers=["y"]).exit_code == EXIT_OK
    undo = sorted((home / "undo").glob("undo_sub_*.json"))[-1]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cli_apply, "send_plan", spy)
        assert run(["undo", str(undo), "--write-unverified"], answers=["y"]).exit_code == EXIT_OK

    assert seen == [False]


def _change(path, old, new):
    return Change(path, old, new, "a", "b")


def test_undo_order_switches_delays_off_first_and_on_last_for_several_outputs():
    back = [
        _change("/outputs/main/06/delay/time", 0.02, 0.0),
        _change("/outputs/main/06/delay/on", 1, 0),
        _change("/outputs/main/07/delay/time", 0.02, 0.0),
        _change("/outputs/main/07/delay/on", 1, 0),
        _change("/mtx/03/preamp/invert", 1, 0),
        _change("/mtx/03/mix/fader", 0.2, 0.3),
    ]
    forth = [_change(c.path, c.new_value, c.old_value) for c in back]

    def ordered(changes):
        return [
            c.path.rsplit("/", 3)[1] + "/" + c.path.rsplit("/", 1)[1]
            for c in sorted(changes, key=cli_apply.safety_phase)
        ]

    assert ordered(back) == [
"06/on", "07/on", "06/time", "07/time", "03/invert", "03/fader",
    ]  # fmt: skip
    assert ordered(forth) == [
"03/fader", "06/time", "07/time", "03/invert", "06/on", "07/on",
    ]  # fmt: skip


def test_undo_never_switches_on_a_delay_longer_than_the_limit(emu, home, run):
    """A file with only the switch would bring in whatever time the console has stored."""
    emu.out_delay[6]["time"] = 0.9  # about 450 ms stored, switched off
    crafted = home / "crafted.json"
    change = {
        "path": "/outputs/main/06/delay/on", "old_value": 1, "new_value": 0,
        "old_display": "ON", "new_display": "OFF",
    }  # fmt: skip
    crafted.write_text(json.dumps({"scope": "sub", "changes": [change]}), "utf-8")

    result = run(["undo", str(crafted), "--write-unverified"], answers=["y"])

    assert result.exit_code == EXIT_REFUSED, result.output
    assert "больше лимита" in result.output
    assert emu.writes == []
    emu.out_delay[6]["time"] = P.quantize_delay(P.delay_to_norm(9.0))
    assert run(["undo", str(crafted), "--write-unverified"], answers=["y"]).exit_code == EXIT_OK
    assert [address for address, _ in emu.writes] == ["/outputs/main/06/delay/on"]


def test_an_interrupted_send_keeps_the_undo_file_and_says_how_to_go_back(
    emu, home, run, monkeypatch
):
    def broken(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_apply, "send_plan", broken)

    result = run(["sub", "crossover"], answers=["y"])

    assert result.exit_code == EXIT_INTERRUPTED
    undo = sorted((home / "undo").glob("undo_sub_*.json"))
    assert len(undo) == 1  # on disk before the send, kept after the failure
    assert files.read_undo(undo[0]).changes
    assert "запись прервана" in result.output
    assert f"ctune undo undo/{undo[0].name}" in result.output


def test_a_lost_console_mid_send_keeps_the_undo_file(emu, home, run, monkeypatch):
    def broken(*args, **kwargs):
        raise ConsoleUnreachable("gone")

    monkeypatch.setattr(cli_apply, "send_plan", broken)

    with pytest.raises(ConsoleUnreachable):
        run(["sub", "crossover"], answers=["y"])

    assert len(list((home / "undo").glob("undo_sub_*.json"))) == 1


def test_a_refused_authorization_is_exit_code_3_and_sends_nothing(emu, home, run, monkeypatch):
    def refused(*args, **kwargs):
        raise UnsafeWrite("plan does not match")

    monkeypatch.setattr("churchtune.cli.apply.authorize", refused)

    result = run(["sub", "crossover"], answers=["y"])

    assert result.exit_code == EXIT_REFUSED
    assert "plan does not match" in result.output
    assert emu.writes == []


def test_restore_interrupted_mid_send_keeps_the_pending_file(emu, home, run, monkeypatch):
    workspace = Workspace(home)
    files.write_pending_mutes(workspace, files.PendingMutes(created="t", mtx_on={1: True}))
    emu.mtx[1]["on"] = 0

    def broken(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_apply, "send_plan", broken)

    assert run(["sub", "restore"], answers=["y"]).exit_code == EXIT_INTERRUPTED
    assert workspace.pending_mutes_file.exists()


# ------------------------------------------------------------ mechanisms around a write
def test_two_plans_in_the_same_second_get_two_undo_files(emu, home, run, monkeypatch):
    frozen = functools.partial(cli_main.Settings, now=lambda: datetime(2026, 10, 8, 12, 0, 0))
    monkeypatch.setattr(cli_main, "Settings", frozen)
    assert run(["sub", "crossover"], answers=["y"]).exit_code == EXIT_OK
    first = sorted((home / "undo").glob("undo_sub_*.json"))
    assert len(first) == 1
    first_text = first[0].read_text(encoding="utf-8")

    assert run(["sub", "crossover", "--fc", "120"], answers=["y"]).exit_code == EXIT_OK

    both = sorted((home / "undo").glob("undo_sub_*.json"))
    assert len(both) == 2
    assert first[0].read_text(encoding="utf-8") == first_text  # never overwritten


def test_unused_path_counts_up_until_a_free_name(tmp_path):
    path = tmp_path / "undo_sub_x.json"
    assert cli_apply._unused(path) == path
    path.write_text("{}")
    assert cli_apply._unused(path) == tmp_path / "undo_sub_x_2.json"
    (tmp_path / "undo_sub_x_2.json").write_text("{}")
    assert cli_apply._unused(path) == tmp_path / "undo_sub_x_3.json"


@pytest.mark.parametrize("stuck", ["/mtx/02/mix/on", "/mtx/03/mix/on"])
def test_a_mute_that_does_not_confirm_stops_the_measurement_and_restores(
    emu, home, console_audio, run, monkeypatch, stuck
):
    """The console ignores one mute: nothing is played with the wrong speakers open."""
    original_mutes = mix_on(emu)
    played = []

    def playrec(out, events=None):
        played.append(1)
        events[0][1]()  # the switch to block B happens while the sweep plays

    monkeypatch.setattr(console_audio, "playrec", playrec)
    emu.ignored.add(stuck)

    with pytest.raises(ChurchTuneError, match="не подтвердились"):
        run(["sub", "measure", "p1"], answers=["y"])

    # Step A mutes the tops, step B mutes the sub: a failure at A means no sound at all.
    assert played == ([] if stuck == "/mtx/02/mix/on" else [1])
    assert mix_on(emu) == original_mutes
    assert not Workspace(home).pending_mutes_file.exists()
    assert not emu.foreign_writes(SUB_PREFIXES)
    assert not list((home / "measurements").glob("*/p1/ir_*.wav"))


def test_apply_and_undo_are_refused_while_mutes_are_pending(emu, home, run):
    workspace = Workspace(home)
    files.write_pending_mutes(workspace, files.PendingMutes(created="t", mtx_on={1: True}))
    assert run(["sub", "crossover", "--dry-run"]).exit_code == EXIT_REFUSED
    undo = home / "u.json"
    undo.write_text(json.dumps({"scope": "main", "changes": []}), encoding="utf-8")

    for argv in (["undo", str(undo)], ["apply", str(home / "eq_proposal.json")]):
        result = run(argv, answers=["y"])

        assert result.exit_code == EXIT_REFUSED, result.output
        assert "ctune sub restore" in result.output
    assert emu.writes == []


def test_undo_for_another_console_asks_first(emu, home, run):
    for point in ("p1", "p2", "p3"):
        run(["measure", point, "-y", "--session", "base"])
    run(["propose"])
    assert run(["apply", str(home / "eq_proposal.json")], answers=["y"]).exit_code == EXIT_OK
    undo_path = sorted((home / "undo").glob("undo_*.json"))[-1]
    stored = json.loads(undo_path.read_text(encoding="utf-8"))
    stored["ip"] = "192.0.2.80"
    undo_path.write_text(json.dumps(stored), encoding="utf-8")
    n0 = len(emu.writes)

    declined = run(["undo", str(undo_path)], answers=["n"])

    assert declined.exit_code == EXIT_USER
    assert "записан для пульта 192.0.2.80" in declined.output
    assert len(emu.writes) == n0
    assert run(["undo", str(undo_path)], answers=[""]).exit_code == EXIT_USER
    assert len(emu.writes) == n0
    assert run(["undo", str(undo_path)], answers=["y", "y"]).exit_code == EXIT_OK
    assert len(emu.writes) > n0


# ------------------------------------------------------------ entry point
def _run_entry(monkeypatch, capsys, error):
    def boom():
        raise error

    monkeypatch.setattr(cli_main, "app", boom)
    with pytest.raises(SystemExit) as exit_info:
        cli_main.run()
    return exit_info.value.code, capsys.readouterr().out


def test_entry_point_turns_a_dev_safe_violation_into_exit_code_3(monkeypatch, capsys):
    code, out = _run_entry(monkeypatch, capsys, DevSafeViolation("not loopback"))

    assert code == EXIT_REFUSED
    assert "dev-safe" in out
    assert "not loopback" in out


def test_entry_point_turns_a_domain_error_into_exit_code_1(monkeypatch, capsys):
    code, out = _run_entry(monkeypatch, capsys, ChurchTuneError("something went wrong"))

    assert code == EXIT_USER
    assert "something went wrong" in out


def test_entry_point_refuses_a_lan_console_with_exit_code_3(home, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["ctune", "--console", "192.0.2.80", "--audio", "fake", "--home", str(home), "backup"],
    )

    with pytest.raises(SystemExit) as exit_info:
        cli_main.run()

    assert exit_info.value.code == EXIT_REFUSED
    assert "dev-safe" in capsys.readouterr().out
