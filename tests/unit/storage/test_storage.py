"""Stored files: paths, round trips, migrations from the original tool's files."""

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from churchtune.domain.changes import Change, WriteScope
from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.errors import UnsupportedSchema
from churchtune.storage import files
from churchtune.storage.migrations import migrate
from churchtune.storage.models import (
    ChangeModel,
    CodedText,
    EqModel,
    JunctionMeta,
    JunctionState,
    PendingMutes,
    PointMeta,
    ProposalFile,
    ResultModel,
    RolesModel,
    SessionFile,
    SweepModel,
    UndoFile,
)
from churchtune.storage.paths import Workspace, workspace_from_env

FS_HZ = 48000.0
EQ = SystemEq(
    True,
    (
        Band(1, EqType.BU18, 23.0, -7.75, 7.8),
        *(Band(i, EqType.PEQ, 100.0 * i, -1.0 * i, 2.0) for i in range(2, 7)),
    ),
)
NOW = datetime(2026, 10, 2, 18, 42, 49)


@pytest.fixture
def ws(tmp_path):
    return Workspace(tmp_path)


def session(name="s1", eq=EQ, kind="baseline", created="2026-10-02T18:09:00"):
    return SessionFile(
        name=name,
        kind=kind,
        created=created,
        eq_at_measurement=EqModel.from_domain(eq) if eq else None,
        fs=FS_HZ,
        sweep=SweepModel(f1=20.0, f2=20000.0, seconds=6.0),
    )


def point_meta(point="p1-center", **overrides):
    values = {
        "point": point, "time": "2026-10-02T18:10:00", "level_dbfs": -17.0, "avg": 2,
        "backend": "simulator", "peak_dbfs": -11.59, "clipped_samples": 0, "noise_dbfs": -57.3,
        "sweep_rms_dbfs": -22.8, "snr_db": 34.5, "pnr_db": 81.4, "latency_ms": 227.12,
        "verdict": "ok",
    }  # fmt: skip
    return PointMeta(**{**values, **overrides})


# ------------------------------------------------------------ paths
def test_workspace_places(tmp_path):
    ws = Workspace(tmp_path)

    assert ws.measurements == tmp_path / "measurements"
    assert ws.results == tmp_path / "results"
    assert ws.backups == tmp_path / "backups"
    assert ws.undo == tmp_path / "undo"
    assert ws.proposal_file == tmp_path / "eq_proposal.json"
    assert ws.pending_mutes_file == tmp_path / "sub_mutes_pending.json"
    assert ws.mic_cal_file == tmp_path / "mic_cal.txt"


def test_undo_file_names_follow_the_original_tool(ws):
    assert ws.undo_path(WriteScope.MAIN, NOW).name == "undo_20261002_184249.json"
    assert ws.undo_path(WriteScope.SUB, NOW).name == "undo_sub_20261002_184249.json"
    assert (
        ws.undo_path(WriteScope.MAIN, NOW, revert=True).name == "undo_20261002_184249_revert.json"
    )


def test_workspace_from_env(tmp_path):
    assert workspace_from_env(tmp_path / "x", {"CTUNE_HOME": "/elsewhere"}).root == tmp_path / "x"
    assert workspace_from_env(None, {"CTUNE_HOME": str(tmp_path)}).root == tmp_path
    assert workspace_from_env(None, {}).root == Path("~/ChurchTune").expanduser()
    assert workspace_from_env(None, {"CTUNE_HOME": "  "}).root == Path("~/ChurchTune").expanduser()


# ------------------------------------------------------------ models
def test_eq_model_round_trips_the_domain_eq():
    assert EqModel.from_domain(EQ).to_domain() == EQ
    assert EqModel.from_domain(EQ).model_dump(mode="json")["bands"][0] == {
        "band": 1, "type": "BU18", "freq": 23.0, "gain": -7.75, "q": 7.8,
    }  # fmt: skip


