"""The step engine: retry, errors, Ctrl+C, the stop flag and the UI going away."""

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from churchtune.errors import ConsoleUnreachable, DevSafeViolation
from churchtune.storage.paths import Workspace
from churchtune.wizard.engine import Engine
from churchtune.wizard.protocol import Answer, Nav, Prompt
from churchtune.wizard.run import new_state
from churchtune.wizard.services import Services, WizardContext
from churchtune.wizard.state import Outcome, StateBox

NOW = datetime(2026, 10, 14, tzinfo=UTC)


@dataclass
class Prompter:
    answers: list[Answer] = field(default_factory=list)
    shown: list[Prompt] = field(default_factory=list)

    def header(self, header):
        pass

    def ask(self, prompt):
        self.shown.append(prompt)
        return self.answers.pop(0) if self.answers else Answer("")

    def progress(self, label, done, total):
        pass

    def meter(self, levels_dbfs):
        pass

    def keys(self):
        return [text.key for prompt in self.shown for text in prompt.body]


@dataclass
class Step:
    id: str
    outcomes: list[object]
    title: str = "step-mac-title"
    changes_console: bool = False
    runs: int = 0
    stop_seen: list[bool] = field(default_factory=list)

    def applies(self, state):
        return True

    def run(self, ctx):
        self.runs += 1
        self.stop_seen.append(ctx.services.stop.is_set())
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def engine(tmp_path: Path, steps, answers=()):
    services = Services(
        console=None,  # type: ignore[arg-type]
        audio=None,  # type: ignore[arg-type]
        workspace=Workspace(tmp_path),
        now=lambda: NOW,
        stop=threading.Event(),
        dev_safe=True,
    )
    prompter = Prompter(list(answers))
    ctx = WizardContext(prompter, StateBox(new_state("en", NOW)), services, tmp_path)
    saved = []
    return Engine(steps, ctx, saved.append), prompter, saved


def test_retry_runs_the_same_step_again_without_completing_it(tmp_path):
    step = Step("a", [Outcome.RETRY, Outcome.DONE])
    run, _, saved = engine(tmp_path, [step])
    result = run.run()
    assert result.finished
    assert step.runs == 2
    assert result.state.completed == ("a",)
    assert len(saved) == 1


def test_an_error_is_explained_and_can_be_retried(tmp_path):
    step = Step("a", [ConsoleUnreachable("gone"), Outcome.DONE])
    run, prompter, _ = engine(tmp_path, [step], [Answer("retry")])
    assert run.run().finished
    assert prompter.keys()[:2] == ["err-console-unreachable", "err-what-now"]


def test_an_error_then_save_and_quit_keeps_the_state(tmp_path):
    first = Step("a", [Outcome.DONE])
    second = Step("b", [ConsoleUnreachable("gone")])
    run, _, saved = engine(tmp_path, [first, second], [Answer("quit")])
    result = run.run()
    assert not result.finished
    assert saved[-1].completed == ("a",)


def test_back_or_quit_on_the_error_screen(tmp_path):
    step = Step("a", [ConsoleUnreachable("gone")])
    run, _, _ = engine(tmp_path, [step], [Answer(nav=Nav.QUIT)])
    assert not run.run().finished


def test_ctrl_c_in_a_line_interface_saves_and_silences(tmp_path):
    first = Step("a", [Outcome.DONE])
    second = Step("b", [KeyboardInterrupt()])
    run, _, saved = engine(tmp_path, [first, second])
    result = run.run()
    assert not result.finished
    assert run.ctx.services.stop.is_set()
    assert saved[-1].completed == ("a",)


def test_the_stop_flag_is_cleared_before_each_step(tmp_path):
    first = Step("a", [Outcome.DONE])
    second = Step("b", [Outcome.DONE])
    run, _, _ = engine(tmp_path, [first, second])
    run.ctx.services.stop.set()  # Space pressed on a quiet screen
    assert run.run().finished
    assert second.stop_seen == [False]


def test_nothing_runs_once_the_ui_is_gone(tmp_path):
    step = Step("a", [Outcome.DONE])
    run, _, _ = engine(tmp_path, [step])
    run.ctx.services.shutdown.set()
    result = run.run()
    assert not result.finished
    assert step.runs == 0
    assert run.ctx.services.stop.is_set()


def test_reset_stop_never_clears_after_shutdown(tmp_path):
    run, _, _ = engine(tmp_path, [])
    services = run.ctx.services
    services.stop.set()
    assert services.reset_stop()
    assert not services.stop.is_set()
    services.shutdown.set()
    assert not services.reset_stop()
    assert services.stop.is_set()


def test_a_guard_error_offers_only_save_and_quit(tmp_path):
    step = Step("a", [DevSafeViolation("real console")])
    run, prompter, _ = engine(tmp_path, [step])
    assert not run.run().finished
    assert [o.id for o in prompter.shown[0].options] == ["quit"]


def test_a_damaged_file_is_explained(tmp_path):
    step = Step("a", [ValueError("broken json"), Outcome.DONE])
    run, prompter, _ = engine(tmp_path, [step], [Answer("retry")])
    assert run.run().finished
    assert prompter.keys()[0] == "err-bad-file"
