"""S14 writes the report (SPEC §10) and never fails the wizard over it."""

import threading
from datetime import UTC, datetime

import pytest
from rich.console import Console

from churchtune.app.widgets import prompt_renderable
from churchtune.i18n import Translator
from churchtune.report.html import render_msg
from churchtune.storage.paths import Workspace
from churchtune.wizard import reporting
from churchtune.wizard.protocol import Kind, Prompt, Text
from churchtune.wizard.run import new_state
from churchtune.wizard.services import Services, WizardContext
from churchtune.wizard.state import Outcome, StateBox
from churchtune.wizard.steps.finish import Finish
from tests.unit.wizard.test_engine import Prompter

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)


def context(tmp_path, language="uk"):
    services = Services(
        console=None,  # type: ignore[arg-type]
        audio=None,  # type: ignore[arg-type]
        workspace=Workspace(tmp_path),
        now=lambda: NOW,
        stop=threading.Event(),
        dev_safe=True,
        translate=Translator(language),
        language=language,
    )
    session = tmp_path / "session"
    session.mkdir()
    prompter = Prompter()
    state = new_state("en", NOW).model_copy(update={"venue": "test-hall", "venue_name": "Test"})
    return WizardContext(prompter, StateBox(state), services, session), prompter


def test_the_report_is_written_in_the_language_of_the_run(tmp_path):
    """A session started in English and resumed in Ukrainian: the page says lang="uk"."""
    ctx, prompter = context(tmp_path, "uk")
    assert Finish().run(ctx) is Outcome.DONE
    page = (ctx.session_dir / "report" / "report.html").read_text(encoding="utf-8")
    assert '<html lang="uk">' in page
    keys = prompter.keys()
    assert "step-finish-report" in keys
    assert "step-finish-saved" in keys


def test_a_failure_while_making_the_report_does_not_stop_the_wizard(tmp_path, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("a chart could not be drawn")

    monkeypatch.setattr(reporting, "report_data", broken)
    ctx, prompter = context(tmp_path)
    assert Finish().run(ctx) is Outcome.DONE
    keys = prompter.keys()
    assert "step-finish-report-failed" in keys
    assert "step-finish-saved" in keys  # the measurements are saved either way


@pytest.mark.parametrize("language", ["en", "ru"])
def test_without_a_translator_no_report_is_written(tmp_path, language):
    ctx, prompter = context(tmp_path, language)
    ctx.services.translate = None
    assert Finish().run(ctx) is Outcome.DONE
    assert not (ctx.session_dir / "report").exists()
    keys = prompter.keys()
    assert "step-finish-report" not in keys


@pytest.mark.parametrize("language", ["en", "ru", "uk"])
def test_the_year_of_the_session_has_no_thousands_separator(language):
    created = reporting._date("2026-10-14T10:00:00")
    text = render_msg(created, Translator(language))  # type: ignore[arg-type]  # a Msg here
    assert "2026" in text
    assert "10:00" in text


def test_a_long_path_on_the_last_screen_is_not_cut_off():
    """80 columns: the report's path and the open command wrap instead of ending in "…"."""
    path = "~/ChurchTune/venues/demo-church/sessions/20261014_100000-baseline/report/report.html"
    prompt = Prompt(Kind.INFO, (Text("report-place", {"name": path, "where": "x"}),))
    console = Console(width=80, record=True, color_system=None)
    console.print(prompt_renderable(Translator("en"), prompt))
    out = console.export_text()
    assert "…" not in out
    assert "report.html" in out.replace("\n", "")
