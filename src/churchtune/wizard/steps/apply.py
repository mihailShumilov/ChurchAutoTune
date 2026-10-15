"""Step S12 (SPEC §7, §9.3): write the chosen packages, and the way back.

The step only shows and asks. The write port reads the console fresh, refuses a package whose
"before" no longer matches (the person re-measures), orders the writes so nothing is louder in
between, writes the undo file before the first write and reads every write back. The person
confirms by typing a word; a typo asks again, an empty line cancels. Output delay and polarity
are advice only in the wizard: the person sets them by hand after the writes (their writes were
never verified on hardware, ADR 0005).
"""

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from churchtune.domain.snapshot import ConsoleSnapshot, SignalRef, SourceKind
from churchtune.errors import ChurchTuneError, ConsoleUnreachable, UnsafeWrite
from churchtune.storage.files import read_packages, read_snapshot, read_wizard_state
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
from churchtune.wizard.steps.setup import ensure_driver, measuring
from churchtune.wizard.ui import confirm, info, kebab, t, task, typed_word

logger = logging.getLogger(__name__)
APPLY_WORD, UNDO_WORD, MUTE_WORD = "apply-word", "undo-word", "mute-word"
# The English words count in every language: typing them is just as deliberate.
ENGLISH_WORDS = {APPLY_WORD: "apply", UNDO_WORD: "undo", MUTE_WORD: "mute"}
FAR_FROM_FIRST_DB = 12.0  # SPEC §9.2 levels: further from the venue's first snapshot needs a "yes"
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


def advice_lines(packages: Sequence[PackageModel]) -> list[Text]:
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
        shown_as_values = package.delays or package.polarities or package.delay_ms
        if package.suggest_only and not shown_as_values:
            lines.append(t("apply-advice-package", package=t(f"package-{kebab(package.kind)}")))
        if package.delay_ms and package.delay_side and not package.delays:
            side = t(f"apply-advice-side-{package.delay_side}")
            lines.append(t("apply-advice-side-delay", side=side, ms=round(package.delay_ms, 1)))
    return lines


def ask_word(
    ctx: WizardContext, key: str, *body: Text, table: Table | None
) -> tuple[str, str] | None:
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
    except ConsoleUnreachable:
        info(ctx.prompter, t("apply-console-lost"))
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
        if not restore_pending_mutes(ctx):  # never write while a speaker may still be muted
            info(ctx.prompter, t("apply-mutes-pending"))
            return Outcome.DONE
        if not state.proposal_file:
            info(ctx.prompter, t("apply-nothing"))
            return Outcome.DONE
        try:
            packages = chosen_packages(read_packages(root / str(state.proposal_file)))
        except (ValueError, OSError, UnsafeWrite):
            info(ctx.prompter, t("apply-proposal-unreadable"))
            return Outcome.DONE
        writes = ctx.services.writes
        advice = advice_lines(packages)
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
            if not _far_levels_confirmed(ctx, plan):
                info(ctx.prompter, t("apply-cancelled"))
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


def first_snapshot(ctx: WizardContext) -> ConsoleSnapshot | None:
    """The venue's first readable snapshot (None when there is none)."""
    workspace = ctx.services.workspace
    for session in workspace.sessions_of(ctx.box.state.venue):
        try:
            path = read_wizard_state(session).snapshot_file
            if path is None:
                continue
            stored = read_snapshot(workspace.root / path)
            return ensure_driver(ctx).parse_snapshot(stored.identity(), stored.raw, stored.taken_at)
        except (ValueError, OSError, ChurchTuneError) as error:
            logger.warning("snapshot of %s not usable for the level check: %s", session, error)
    return None


def first_fader_db(snapshot: ConsoleSnapshot | None, strip: SignalRef) -> float | None:
    """The fader of a matrix in a snapshot (None without one)."""
    if snapshot is None or strip.kind is not SourceKind.MATRIX:
        return None
    found = snapshot.matrices.get(strip.number)
    return found.fader_db if found is not None else None


def _far_levels_confirmed(ctx: WizardContext, plan: WritePlan) -> bool:
    """A fader more than 12 dB away from the venue's first snapshot needs a separate "yes"."""
    faders = [w for w in plan.writes if w.param == "fader" and isinstance(w.after_value, float)]
    if not faders:
        return True
    snapshot = first_snapshot(ctx)
    far: list[Text] = []
    for write in faders:
        first = first_fader_db(snapshot, write.strip)
        after = float(write.after_value)
        if first is None or math.isinf(first) or math.isinf(after):
            continue
        if abs(after - first) > FAR_FROM_FIRST_DB:
            text = t(
                "apply-level-far",
                path=path_text(write.strip),
                first_db=round(first, 1),
                after_db=round(after, 1),
                delta_db=round(abs(after - first), 1),
                louder="yes" if after > first else "no",
            )
            far.append(text)
    if not far:
        return True
    return confirm(ctx.prompter, *far, t("apply-level-far-question"), default=False)


def restore_pending_mutes(ctx: WizardContext) -> bool:  # noqa: PLR0911 - one exit per case
    """Switch back on what an interrupted measurement left muted (fresh read, table, word).

    Returns:
        True when nothing is pending any more.
    """
    writes = ctx.services.writes
    if writes is None:
        return True
    try:
        if writes.clear_settled_mutes():
            return True
        plan = writes.pending_mutes()
    except ConsoleUnreachable:
        info(ctx.prompter, t("apply-console-lost"))
        return False
    if plan is None:
        return True
    word = ctx.services.words(UNDO_WORD)
    answer = ask_word(
        ctx,
        UNDO_WORD,
        t("mutes-pending-what"),
        t("mutes-pending-type", word=word),
        table=write_table(plan),
    )
    if answer is None:
        info(ctx.prompter, t("mutes-pending-left"))
        return False
    try:
        writes.restore_pending_mutes(plan, *answer)
    except UnsafeWrite:
        info(ctx.prompter, t("mutes-pending-changed"))
        return False
    except ConsoleUnreachable:
        info(ctx.prompter, t("apply-console-lost"))
        return False
    info(ctx.prompter, t("mutes-pending-done"))
    return True


def _report(ctx: WizardContext, outcome: WriteOutcome, plan: WritePlan) -> None:
    """Say how the writing went; offer the way back when it stopped half-way."""
    ctx.box.update(applied_state=APPLIED if outcome.sent else NOTHING)
    if outcome.still_muted:
        names = tuple(path_text(SignalRef(SourceKind.MATRIX, n)) for n in outcome.still_muted)
        info(ctx.prompter, t("apply-still-muted", matrices=names))
    if outcome.complete:
        info(ctx.prompter, t("apply-done", count=outcome.sent), t("apply-scene-not-saved"))
        return
    question = t("apply-offer-undo") if outcome.sent else t("apply-offer-check")
    if confirm(ctx.prompter, *_failure_texts(outcome, plan), question, default=True):
        undo_applied(ctx, outcome.undo_path)
