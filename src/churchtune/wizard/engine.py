"""The wizard's step engine: applicable steps, numbering, navigation, saving (SPEC §6.2, §7).

A step is an object with an id, a title key, ``applies(state)`` and ``run(ctx)``. The engine shows
"Step n of m" counting only the steps that apply (the total is recomputed when a step changes the
configuration), saves the state after every completed step and resumes after the last one. "Back"
goes to the previous step that applies, except across a step that changed the console.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from churchtune.errors import BadFile, ChurchTuneError, MeasurementAborted
from churchtune.storage.models import WizardStateFile
from churchtune.wizard.protocol import Option, StepHeader, Text
from churchtune.wizard.services import WizardContext
from churchtune.wizard.state import Navigate, Outcome
from churchtune.wizard.ui import choose

RETRY, QUIT = "retry", "quit"


class Step(Protocol):
    """One step of the wizard."""

    @property
    def id(self) -> str:
        """Stable id, stored in ``completed``."""
        ...

    @property
    def title(self) -> str:
        """Message key of the step name."""
        ...

    @property
    def changes_console(self) -> bool:
        """A step that changed the console cannot be left with "Back" (SPEC §6.2: "Undo")."""
        ...

    def applies(self, state: WizardStateFile) -> bool:
        """True when this step is part of the session for this goal and configuration."""
        ...

    def run(self, ctx: WizardContext) -> Outcome:
        """Run the step."""
        ...


@dataclass(frozen=True, slots=True)
class RunResult:
    """How the engine ended.

    Attributes:
        finished: True when every applicable step completed.
        state: The final state.
    """

    finished: bool
    state: WizardStateFile


@dataclass(slots=True)
class Engine:
    """Runs steps in order with navigation and saving.

    Attributes:
        steps: Every step in SPEC order.
        ctx: Context handed to each step.
        save: Persists the state; called after every completed step and on quit.
    """

    steps: Sequence[Step]
    ctx: WizardContext
    save: Callable[[WizardStateFile], None]

    def applicable(self) -> list[Step]:
        """Steps that apply to the current state, in order."""
        state = self.ctx.box.state
        return [s for s in self.steps if s.applies(state)]

    def _next(self) -> Step | None:
        done = set(self.ctx.box.state.completed)
        return next((s for s in self.applicable() if s.id not in done), None)

    def _header(self, step: Step) -> StepHeader:
        steps = self.applicable()
        number = next(i for i, s in enumerate(steps, start=1) if s.id == step.id)
        venue = self.ctx.box.state.venue_name
        return StepHeader(number, len(steps), Text(step.title), venue)

    def _go_back(self, step: Step) -> None:
        """Forget the previous applicable step so it runs again; never across a console change."""
        steps = self.applicable()
        index = next(i for i, s in enumerate(steps) if s.id == step.id)
        if index == 0:
            return
        previous = steps[index - 1]
        if previous.changes_console:
            return
        box = self.ctx.box
        box.update(completed=tuple(c for c in box.state.completed if c != previous.id))

    def _failed(self, error: ChurchTuneError) -> Outcome:
        """Explain an error in the person's language; repeat the step or save and quit."""
        args = {k: v for k, v in error.context.items() if isinstance(v, str | int | float)}
        options = [Option(QUIT, Text("common-save-quit"))]
        if error.retryable:
            options.insert(0, Option(RETRY, Text("common-retry"), True))
        try:
            again = choose(
                self.ctx.prompter, [Text(error.i18n_key, args), Text("err-what-now")], options
            )
        except Navigate as navigation:
            return navigation.outcome
        return Outcome.RETRY if again == RETRY else Outcome.QUIT

    def _run_step(self, step: Step) -> Outcome:
        try:
            return step.run(self.ctx)
        except Navigate as navigation:
            return navigation.outcome
        except MeasurementAborted:
            # Ctrl+C / Space: the sound is already off (the measurement stops its stream);
            # save and leave so the person can resume (SPEC §6.2).
            return Outcome.QUIT
        except ChurchTuneError as error:
            return self._failed(error)
        except (ValueError, OSError) as error:  # a damaged or missing stored file
            return self._failed(BadFile(str(error)))

    def run(self) -> RunResult:
        """Run until every step is done or the person quits.

        The stop flag is cleared before each step: a Space pressed on a quiet screen must not
        cut the next recording. Ctrl+C in a line interface (``KeyboardInterrupt``) saves and quits.
        """
        services = self.ctx.services
        box = self.ctx.box
        try:
            while (step := self._next()) is not None:
                if not services.reset_stop():
                    break
                self.ctx.prompter.header(self._header(step))
                outcome = self._run_step(step)
                if outcome is Outcome.QUIT:
                    break
                if outcome is Outcome.BACK:
                    self._go_back(step)
                    continue
                if outcome is Outcome.RETRY:
                    continue
                box.update(completed=(*box.state.completed, step.id))
                self.save(box.state)
            else:
                return RunResult(True, box.state)
        except KeyboardInterrupt:
            services.stop.set()
        self.save(box.state)
        return RunResult(False, box.state)