def test_change_model_keeps_ints_and_floats_apart_through_json():
    changes = [
        Change("/mtx/03/eq/6/type", 4, 13, "HShv", "LR24"),
        Change("/mtx/03/mix/on", 1, 0, "ON", "MUTE"),
        Change("/mtx/03/eq/6/f", 0.525, 0.235, "751.7 Hz", "101.4 Hz"),
        Change("/main/st/eq/2/g", 0.0, 1.0, "-15.0 dB", "+15.0 dB"),
    ]
    undo = UndoFile(
        created="t",
        scope=WriteScope.SUB,
        changes=tuple(ChangeModel.from_domain(c) for c in changes),
    )

    restored = UndoFile.model_validate(json.loads(json.dumps(undo.model_dump(mode="json"))))

    for original, model in zip(changes, restored.changes, strict=True):
        back = model.to_domain()
        assert back == original
        assert type(back.old_value) is type(original.old_value)
        assert type(back.new_value) is type(original.new_value)


def test_models_refuse_unknown_keys_and_are_immutable():
    with pytest.raises(ValidationError):
        SessionFile.model_validate({**session().model_dump(mode="json"), "surprise": 1})
    with pytest.raises(ValidationError):
        session().name = "other"


def test_every_file_model_carries_the_schema_version():
    undo = UndoFile(created="t", scope=WriteScope.MAIN, changes=())
    pending = PendingMutes(created="t", mtx_on={3: True})

    for model in (session(), point_meta(), undo, pending):
        assert model.model_dump(mode="json")["schema_version"] == 1


# ------------------------------------------------------------ json writing
def test_write_json_is_atomic_and_leaves_no_temporary_file(tmp_path):
    path = tmp_path / "deep" / "undo.json"

    files.write_json(path, session())

    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "s1"
    assert [p.name for p in path.parent.iterdir()] == ["undo.json"]
    assert path.read_text(encoding="utf-8").endswith("}\n")


def test_write_json_replaces_an_existing_file_completely(tmp_path):
    path = tmp_path / "session.json"
    path.write_text("x" * 10000, encoding="utf-8")

    files.write_json(path, session())

    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "s1"


