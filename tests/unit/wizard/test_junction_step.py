"""S10b puts the matrix switches back, and says so honestly when the console does not confirm."""

from types import SimpleNamespace

from churchtune.wizard.protocol import Answer
from churchtune.wizard.steps import junction

MATRICES = junction.JunctionMatrices(sub=(3,), tops=(1, 2), names={3: "Sub buffer", 1: ""})


class Prompter:
    def __init__(self):
        self.keys: list[str] = []

    def ask(self, prompt):
        self.keys += [text.key for text in prompt.body]
        return Answer("")


class Session:
    def __init__(self, works_after: int):
        self.calls = 0
        self.works_after = works_after

    def restore(self) -> bool:
        self.calls += 1
        return self.calls > self.works_after


def ctx(sleeps: list[float]):
    return SimpleNamespace(prompter=Prompter(), services=SimpleNamespace(sleep=sleeps.append))


def test_the_restore_tries_again_every_two_seconds():
    sleeps: list[float] = []
    context = ctx(sleeps)
    assert junction._restore(context, Session(works_after=2), MATRICES)
    assert sleeps == [2.0, 2.0]
    assert context.prompter.keys == []


def test_a_restore_that_never_confirms_tells_the_person_which_buttons_to_check():
    sleeps: list[float] = []
    context = ctx(sleeps)
    assert not junction._restore(context, Session(works_after=99), MATRICES)
    assert len(sleeps) == junction.RESTORE_TRIES - 1
    assert context.prompter.keys == ["junction-restore-by-hand"]


def test_matrices_are_named_as_on_the_console():
    texts = MATRICES.texts()
    assert texts[0].key == "junction-matrix"
    assert texts[0].args["name"] == "Sub buffer"
    assert texts[1].key == "signal-matrix"  # no name: just "matrix 1"
