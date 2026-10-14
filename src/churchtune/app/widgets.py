"""Widgets of the wizard screen (skill wizard-ux): step header, prompt, live level meters."""

from collections.abc import Mapping

from rich.table import Table as RichTable
from rich.text import Text as RichText
from textual.widgets import Static

from churchtune.i18n import Translator
from churchtune.wizard.protocol import Prompt, StepHeader, Text, render_text, split_question

METER_FLOOR_DBFS = -60.0
METER_WIDTH = 30
TARGET_LOW_DBFS, TARGET_HIGH_DBFS = -20.0, -12.0


def render(translate: Translator, text: Text) -> str:
    """One message in the interface language."""
    return render_text(text, translate)


class StepHeaderView(Static):
    """The line "Step 7 of 13 · Microphones · Venue"."""

    def show(self, translate: Translator, header: StepHeader) -> None:
        """Show a step header."""
        step = translate("common-step", {"number": header.number, "total": header.total})
        title = translate(header.title.key)
        venue = f" · {header.venue}" if header.venue else ""
        self.update(RichText(f"{step} · {title}{venue}", style="bold"))


class PromptView(Static):
    """The paragraphs, table and picture of a prompt (options and input are separate widgets)."""

    def show(self, translate: Translator, prompt: Prompt) -> None:
        """Show a prompt's text."""
        parts: list[RichText | RichTable] = []
        before, question = split_question(prompt)
        for index, paragraph in enumerate(before):
            style = "italic" if index == 0 and len(prompt.body) > 1 else ""
            parts.append(RichText(render(translate, paragraph) + "\n", style=style))
        if prompt.table is not None:
            table = RichTable(*(render(translate, h) for h in prompt.table.headers), expand=False)
            for row in prompt.table.rows:
                table.add_row(*(render(translate, cell) for cell in row))
            parts.append(table)
        if prompt.picture:
            parts.append(RichText(prompt.picture))
        parts.extend(RichText("\n" + render(translate, q), style="bold") for q in question)
        self.update(_stack(parts))


def _stack(parts: list[RichText | RichTable]) -> RichTable:
    grid = RichTable.grid()
    for part in parts:
        grid.add_row(part)
    return grid


def meter_bar(level_dbfs: float, value: str, width: int = METER_WIDTH) -> str:
    """A text bar for one level and its formatted ``value``.

    The target zone is marked with a sign, so colour is never needed.
    """
    filled = round(
        width * (min(max(level_dbfs, METER_FLOOR_DBFS), 0.0) - METER_FLOOR_DBFS) / -METER_FLOOR_DBFS
    )
    mark = "✓" if TARGET_LOW_DBFS <= level_dbfs <= TARGET_HIGH_DBFS else " "
    return f"{'█' * filled}{'·' * (width - filled)} {value:>11} {mark}"


class LevelMeter(Static):
    """Live levels of the measurement microphones (≤ 10 updates per second)."""

    def show(self, translate: Translator, levels_dbfs: Mapping[str, float]) -> None:
        """Show one bar per microphone; keys are microphone numbers."""
        lines = [
            f"{translate('common-mic-number', {'number': number})}: "
            + meter_bar(level, translate("common-dbfs", {"level": round(level, 1)}))
            for number, level in levels_dbfs.items()
        ]
        self.update("\n".join(lines))