def test_reading_a_json_file_that_is_not_an_object_fails(tmp_path):
    path = tmp_path / "undo.json"
    path.write_text("[1, 2]", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        files.read_undo(path)


# ------------------------------------------------------------ sessions
def test_ensure_session_creates_then_reuses(ws):
    first = files.ensure_session(ws, session(), "18:09:00")
    second = files.ensure_session(ws, session(), "18:15:00")

    assert (first.created, first.eq_changed) == (True, False)
    assert (second.created, second.eq_changed) == (False, False)
    assert first.path == ws.measurements / "s1"
    assert files.read_session(first.path) == session()


def test_ensure_session_flags_an_eq_that_changed_during_the_session(ws):
    files.ensure_session(ws, session(), "18:09:00")
    other = EQ.with_band(Band(2, EqType.PEQ, 480.0, -9.0, 1.5))

    status = files.ensure_session(ws, session(eq=other), "18:30:00")

    assert status.eq_changed is True
    stored = files.read_session(status.path)
    assert stored.eq_at_measurement == EqModel.from_domain(EQ)  # the original EQ stays on record
    assert stored.warnings == ("EQ changed during session at 18:30:00",)


def test_ensure_session_does_not_flag_when_an_eq_is_unknown(ws):
    files.ensure_session(ws, session(eq=None), "18:09:00")

    assert files.ensure_session(ws, session(), "18:10:00").eq_changed is False


def test_sessions_are_listed_by_creation_time_and_latest_respects_the_kind(ws):
    files.ensure_session(ws, session("b", created="2026-10-02T19:00:00"), "t")
    files.ensure_session(ws, session("a", created="2026-10-02T20:00:00"), "t")
    files.ensure_session(ws, session("v", kind="verify", created="2026-10-02T21:00:00"), "t")
    (ws.measurements / "not-a-session").mkdir()

    assert [p.name for p in files.list_sessions(ws)] == ["b", "a", "v"]
    assert files.latest_session(ws).name == "a"
    assert files.latest_session(ws, "verify").name == "v"


def test_no_sessions(ws):
    assert files.list_sessions(ws) == []
    assert files.latest_session(ws) is None


# ------------------------------------------------------------ points
def test_point_round_trip_keeps_the_response_within_float32(ws):
    rng = np.random.default_rng(0)
    ir = rng.normal(scale=0.1, size=4800)
    recording = rng.normal(scale=0.01, size=9600)
    grid = np.array([20.0, 1000.0, 19896.974])
    response = np.array([-3.21049, 0.0, -12.5])
    directory = ws.measurements / "s1"

    saved = files.save_point(
        directory,
        point_meta(),
        recording=recording,
        ir=ir,
        fs_hz=FS_HZ,
        grid_hz=grid,
        response_db=response,
    )

    points = files.load_points(directory)
    assert saved == directory / "p1-center"
    assert list(points) == ["p1-center"]
    stored = points["p1-center"]
    np.testing.assert_allclose(stored.ir, ir, atol=1e-7)  # float32 on disk
    assert stored.fs_hz == FS_HZ
    assert stored.meta == point_meta()
    assert (saved / "fr.csv").read_text(encoding="utf-8").splitlines() == [
        "freq_hz,level_db_1_6oct_raw", "20.000,-3.210", "1000.000,0.000", "19896.974,-12.500",
    ]  # fmt: skip
    assert files.point_names(directory) == ["p1-center"]
    assert files.session_levels(directory) == {"p1-center": point_meta()}


def test_point_warnings_are_stored_as_codes_with_parameters(ws):
    warning = CodedText(code="low-snr", params={"snr_db": 24.1, "noise_dbfs": -41.0})
    directory = ws.measurements / "s1"

    files.save_point(
        directory, point_meta(verdict="warn", warnings=(warning,)), recording=np.zeros(8),
        ir=np.zeros(8), fs_hz=FS_HZ, grid_hz=np.array([20.0]), response_db=np.array([0.0]),
    )  # fmt: skip

    assert files.load_points(directory)["p1-center"].meta.warnings == (warning,)


def test_a_point_without_meta_still_loads(ws):
    directory = ws.measurements / "s1"
    files.save_point(
        directory, point_meta(), recording=np.zeros(8), ir=np.ones(8) * 0.5, fs_hz=FS_HZ,
        grid_hz=np.array([20.0]), response_db=np.array([0.0]),
    )  # fmt: skip
    (directory / "p1-center" / "meta.json").unlink()
    (directory / "empty-dir").mkdir()

    points = files.load_points(directory)

    assert list(points) == ["p1-center"]
    assert points["p1-center"].meta is None
    assert files.session_levels(directory) == {}


def test_junction_point_round_trip_stores_the_first_half_of_each_response(ws):
    rng = np.random.default_rng(1)
    irs = tuple(rng.normal(scale=0.1, size=1024) for _ in range(3))
    meta = JunctionMeta(
        point="p4-mid", time="t", level_dbfs=-17.0, peaks_dbfs=(-21.3, -12.7, -12.5),
        roles=RolesModel(sub=(3,), mains=(1, 2), others=(5, 6)), state=JunctionState(sub_fader=-37.5),
    )  # fmt: skip
    directory = ws.measurements / "sub-2026-10-02"

    files.save_junction_point(directory, meta, irs, FS_HZ)

    stored = files.load_junction_points(directory)["p4-mid"]
    assert stored.meta == meta
    assert stored.fs_hz == FS_HZ
    for got, original in zip((stored.sub, stored.tops, stored.both), irs, strict=True):
        assert len(got) == 512
        np.testing.assert_allclose(got, original[:512], atol=1e-7)


# ------------------------------------------------------------ mic calibration
def test_mic_cal_is_read_with_spaces_or_commas_and_skips_junk(tmp_path):
    path = tmp_path / "mic_cal.txt"
    path.write_text("* header\n20 0.5\n1000, 0.0\n\nfoo bar\n20000\t-2.5\n", encoding="utf-8")

    freqs, response = files.load_mic_cal(path)

    np.testing.assert_array_equal(freqs, [20.0, 1000.0, 20000.0])
    np.testing.assert_array_equal(response, [0.5, 0.0, -2.5])


def test_mic_cal_missing_or_empty_is_none(tmp_path):
    empty = tmp_path / "mic_cal.txt"
    empty.write_text("nothing here\n", encoding="utf-8")

    assert files.load_mic_cal(tmp_path / "absent.txt") is None
    assert files.load_mic_cal(empty) is None


# ------------------------------------------------------------ undo, pending
def test_undo_file_round_trip(ws):
    undo = UndoFile(
        created="2026-10-02T18:42:49",
        ip="192.0.2.80",
        scope=WriteScope.SUB,
        what="crossover LR24 100Hz",
        changes=(
            ChangeModel(
                path="/mtx/03/eq/6/type",
                old_value=4,
                new_value=13,
                old_display="HShv",
                new_display="LR24",
            ),
        ),
        results=(
            ResultModel(
                path="/mtx/03/eq/6/type",
                wanted=13,
                readback=13,
                node="/mtx/03/eq/6 LR24 101.4 -15.0 2.0",
                ok=True,
            ),
        ),
    )
    path = ws.undo_path(WriteScope.SUB, NOW)

    files.write_json(path, undo)

    assert files.read_undo(path) == undo


def test_pending_mutes_round_trip_and_clear(ws):
    assert files.read_pending_mutes(ws) is None
    pending = PendingMutes(created="t", mtx_on={1: True, 3: False})

    files.write_pending_mutes(ws, pending)

    assert files.read_pending_mutes(ws) == pending
    files.clear_pending_mutes(ws)
    files.clear_pending_mutes(ws)  # clearing twice is fine
    assert files.read_pending_mutes(ws) is None


# ------------------------------------------------------------ migrations from the original tool
LEGACY_BAND = {"band": 2, "type": "PEQ", "freq": 496.6, "gain": -4.0, "q": 1.2}
LEGACY_EQ = {
    "on": True,
    "bands": [{**LEGACY_BAND, "band": i} for i in range(1, 7)],
}


def test_legacy_session_file_is_read(tmp_path):
    legacy = {
        "name": "after-sub", "kind": "baseline", "base": None, "created": "2026-10-02T18:57:10",
        "eq_at_measurement": LEGACY_EQ, "fs": 48000, "sweep": {"f1": 20.0, "f2": 20000.0, "seconds": 6.0},
    }  # fmt: skip
    (tmp_path / "session.json").write_text(json.dumps(legacy), encoding="utf-8")

    stored = files.read_session(tmp_path)

    assert stored.schema_version == 1
    assert stored.name == "after-sub"
    assert stored.eq_at_measurement.to_domain().band(2) == Band(2, EqType.PEQ, 496.6, -4.0, 1.2)


def test_legacy_point_meta_turns_text_warnings_into_coded_ones():
    legacy = {
        **point_meta().model_dump(mode="json"),
        "warnings": ["пик -5.1 dBFS — близко к клиппингу"],
    }
    del legacy["schema_version"]

    meta = PointMeta.model_validate(migrate("point_meta", legacy))

    assert meta.warnings == (
        CodedText(code="legacy", params={"text": "пик -5.1 dBFS — близко к клиппингу"}),
    )


def test_legacy_undo_file_without_scope_is_a_main_scope_undo():
    legacy = {
        "created": "2026-10-02T19:19:41", "ip": "192.0.2.80", "proposal": "eq_proposal.json",
        "eq_before": LEGACY_EQ,
        "changes": [{"path": "/main/st/eq/2/g", "old_value": 0.36666666666666664, "new_value": 0.2,
                     "old_display": "-4.00 dB", "new_display": "-9.00 dB"}],
        "eq_after": LEGACY_EQ,
        "results": [{"path": "/main/st/eq/2/g", "wanted": 0.2, "readback": 0.20000000298023224,
                     "node": "/main/st/eq/2 PEQ 479.8 -9.00 1.5", "ok": True}],
    }  # fmt: skip

    undo = UndoFile.model_validate(migrate("undo", legacy))

    assert undo.scope is WriteScope.MAIN
    assert undo.changes[0].to_domain().new_value == 0.2
    assert undo.results[0].ok is True


def test_legacy_sub_undo_and_pending_files_are_read(ws):
    legacy_undo = {
        "created": "2026-10-02T18:50:31", "ip": "192.0.2.80", "scope": "sub", "what": "align d=0.0ms pol=1",
        "changes": [{"path": "/mtx/03/mix/fader", "old_value": 0.2033, "new_value": 0.2405,
                     "old_display": "-37.5", "new_display": "-31.5"}],
        "results": [],
    }  # fmt: skip
    ws.root.mkdir(parents=True, exist_ok=True)
    ws.pending_mutes_file.write_text(
        json.dumps({"created": "t", "mtx_on": {"1": 1, "3": 0}}), encoding="utf-8"
    )

    undo = UndoFile.model_validate(migrate("undo", legacy_undo))
    pending = files.read_pending_mutes(ws)

    assert (undo.scope, undo.what) == (WriteScope.SUB, "align d=0.0ms pol=1")
    assert pending.mtx_on == {1: True, 3: False}


def test_legacy_proposal_is_read_with_text_reasons_kept(tmp_path):
    legacy = {
        "created": "2026-10-02T19:19:31", "session": "after-sub", "points": 6, "method": "optimize",
        "target": {"tilt_db_per_oct": -1.0, "tilt_from_hz": 1000.0, "bass_lift_db": 3.0, "description": "…"},
        "f_low_hz": 26.3, "eq_on": True,
        "bands": [
            {**LEGACY_BAND, "band": i, "action": "keep", "was": "PEQ ...", "now": "PEQ ...",
             "approximate": False, "reason": "без изменений"}
            for i in range(1, 7)
        ],
        "skipped": [{"kind": "boost", "freq_hz": 800.0, "dev_db": -7.9, "width_oct": 0.23, "points": 6,
                     "reason": "узкий провал"}],
        "warnings": [], "prediction": {"rms_dev_before_db": 2.81, "rms_dev_after_db": 2.36,
                                       "max_dev_before_db": 5.86, "max_dev_after_db": 4.33,
                                       "range_hz": [35.1, 10000.0]},
        "eq_at_measurement": LEGACY_EQ, "eq_current": LEGACY_EQ, "note": "…",
    }  # fmt: skip
    path = tmp_path / "eq_proposal.json"
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    proposal = files.read_proposal(path)

    assert isinstance(proposal, ProposalFile)
    assert proposal.method == "optimize"
    assert proposal.bands[1].was.to_domain() == Band(2, EqType.PEQ, 496.6, -4.0, 1.2)
    assert proposal.bands[1].reason == CodedText(code="legacy", params={"text": "без изменений"})
    assert proposal.skipped[0].reason.params == {"text": "узкий провал"}
    assert proposal.target.bass_from_hz == 100.0
    assert proposal.prediction.range_hz == (35.1, 10000.0)


def test_current_version_files_pass_through_migration_unchanged():
    data = session().model_dump(mode="json")

    assert migrate("session", data) == data


@pytest.mark.parametrize("version", [2, 99, -1, "1", 1.0, True, None])
def test_unknown_schema_versions_are_refused(version):
    with pytest.raises(UnsupportedSchema, match="unsupported schema_version"):
        migrate("session", {"schema_version": version})


def test_migration_does_not_mutate_its_input():
    legacy = {"created": "t", "changes": []}

    migrate("undo", legacy)

    assert legacy == {"created": "t", "changes": []}


@pytest.mark.parametrize(
    "value", ["0.5", True, None, float("nan"), float("inf"), -float("inf")], ids=repr
)
def test_change_values_must_be_real_finite_numbers(value):
    """A value that goes on the wire is never coerced from a string or a boolean."""
    for field in ("old_value", "new_value"):
        fields = {"path": "/main/st/eq/1/g", "old_value": 0.5, "new_value": 0.5, field: value}
        with pytest.raises(ValidationError):
            ChangeModel(old_display="a", new_display="b", **fields)


@pytest.mark.parametrize("matrix", [0, 7, -1])
def test_pending_mutes_name_only_matrices_that_exist(matrix):
    with pytest.raises(ValidationError, match="no such matrices"):
        PendingMutes(created="t", mtx_on={1: True, matrix: False})
