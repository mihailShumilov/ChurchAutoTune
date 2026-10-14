"""S12 asks a separate "yes" for a sub level far from the venue's first snapshot (SPEC §9.2)."""

from types import SimpleNamespace

import pytest

from churchtune.domain.snapshot import SignalRef, SourceKind
from churchtune.wizard.protocol import Answer
from churchtune.wizard.services import PlannedWrite, WritePlan
from churchtune.wizard.steps import apply as step

SUB = SignalRef(SourceKind.MATRIX, 3)


class Prompter:
    def __init__(self, answer: str):
        self.answer = answer
        self.keys: list[str] = []

    def ask(self, prompt):
        self.keys += [text.key for text in prompt.body]
        return Answer(self.answer)


def plan(after_db: float) -> WritePlan:
    write = PlannedWrite("/mtx/03/mix/fader", SUB, None, "fader", "-30", "x", -30.0, after_db)
    return WritePlan((write,), token=None)


@pytest.mark.parametrize(
    ("first_db", "after_db", "answer", "asked", "ok"),
    [
        (-30.0, -20.0, "", False, True),  # within 12 dB: no extra question
        (-30.0, -15.0, "no", True, False),  # 15 dB away: the default is no
        (-30.0, -15.0, "yes", True, True),
        (None, -15.0, "", False, True),  # no first snapshot: nothing to compare with
    ],
)
def test_a_far_sub_level_needs_a_separate_yes(monkeypatch, first_db, after_db, answer, asked, ok):
    monkeypatch.setattr(step, "first_snapshot", lambda ctx: object())
    monkeypatch.setattr(step, "first_fader_db", lambda snapshot, strip: first_db)
    prompter = Prompter(answer)
    ctx = SimpleNamespace(prompter=prompter)
    assert step._far_levels_confirmed(ctx, plan(after_db)) is ok
    assert ("apply-level-far" in prompter.keys) is asked
