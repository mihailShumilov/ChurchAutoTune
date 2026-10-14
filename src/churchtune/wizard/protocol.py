"""The conversation between the wizard engine and a user interface (SPEC §6.2, ADR 0008).

The engine never prints and never reads a key: it sends plain, serialisable messages to a
``Prompter`` and gets answers back. Texts are message keys with arguments, rendered by the UI in
its language. The Textual app implements a ``Prompter``; tests use a scripted one; a future
graphical or web UI can speak the same messages as JSON (``to_dict``).
"""

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Protocol

# A placeholder value: a number or a name, or a translated text of its own (a console word such
# as "matrix 3"), or a list of those joined with commas.
type ArgValue = str | int | float | Text | tuple[str | Text, ...]
LIST_SEPARATOR = ", "


@dataclass(frozen=True, slots=True)
class Text:
    """A message key and its arguments, rendered by the UI.

    Attributes:
        key: Fluent message key, such as ``step-console-found``.
        args: Values for the placeholders, named with units (``freq_hz``).
    """

    key: str
    args: Mapping[str, ArgValue] = field(default_factory=dict)


class Kind(StrEnum):
    """What the UI must do with a prompt."""

    # Show and continue on Enter.
    INFO = "info"
    # A detected value or a question to confirm: Enter accepts the default.
    CONFIRM = "confirm"
    # Pick one option.
    CHOOSE = "choose"
    # Type a value (a number, an address, a name).
    INPUT = "input"
    # Do something physical, then Enter ("put mic 2 at point 5").
    TASK = "task"
    # A typed confirmation word before a console change or a sound.
    TYPED = "typed"


class Nav(StrEnum):
    """Navigation besides an answer."""

    BACK = "back"
    QUIT = "quit"


@dataclass(frozen=True, slots=True)
class Option:
    """One choice.

    Attributes:
        id: Stable id returned in the answer.
        label: What the UI shows.
        recommended: Shown first and marked "(recommended)".
    """

    id: str
    label: Text
    recommended: bool = False


@dataclass(frozen=True, slots=True)
class Table:
    """A small table: headers and rows of already formatted cells (numbers stay plain).

    Attributes:
        headers: Column titles.
        rows: Cells as texts.
    """

    headers: tuple[Text, ...]
    rows: tuple[tuple[Text, ...], ...]


@dataclass(frozen=True, slots=True)
class Prompt:
    """One screen of the wizard.

    Attributes:
        kind: What the UI must do.
        body: Paragraphs, the first one is "what happens now".
        options: Choices for ``CHOOSE``.
        default: Default answer (option id or text).
        table: Optional table (diffs, topology, results).
        picture: Optional pre-drawn ASCII picture (hall map), monospace.
        can_back: The UI offers "Back".
    """

    kind: Kind
    body: tuple[Text, ...]
    options: tuple[Option, ...] = ()
    default: str | None = None
    table: Table | None = None
    picture: str | None = None
    can_back: bool = True


@dataclass(frozen=True, slots=True)
class Answer:
    """The UI's answer.

    Attributes:
        value: Option id, typed text, or ``""`` for plain Enter.
        nav: Navigation instead of an answer.
    """

    value: str = ""
    nav: Nav | None = None


@dataclass(frozen=True, slots=True)
class StepHeader:
    """The line above every step: "Step 7 of 13 · Microphones".

    Attributes:
        number: 1-based number among the steps that apply.
        total: Steps that apply to this goal and configuration.
        title: Name of the step.
        venue: Venue name, if known.
    """

    number: int
    total: int
    title: Text
    venue: str = ""


class Prompter(Protocol):
    """What the engine needs from a user interface."""

    def header(self, header: StepHeader) -> None:
        """Show the step header."""
        ...

    def ask(self, prompt: Prompt) -> Answer:
        """Show a prompt and wait for the answer."""
        ...

    def progress(self, label: Text, done: int, total: int) -> None:
        """Show progress of a long operation."""
        ...

    def meter(self, levels_dbfs: Mapping[str, float]) -> None:
        """Show live levels (≤ 10 updates per second)."""
        ...


def to_dict(message: Prompt | Answer | StepHeader | Text) -> dict[str, Any]:
    """A message as plain JSON-ready data."""
    return asdict(message)


Translate = Callable[[str, Mapping[str, str | int | float]], str]


def render_text(text: Text, translate: Translate) -> str:
    """Render a text with ``translate``; nested texts are rendered first, lists joined."""

    def value(arg: ArgValue) -> str | int | float:
        if isinstance(arg, Text):
            return render_text(arg, translate)
        if isinstance(arg, tuple):
            return LIST_SEPARATOR.join(str(value(item)) for item in arg)
        return arg

    return translate(text.key, {name: value(arg) for name, arg in text.args.items()})
