"""The wizard commands on the emulator and the simulated hall, and the plain line interface."""

import io

import pytest
from typer.testing import CliRunner

from churchtune.cli.context import EXIT_USER
from churchtune.cli.main import app
from churchtune.cli.wizard import LinePrompter
from churchtune.i18n import Translator
from churchtune.wizard.protocol import Answer, Kind, Nav, Option, Prompt, StepHeader, Table, Text


def invoke(home, *argv, answers=()):
    return CliRunner().invoke(
        app,
        ["--home", str(home), "--audio", "fake", "--emulator", *argv],
        input="".join(f"{a}\n" for a in answers),
        catch_exceptions=False,
    )


def test_plain_ctune_starts_the_wizard_and_quits_with_a_saved_session(tmp_path):
    result = invoke(tmp_path, answers=["", "Big hall", "", "q"])
    assert result.exit_code == EXIT_USER, result.output
    assert "Step 1 of" in result.output
    sessions = list((tmp_path / "venues" / "big-hall" / "sessions").iterdir())
    assert len(sessions) == 1
    assert (sessions[0] / "session.json").is_file()


def test_resume_continues_after_the_last_finished_step(tmp_path):
    invoke(tmp_path, "wizard", "--lang", "en", answers=["", "Big hall", "", "q"])
    result = invoke(tmp_path, "resume", "--lang", "en", answers=["q"])
    assert result.exit_code == EXIT_USER, result.output
    assert "Step 1 of" not in result.output
    assert "Mac check" in result.output


def test_wizard_finds_the_emulator_and_reaches_the_console_step(tmp_path):
    result = invoke(tmp_path, "wizard", "--lang", "en", answers=["", "Big hall", "", "", "q"])
    assert "is this your console?" in result.output.lower(), result.output


def test_resume_without_a_session_says_how_to_start(tmp_path):
    result = invoke(tmp_path, "resume", "--lang", "ru")
    assert result.exit_code == EXIT_USER
    assert "Сохранённой сессии пока нет" in result.output


def test_an_unknown_language_is_refused(tmp_path):
    assert invoke(tmp_path, "wizard", "--lang", "xx").exit_code == EXIT_USER


# ---------------------------------------------------------------------------- line interface
def t(key: str, **args: str | int | float) -> Text:
    return Text(key, args)


CHOICE = Prompt(
    Kind.CHOOSE,
    (t("step-welcome-goal"),),
    (Option("full", t("step-welcome-goal-full"), True), Option("sub", t("step-welcome-goal-sub"))),
    default="full",
    table=Table((t("check-what"),), ((t("check-ok"),),)),
    picture="[ stage ]",
)


def ask(monkeypatch, capsys, prompt: Prompt, typed: str) -> tuple[Answer, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(typed))
    answer = LinePrompter(Translator("en")).ask(prompt)
    return answer, capsys.readouterr().out


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("2\n", Answer("sub")),
        ("\n", Answer("")),
        ("9\n", Answer(nav=Nav.QUIT)),  # asked again, then the input ends
        ("Only the subwoofer\n", Answer("sub")),
        ("q\n", Answer(nav=Nav.QUIT)),
        ("b\n", Answer(nav=Nav.BACK)),
        ("", Answer(nav=Nav.QUIT)),
    ],
)
def test_line_answers(monkeypatch, capsys, typed, expected):
    answer, out = ask(monkeypatch, capsys, CHOICE, typed)
    assert answer == expected
    assert "  2) Only the subwoofer" in out
    assert "[ stage ]" in out
    assert "\n> " in out  # the hint has its own line, the answer goes after "> "


def test_back_is_ignored_where_the_screen_forbids_it(monkeypatch, capsys):
    no_back = Prompt(Kind.INPUT, (t("step-welcome-venue-name"),), can_back=False)
    answer, _ = ask(monkeypatch, capsys, no_back, "b\n")
    assert answer == Answer("b")


def test_the_line_interface_promises_only_ctrl_c(monkeypatch, capsys):
    warning = Prompt(Kind.CONFIRM, (t("sound-warning"), t("sound-stop-keys")))
    _, out = ask(monkeypatch, capsys, warning, "\n")
    assert "Ctrl+C stops the sound at once." in out
    assert "Space" not in out


def test_header_progress_and_throttled_meter_print_whole_lines(capsys):
    now = [0.0]
    line = LinePrompter(Translator("en"), clock=lambda: now[0])
    line.header(StepHeader(3, 12, t("step-connect-title")))
    for done in range(4):
        line.progress(t("step-read-progress"), done, 3)
    line.meter({"1": -20.0})
    now[0] = 0.5
    line.meter({"1": -19.5})  # too soon: skipped
    now[0] = 1.5
    line.meter({"1": -18.5})
    out = capsys.readouterr().out
    assert "== Step 3 of 12 · Console ==" in out
    assert "Reading the console 0/3" in out
    assert "Reading the console 3/3" in out
    assert "1/3" not in out
    assert "Microphone 1: -20 dBFS" in out
    assert "-19.5" not in out
    assert "Microphone 1: -18.5 dBFS" in out
    assert "\r" not in out


def test_a_choice_that_names_no_option_is_asked_again(monkeypatch, capsys):
    answer, out = ask(monkeypatch, capsys, CHOICE, "9\n2\n")
    assert answer == Answer("sub")
    assert "Type a number from 1 to 2." in out


@pytest.mark.parametrize(("typed", "expected"), [("да\n", "yes"), ("ні\n", "no"), ("y\n", "yes")])
def test_yes_and_no_words_in_three_languages(monkeypatch, capsys, typed, expected):
    question = Prompt(
        Kind.CONFIRM,
        (t("step-topology-correct"),),
        (Option("yes", t("common-yes"), True), Option("no", t("common-no"))),
        default="yes",
    )
    answer, out = ask(monkeypatch, capsys, question, typed)
    assert answer == Answer(expected)
    assert "Yes (recommended)" in out


def test_table_headers_and_the_default_are_spoken(monkeypatch, capsys):
    named = Prompt(Kind.INPUT, (t("step-welcome-venue-name"),), default="Church")
    _, out = ask(monkeypatch, capsys, named, "\n")
    assert "Enter keeps: Church" in out
    _, out = ask(monkeypatch, capsys, CHOICE, "1\n")
    assert "  What\n" in out


def test_the_playing_line_names_only_ctrl_c(capsys):
    LinePrompter(Translator("en")).progress(t("sound-playing"), 0, 1)
    out = capsys.readouterr().out
    assert "Ctrl+C stops it" in out
    assert "Space" not in out
