"""The wizard state holder and step outcomes, shared by the engine, steps and UI helpers."""

from dataclasses import dataclass
from enum import StrEnum

from churchtune.storage.models import WizardStateFile


class Outcome(StrEnum):
    """How a step ended."""

    DONE = "done"
    BACK = "back"
    # Run the same step again from its start (a failed check the person wants to repeat).
    RETRY = "retry"
    QUIT = "quit"


class Navigate(Exception):
    """Raised by prompt helpers when the person chose Back or Save & quit."""

    def __init__(self, outcome: Outcome) -> None:
        super().__init__(outcome.value)
        self.outcome = outcome


@dataclass(slots=True)
class StateBox:
    """The current wizard state; steps replace it with ``update``."""

    state: WizardStateFile

    def update(self, **changes: object) -> WizardStateFile:
        """Replace fields of the state (it is immutable) and return the new state."""
        self.state = self.state.model_copy(update=changes)
        return self.state
