"""``apply-packages`` and its undo end to end against the emulator (SPEC S12, ADR 0010).

Never weaken, skip or xfail these tests: they are part of the safety suite.
"""

import dataclasses
import json
from dataclasses import replace
from datetime import datetime

import pytest
from typer.testing import CliRunner

from churchtune.cli.context import EXIT_OK, EXIT_REFUSED, EXIT_USER
from churchtune.cli.main import app
from churchtune.domain.eq import EqType, SystemEq
from churchtune.errors import UnsafeWrite
from churchtune.mixers.x32 import params as P  # noqa: N812 - short alias as elsewhere
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


# ------------------------------------------------------------ regressions of the M5 safety review
def eq_proposal(home, kind, ref, before, after) -> str:
    package = PackageModel(
        kind=kind,
        suggest_only=False,
        risk="low",
        selected=True,
        eq=(
            PathEqModel(
                path=ref, before=EqModel.from_domain(before), after=EqModel.from_domain(after)
            ),
        ),
    )
    path = home / "proposal.json"
    stored = PackagesFile(created="2026-10-14T10:00:00", session="s", fc_hz=100.0,
                          packages=(package,))  # fmt: skip
    files.write_json(path, stored)
    return str(path)


MAIN_REF = PathRefModel(kind="main_l")


def test_a_package_never_switches_on_an_eq_with_a_hidden_boost(emu, home, run, client):
    emu.hand_change("/main/st/eq/on", 0)
    emu.hand_change("/main/st/eq/2/type", 2)
    emu.hand_change("/main/st/eq/2/g", P.gain_to_norm(15.0))  # a leftover of an old scene
    before = read_console_state(client).main_eq
    proposal = eq_proposal(home, "system_eq", MAIN_REF, before, before.with_on(True))
    result = run(["apply-packages", proposal], answers=["применить"])
    assert result.exit_code == EXIT_REFUSED, result.output
    assert emu.writes == []
    assert not emu.main_eq().on


@pytest.mark.parametrize(
    "change",
    [
        {"type": EqType.PEQ, "gain_db": 0.0},  # removing the top's low cut
        {"freq_hz": 400.0},  # moving it
    ],
)
def test_only_the_crossover_package_touches_a_cut_filter(emu, home, run, client, change):
    before = read_console_state(client).matrices[1].eq
    assert not before.band(1).adjustable  # the reference hall has a low cut on the tops
    after = before.with_band(replace(before.band(1), **change))
    ref = PathRefModel(kind="matrix", number=1)
    result = run(["apply-packages", eq_proposal(home, "system_eq", ref, before, after)],
                 answers=["применить"])  # fmt: skip
    assert result.exit_code == EXIT_REFUSED, result.output
    assert emu.writes == []


def _port(client, transport):
    from churchtune.cli.context import Console  # noqa: PLC0415
    from churchtune.cli.package_writes import X32WritePort  # noqa: PLC0415

    return X32WritePort(lambda: (LOOPBACK, Console(transport, client)), datetime.now)


def _two_packages(client, sub_step_db):
    state = read_console_state(client)
    fader = state.matrices[SUB_MATRIX].fader_db
    level = PackageModel(
        kind="sub_level", suggest_only=False, risk="low", selected=True,
        levels=(LevelModel(path=PathRefModel(kind="matrix", number=SUB_MATRIX),
                           before_db=fader, after_db=fader + sub_step_db),),
    )  # fmt: skip
    eq = PackageModel(
        kind="system_eq", suggest_only=False, risk="low", selected=True,
        eq=(PathEqModel(path=MAIN_REF, before=EqModel.from_domain(state.main_eq),
                        after=EqModel.from_domain(cut(state.main_eq))),),
    )  # fmt: skip
    return [eq, level], fader


def test_the_port_refuses_a_package_whose_before_no_longer_matches(
    emu, client, transport, tmp_path
):
    packages, fader = _two_packages(client, -3.0)
    port = _port(client, transport)
    plan = port.plan_packages(packages)
    emu.hand_change(f"/mtx/{SUB_MATRIX:02d}/mix/fader", P.fader_to_norm(fader - 12.0))
    with pytest.raises(UnsafeWrite, match="console changed") as refused:
        port.apply(plan, "применить", "применить", tmp_path / "u.json", lambda: False)
    assert "mismatches" in refused.value.context
    assert emu.writes == []
    assert not (tmp_path / "u.json").exists()


def test_the_cli_refuses_a_console_changed_while_the_word_was_typed(
    emu, home, run, client, monkeypatch
):
    from churchtune.cli import apply_packages  # noqa: PLC0415

    proposal = write_proposal(home, client, level_db=-3.0)
    real_ask = apply_packages.ask

    def ask_and_move(prompt):
        emu.hand_change("/main/st/eq/3/g", P.gain_to_norm(-12.0))  # while the person reads
        return real_ask(prompt)

    monkeypatch.setattr(apply_packages, "ask", ask_and_move)
    result = run(["apply-packages", proposal], answers=["применить"])
    assert result.exit_code == EXIT_REFUSED, result.output
    assert emu.writes == []


