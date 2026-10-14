"""Step S12 (SPEC §7, §9.3): write the chosen packages, and the way back.

The step only shows and asks. The write port reads the console fresh, refuses a package whose
"before" no longer matches (the person re-measures), orders the writes so nothing is louder in
between, writes the undo file before the first write and reads every write back. The person
confirms by typing a word; a typo asks again, an empty line cancels. Output delay and polarity
are advice only in the wizard: the person sets them by hand after the writes (their writes were
never verified on hardware, ADR 0005).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from churchtune.errors import ConsoleUnreachable, UnsafeWrite
from churchtune.storage.files import read_packages
from churchtune.storage.models import PackageModel, WizardStateFile
from churchtune.wizard.applying import chosen_packages, ref_of
from churchtune.wizard.labels import label_text, output_text
from churchtune.wizard.protocol import Table, Text
from churchtune.wizard.services import (
    PlannedWrite,
    WizardContext,
    WriteOutcome,
    WritePlan,
    WritePort,
)
from churchtune.wizard.state import Outcome
from churchtune.wizard.steps.proposal import path_text
from churchtune.wizard.steps.setup import measuring
from churchtune.wizard.ui import confirm, info, kebab, t, task, typed_word

APPLY_WORD, UNDO_WORD = "apply-word", "undo-word"
# The English words count in every language: typing them is just as deliberate.
ENGLISH_WORDS = {APPLY_WORD: "apply", UNDO_WORD: "undo"}
WRITING_GOALS = frozenset({"full", "sub"})
REPLANS = 3  # the console changed while the list was shown: read it again at most this often
KHZ = 1000.0
APPLIED, NOTHING, REVERTED = "applied", "nothing", "reverted"


def write_text(write: PlannedWrite) -> Text:
    """Which parameter a write changes, in words: "Main LR, band 3: gain"."""
    where = path_text(write.strip)
    if write.band is None:
        return t(f"write-{kebab(write.param)}", path=where)
    return t(f"write-band-{write.param}", path=where, band=write.band)


def value_text(param: str, value: float | bool | str) -> Text:  # noqa: PLR0911 - one per unit
    """A value in the person's language: "138 Hz", "6.85 kHz", "−3 dB", "on"."""
    if isinstance(value, bool):
        return t("value-on") if value else t("value-off")
    if isinstance(value, str):
        return t("value-console", value=value)
    if param == "freq":
        if value >= KHZ:
            return t("value-khz", khz=round(value / KHZ, 2))
        return t("value-hz", hz=round(value))
    if param == "q":
        return t("value-plain", value=round(value, 1))
    if param == "fader" and math.isinf(value):
        return t("value-fader-down")
    return t("value-db", db=round(value, 1))


def write_table(plan: WritePlan) -> Table:
    """Parameter · now · after (on the console's grid)."""
    rows = tuple(
        (
            write_text(w),
            value_text(w.param, w.before_value),
            value_text(w.param, w.after_value),
        )
        for w in plan.writes
    )
    return Table((t("write-what"), t("change-before"), t("write-after")), rows)


def _advice(packages: Sequence[PackageModel]) -> list[Text]:
    """Output delays and polarity as things to set by hand."""
    lines: list[Text] = []
    for package in packages:
        lines.extend(
            t("apply-advice-delay", output=output_text("main", d.output), ms=d.after_ms)
            for d in package.delays
            if d.after_ms != d.before_ms
        )
        lines.extend(
            t("apply-advice-polarity", path=path_text(ref_of(p.path)))
            for p in package.polarities
            if p.after != p.before
        )
    return lines


def ask_word(ctx: WizardContext, key: str, *body: Text, table: Table) -> tuple[str, str] | None:
    """The typed word and the word it matched; None when the person cancelled (empty line).

    A word that matches neither the word of the session's language nor the English one is
    explained and asked again.
    """
    word = ctx.services.words(key)
    accepted = (word, ENGLISH_WORDS[key])
    mismatch: list[Text] = []
    while True:
        typed = typed_word(ctx.prompter, *mismatch, *body, table=table).strip()
        if not typed:
            return None
        for expected in accepted:
            if typed.casefold() == expected.casefold():
                return typed, expected
        mismatch = [t(f"{key.removesuffix('-word')}-word-mismatch", word=word)]


def undo_applied(ctx: WizardContext, undo_path: Path) -> bool:  # noqa: PLR0911 - one per case
    """Put back what an undo file recorded, after a fresh read, a table and the typed word.

    Returns:
        True when everything that could go back went back.
    """
    writes = ctx.services.writes
    if writes is None:
        return False
    try:
        plan = writes.plan_undo(undo_path)
    except UnsafeWrite:
        info(ctx.prompter, t("undo-refused"))
        return False
    notes: list[Text] = []
    if plan.changed_by_hand:
        notes.append(t("undo-changed-by-hand", count=len(plan.changed_by_hand)))
    if not plan.writes:
        info(ctx.prompter, t("undo-nothing"), *notes)
        ctx.box.update(applied_state=REVERTED)
        return True
    word = ctx.services.words(UNDO_WORD)
    answer = ask_word(
        ctx, UNDO_WORD, t("undo-what", count=len(plan.writes)), *notes,
        t("undo-type", word=word), table=write_table(plan),
    )  # fmt: skip
    if answer is None:
        info(ctx.prompter, t("undo-cancelled"))
        return False
    revert = ctx.services.workspace.new_undo_path(None, ctx.services.now(), revert=True)
    ctx.services.reset_stop()
    try:
        outcome = writes.apply(plan, *answer, revert, ctx.services.stop.is_set,
                               reverts=undo_path)  # fmt: skip
    except UnsafeWrite as error:
        changed = "mismatches" in error.context
        info(ctx.prompter, t("undo-console-changed") if changed else t("undo-cancelled"))
        return False
    except ConsoleUnreachable:
        info(ctx.prompter, t("apply-console-lost"))
        return False
    if outcome.complete:
        ctx.box.update(applied_state=REVERTED)
    info(ctx.prompter, t("undo-done") if outcome.complete else t("undo-incomplete"))
    return outcome.complete


