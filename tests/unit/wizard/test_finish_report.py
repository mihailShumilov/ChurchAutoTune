"""S14 writes the report (SPEC §10) and never fails the wizard over it."""

import threading
from datetime import UTC, datetime

import pytest

from churchtune.i18n import Translator
from churchtune.storage.paths import Workspace
from churchtune.wizard import reporting
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