def test_a_parameter_written_twice_goes_back_to_its_first_value(emu, home, run, client):
    emu.hand_change("/main/st/eq/5/type", P.EQ_TYPES.index(EqType.HSHV))
    emu.hand_change("/main/st/eq/5/f", P.freq_to_norm(8000.0))
    emu.hand_change("/main/st/eq/5/g", P.gain_to_norm(2.0))
    before = read_console_state(client).main_eq
    original = before.band(5)
    after = before.with_band(replace(original, type=EqType.LSHV, freq_hz=60.0, gain_db=-2.0))
    applied = run(["apply-packages", eq_proposal(home, "system_eq", MAIN_REF, before, after)],
                  answers=["применить"])  # fmt: skip
    assert applied.exit_code == EXIT_OK, applied.output
    assert emu.main_eq().band(5).gain_db == pytest.approx(-2.0, abs=0.13)
    (undo,) = undo_files(home)
    back = run(["undo", str(undo)], answers=["вернуть"])
    assert back.exit_code == EXIT_OK, back.output
    assert "руками" not in back.output
    now = emu.main_eq().band(5)
    assert now.type is EqType.HSHV
    assert now.gain_db == pytest.approx(2.0, abs=0.13)


def test_a_shelf_that_moves_keeps_its_gain(emu, home, run, client):
    emu.hand_change("/main/st/eq/5/type", P.EQ_TYPES.index(EqType.HSHV))
    emu.hand_change("/main/st/eq/5/f", P.freq_to_norm(8000.0))
    emu.hand_change("/main/st/eq/5/g", P.gain_to_norm(2.0))
    before = read_console_state(client).main_eq
    after = before.with_band(replace(before.band(5), type=EqType.LSHV, freq_hz=60.0))
    result = run(["apply-packages", eq_proposal(home, "system_eq", MAIN_REF, before, after)],
                 answers=["применить"])  # fmt: skip
    assert result.exit_code == EXIT_OK, result.output
    band = emu.main_eq().band(5)
    assert band.type is EqType.LSHV
    assert band.gain_db == pytest.approx(2.0, abs=0.13)


def test_a_package_that_needs_an_advice_package_is_left_out(emu, home, run, client):
    state = read_console_state(client)
    align = PackageModel(kind="alignment", suggest_only=False, risk="unverified", selected=True,
                         delay_side="sub", delay_ms=1.5)  # fmt: skip
    path_eq = PathEqModel(path=MAIN_REF, before=EqModel.from_domain(state.main_eq),
                          after=EqModel.from_domain(cut(state.main_eq)))  # fmt: skip
    eq = PackageModel(kind="system_eq", suggest_only=False, risk="low", selected=True,
                      requires=("alignment",), eq=(path_eq,))  # fmt: skip
    stored = PackagesFile(created="2026-10-14T10:00:00", session="s", fc_hz=100.0,
                          packages=(align, eq))  # fmt: skip
    files.write_json(home / "proposal.json", stored)
    result = run(["apply-packages", str(home / "proposal.json")], answers=["применить"])
    assert result.exit_code == EXIT_OK, result.output
    assert emu.writes == []


def test_a_run_refused_after_an_earlier_run_reports_a_partial_write(
    emu, client, transport, tmp_path, monkeypatch
):
    from churchtune.cli import package_writes  # noqa: PLC0415

    packages, _ = _two_packages(client, -3.0)
    port = _port(client, transport)
    plan = port.plan_packages(packages)
    real = package_writes.authorize_typed
    calls = []

    def refuse_the_second_run(*args, **kwargs):
        calls.append(args[0])
        if len(calls) > 1:
            raise UnsafeWrite("refused")
        return real(*args, **kwargs)

    monkeypatch.setattr(package_writes, "authorize_typed", refuse_the_second_run)
    outcome = port.apply(plan, "применить", "применить", tmp_path / "u.json", lambda: False)
    assert len(calls) == 2
    assert not outcome.complete
    assert outcome.sent > 0
    assert files.read_undo(tmp_path / "u.json").results  # what was written is on record


# ------------------------------------------------------------ regressions of the second review
def _forged_undo(home, changes) -> str:
    path = home / "undo" / "undo_pkg_forged.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"created": "2026-10-14T10:00:00", "scope": "system_eq",
                                "what": "packages", "changes": changes}), encoding="utf-8")  # fmt: skip
    return str(path)


