"""Step S12 (SPEC §7, §9.3): write the chosen packages, and the way back.

The step only shows and asks. The write port reads the console fresh, refuses a package whose
"before" no longer matches (the person re-measures), orders the writes so nothing is louder in
between, writes the undo file before the first write and reads every write back. The person
confirms by typing a word. Output delay and polarity are advice only in the wizard: the person
sets them by hand (their writes were never verified on hardware, ADR 0005).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from churchtune.errors import ConsoleUnreachable, UnsafeWrite
from churchtune.storage.files import read_packages
from churchtune.storage.models import PackageModel, WizardStateFile
from churchtune.wizard.applying import chosen_packages, ref_of
from churchtune.wizard.labels import output_text
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
from churchtune.wizard.ui import confirm, info, kebab, t, typed_word

APPLY_WORD, UNDO_WORD = "apply-word", "undo-word"
WRITING_GOALS = frozenset({"full", "sub"})


def write_text(write: PlannedWrite) -> Text:
    """Which parameter a write changes, in words: "Main LR, band 3: gain"."""
    where = path_text(write.strip)
    if write.band is None:
        return t(f"write-{kebab(write.param)}", path=where)
    return t(f"write-band-{write.param}", path=where, band=write.band)


def write_table(plan: WritePlan) -> Table:
    """Parameter · now · after (on the console's grid)."""
    rows = tuple(
        (write_text(w), t("value-console", value=w.before), t("value-console", value=w.after))
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


def _outcome_texts(outcome: WriteOutcome) -> list[Text]:
    if outcome.complete:
        return [t("apply-done", count=outcome.sent), t("apply-scene-not-saved")]
    if outcome.stopped:
        return [t("apply-stopped", count=outcome.sent)]
    if outcome.failed_path is None:  # a later run was refused by the safety check
        return [t("apply-run-refused", count=outcome.sent)]
    return [t("apply-readback-failed", path=outcome.failed_path, count=outcome.sent)]


def undo_applied(ctx: WizardContext, undo_path: Path) -> bool:
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
        return True
    word = ctx.services.words(UNDO_WORD)
    typed = typed_word(
        ctx.prompter, t("undo-what", count=len(plan.writes)), *notes, t("undo-type", word=word),
        table=write_table(plan),
    )  # fmt: skip
    revert = ctx.services.workspace.new_undo_path(None, ctx.services.now(), revert=True)
    ctx.services.reset_stop()
    try:
        outcome = writes.apply(plan, typed, word, revert, ctx.services.stop.is_set,
                               reverts=undo_path)  # fmt: skip
    except UnsafeWrite as error:
        changed = "mismatches" in error.context
        info(ctx.prompter, t("undo-console-changed") if changed else t("undo-cancelled"))
        return False
    except ConsoleUnreachable:
        info(ctx.prompter, t("apply-console-lost"))
        return False
    info(ctx.prompter, t("undo-done") if outcome.complete else t("undo-incomplete"))
    return outcome.complete


def _send(
    ctx: WizardContext, writes: WritePort, plan: WritePlan, typed: str, word: str
) -> WriteOutcome | None:
    """Send a confirmed plan; None (with the reason shown) when nothing could be judged.

    Whatever happens, once the undo file exists the way back is remembered in the state.
    """
    root = ctx.services.workspace.root
    undo_path = ctx.services.workspace.new_undo_path(None, ctx.services.now())
    ctx.services.reset_stop()
    try:
        return writes.apply(plan, typed, word, undo_path, ctx.services.stop.is_set)
    except UnsafeWrite as error:
        changed = "mismatches" in error.context
        info(ctx.prompter, t("apply-console-changed") if changed else t("apply-cancelled"))
    except ConsoleUnreachable:
        info(ctx.prompter, t("apply-console-lost"))
    finally:
        if undo_path.exists():
            ctx.box.update(applied_undo=str(undo_path.relative_to(root)))
    return None


@dataclass(frozen=True, slots=True)
class Apply:
    """S12: the chosen packages to the console, confirmed by a typed word."""

    id: str = "apply"
    title: str = "step-apply-title"
    changes_console: bool = True

    def applies(self, state: WizardStateFile) -> bool:
        """Goals that change the system (counted from the start, so the total stays put)."""
        return measuring(state) and state.goal in WRITING_GOALS

    def run(self, ctx: WizardContext) -> Outcome:  # noqa: PLR0911 - one exit per situation
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
        advice = _advice(packages)
        if advice:
            info(ctx.prompter, t("apply-advice-intro"), *advice)
        writes = ctx.services.writes
        if writes is None or not packages:
            info(ctx.prompter, t("apply-nothing"))
            return Outcome.DONE
        try:
            plan = writes.plan_packages(packages)
        except UnsafeWrite as error:
            changed = "mismatches" in error.context
            info(ctx.prompter, t("apply-console-changed") if changed else t("apply-refused"))
            return Outcome.DONE
        except ConsoleUnreachable:
            info(ctx.prompter, t("apply-console-lost"))
            return Outcome.DONE
        if not plan.writes:
            info(ctx.prompter, t("apply-nothing"))
            return Outcome.DONE
        word = ctx.services.words(APPLY_WORD)
        typed = typed_word(
            ctx.prompter, t("apply-what", count=len(plan.writes)), t("apply-undo-first"),
            t("apply-type", word=word), table=write_table(plan),
        )  # fmt: skip
        outcome = _send(ctx, writes, plan, typed, word)
        if outcome is None:
            return Outcome.DONE
        texts = _outcome_texts(outcome)
        if outcome.complete:
            info(ctx.prompter, *texts)
            return Outcome.DONE
        if confirm(ctx.prompter, *texts, t("apply-offer-undo"), default=True):
            undo_applied(ctx, outcome.undo_path)
        return Outcome.DONE