class _ShownPlanChanged(Exception):
    """The console changed while the list was on the screen: read it again."""


def _send(
    ctx: WizardContext, writes: WritePort, plan: WritePlan, answer: tuple[str, str]
) -> WriteOutcome | None:
    """Send a confirmed plan; None (with the reason shown) when nothing was sent.

    Whatever happens, once the undo file exists the way back is remembered in the state.

    Raises:
        _ShownPlanChanged: The console changed while the list was shown; nothing was sent.
    """
    root = ctx.services.workspace.root
    undo_path = ctx.services.workspace.new_undo_path(None, ctx.services.now())
    ctx.services.reset_stop()
    try:
        return writes.apply(plan, *answer, undo_path, ctx.services.stop.is_set)
    except UnsafeWrite as error:
        if "mismatches" in error.context:
            raise _ShownPlanChanged from error
        info(ctx.prompter, t("apply-cancelled"))
    except ConsoleUnreachable:
        info(ctx.prompter, t("apply-console-lost"))
    finally:
        if undo_path.exists():
            ctx.box.update(applied_undo=str(undo_path.relative_to(root)))
    return None


def _failure_texts(outcome: WriteOutcome, plan: WritePlan) -> list[Text]:
    if outcome.stopped:
        return [t("apply-stopped", count=outcome.sent)]
    if outcome.failed_path is None:  # a later run was refused by the safety check
        return [t("apply-run-refused", count=outcome.sent)]
    write = next((w for w in plan.writes if w.path == outcome.failed_path), None)
    setting = write_text(write) if write is not None else t("value-console", value="?")
    return [t("apply-readback-failed", setting=setting, count=outcome.sent)]


def _refusal_text(error: UnsafeWrite) -> Text:
    mismatches = error.context.get("mismatches")
    if isinstance(mismatches, str) and mismatches:
        strips = dict.fromkeys(m.rsplit(": ", 1)[-1] for m in mismatches.split(", "))
        names = tuple(t("signal-main-lr") if s == "main_l" else label_text(s) for s in strips)
        return t("apply-console-changed", settings=names)
    return t("apply-refused")


@dataclass(frozen=True, slots=True)
class Apply:
    """S12: the chosen packages to the console, confirmed by a typed word."""

    id: str = "apply"
    title: str = "step-apply-title"
    changes_console: bool = True

    def applies(self, state: WizardStateFile) -> bool:
        """Goals that change the system (counted from the start, so the total stays put)."""
        return measuring(state) and state.goal in WRITING_GOALS

    def run(self, ctx: WizardContext) -> Outcome:
        """Plan from a fresh read, show it, ask for the word, write, report."""
        state = ctx.box.state
        root = ctx.services.workspace.root
        if not state.proposal_file:
            info(ctx.prompter, t("apply-nothing"))
            return Outcome.DONE
        try:
            packages = chosen_packages(read_packages(root / str(state.proposal_file)))
        except (ValueError, OSError, UnsafeWrite):
            info(ctx.prompter, t("apply-proposal-unreadable"))
            return Outcome.DONE
        writes = ctx.services.writes
        advice = _advice(packages)
        if writes is None or not packages:
            info(ctx.prompter, t("apply-nothing"))
        else:
            self._write(ctx, writes, packages)
        if advice:
            task(ctx.prompter, t("apply-advice-intro"), *advice)
        return Outcome.DONE

    @staticmethod
    def _write(ctx: WizardContext, writes: WritePort, packages: Sequence[PackageModel]) -> None:
        notice: list[Text] = []
        for _ in range(REPLANS):
            try:
                plan = writes.plan_packages(packages)
            except UnsafeWrite as error:
                info(ctx.prompter, *notice, _refusal_text(error))
                return
            except ConsoleUnreachable:
                info(ctx.prompter, t("apply-console-lost"))
                return
            if not plan.writes:
                info(ctx.prompter, *notice, t("apply-nothing"))
                return
            word = ctx.services.words(APPLY_WORD)
            answer = ask_word(
                ctx, APPLY_WORD, *notice, t("apply-what", count=len(plan.writes)),
                t("apply-undo-first"), t("apply-type", word=word), table=write_table(plan),
            )  # fmt: skip
            if answer is None:
                info(ctx.prompter, t("apply-cancelled"))
                return
            try:
                outcome = _send(ctx, writes, plan, answer)
            except _ShownPlanChanged:
                notice = [t("apply-console-changed-now")]
                continue
            if outcome is not None:
                _report(ctx, outcome, plan)
            return
        info(ctx.prompter, t("apply-console-keeps-changing"))


def _report(ctx: WizardContext, outcome: WriteOutcome, plan: WritePlan) -> None:
    """Say how the writing went; offer the way back when it stopped half-way."""
    ctx.box.update(applied_state=APPLIED if outcome.sent else NOTHING)
    if outcome.complete:
        info(ctx.prompter, t("apply-done", count=outcome.sent), t("apply-scene-not-saved"))
        return
    question = t("apply-offer-undo") if outcome.sent else t("apply-offer-check")
    if confirm(ctx.prompter, *_failure_texts(outcome, plan), question, default=True):
        undo_applied(ctx, outcome.undo_path)