def test_an_undo_file_never_switches_on_an_eq(emu, home, run):
    emu.hand_change("/main/st/eq/on", 0)
    emu.hand_change("/main/st/eq/5/g", P.gain_to_norm(15.0))
    undo = _forged_undo(home, [{"path": "/main/st/eq/on", "old_value": 1, "new_value": 0,
                                "old_display": "ON", "new_display": "OFF",
                                "scope": "system_eq"}])  # fmt: skip
    result = run(["undo", undo], answers=["вернуть"])
    assert result.exit_code == EXIT_REFUSED, result.output
    assert emu.writes == []


def test_an_undo_file_never_moves_a_tops_high_pass_out_of_range(emu, home, run, client):
    now = read_console_state(client).matrices[1].eq.band(1)
    current = P.quantize_freq(P.freq_to_norm(now.freq_hz))
    undo = _forged_undo(home, [{"path": "/mtx/01/eq/1/f", "old_value": P.freq_to_norm(16000.0),
                                "new_value": current, "old_display": "16k", "new_display": "x",
                                "scope": "system_eq"}])  # fmt: skip
    result = run(["undo", undo], answers=["вернуть"])
    assert result.exit_code == EXIT_REFUSED, result.output
    assert emu.writes == []


@pytest.mark.parametrize(
    ("number", "band", "change"),
    [
        (0, 1, {"type": EqType.PEQ}),  # Main's subsonic filter
        (1, 1, {"type": EqType.PEQ, "gain_db": 0.0}),  # the tops' high-pass removed
        (1, 1, {"freq_hz": 1490.0}),  # moved far out of a crossover's range
    ],
)
def test_even_the_crossover_package_keeps_the_cut_filters_in_their_place(
    emu, home, run, client, number, band, change
):
    state = read_console_state(client)
    before = state.main_eq if number == 0 else state.matrices[number].eq
    assert not before.band(band).adjustable, "the fixture must have a cut filter here"
    after = before.with_band(replace(before.band(band), **change))
    ref = MAIN_REF if number == 0 else PathRefModel(kind="matrix", number=number)
    result = run(["apply-packages", eq_proposal(home, "crossover", ref, before, after)],
                 answers=["применить"])  # fmt: skip
    assert result.exit_code == EXIT_REFUSED, result.output
    assert emu.writes == []


def test_an_undo_after_a_stop_puts_back_a_neutralised_band(emu, home, client, transport, tmp_path):
    emu.hand_change("/main/st/eq/5/type", P.EQ_TYPES.index(EqType.HSHV))
    emu.hand_change("/main/st/eq/5/f", P.freq_to_norm(8000.0))
    emu.hand_change("/main/st/eq/5/g", P.gain_to_norm(-6.0))
    before = read_console_state(client).main_eq
    after = before.with_band(replace(before.band(5), type=EqType.LSHV, freq_hz=60.0))
    package = PackageModel(kind="system_eq", suggest_only=False, risk="low", selected=True,
                           eq=(PathEqModel(path=MAIN_REF, before=EqModel.from_domain(before),
                                           after=EqModel.from_domain(after)),))  # fmt: skip
    port = _port(client, transport)
    plan = port.plan_packages([package])
    asked = []

    def stop_after_the_first_write():
        asked.append(1)
        return len(asked) > 1

    outcome = port.apply(plan, "применить", "применить", tmp_path / "u.json",
                         stop_after_the_first_write)  # fmt: skip
    assert outcome.stopped
    assert emu.main_eq().band(5).gain_db == pytest.approx(0.0, abs=0.13)  # neutralised, louder
    back = port.plan_undo(tmp_path / "u.json")
    assert "/main/st/eq/5/g" not in back.changed_by_hand
    port.apply(back, "вернуть", "вернуть", tmp_path / "r.json", lambda: False,
               reverts=tmp_path / "u.json")  # fmt: skip
    assert emu.main_eq().band(5).gain_db == pytest.approx(-6.0, abs=0.13)


def test_the_port_sends_only_the_plan_that_was_shown(emu, client, transport, tmp_path):
    from churchtune.cli.package_writes import _PortPlan  # noqa: PLC0415

    packages, _ = _two_packages(client, -3.0)
    port = _port(client, transport)
    shown = port.plan_packages(packages)
    token = shown.token
    assert isinstance(token, _PortPlan)
    # A fresh plan that differs although every package still matches its "before".
    other = _PortPlan(lambda state, policy: token.recipe(state, policy)[:-1], token.planned,
                      token.allow_cut_filters)  # fmt: skip
    tampered = dataclasses.replace(shown, token=other)
    with pytest.raises(UnsafeWrite, match="changed while the plan was shown"):
        port.apply(tampered, "применить", "применить", tmp_path / "u.json", lambda: False)
    assert emu.writes == []
    assert not (tmp_path / "u.json").exists()
