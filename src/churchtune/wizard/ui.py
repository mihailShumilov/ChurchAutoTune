"""Small helpers that build prompts and turn navigation into ``Navigate`` (skill wizard-ux)."""

from collections.abc import Sequence

from churchtune.wizard.protocol import (
    Answer,
    ArgValue,
    Kind,
    Nav,
    Option,
    Prompt,
    Prompter,
    Table,
    Text,
)
from churchtune.wizard.state import Navigate, Outcome

YES, NO = "yes", "no"


def t(key: str, **args: ArgValue) -> Text:
    """A text by key with arguments."""
    return Text(key, args)


def kebab(value: str) -> str:
    """A code as a message-key part: ``"top_l"`` gives ``"top-l"``."""
    return value.replace("_", "-")


def _answer(prompter: Prompter, prompt: Prompt) -> Answer:
    answer = prompter.ask(prompt)
    if answer.nav is Nav.QUIT:
        raise Navigate(Outcome.QUIT)
    if answer.nav is Nav.BACK and prompt.can_back:
        raise Navigate(Outcome.BACK)
    return answer


def info(
    prompter: Prompter, *body: Text, table: Table | None = None, picture: str | None = None
) -> None:
    """Show information; Enter continues."""
    _answer(prompter, Prompt(Kind.INFO, body, table=table, picture=picture))


def confirm(
    prompter: Prompter, *body: Text, default: bool = True, table: Table | None = None
) -> bool:
    """A yes/no question; Enter takes the default."""
    options = (Option(YES, t("common-yes"), default), Option(NO, t("common-no"), not default))
    prompt = Prompt(Kind.CONFIRM, body, options, YES if default else NO, table)
    value = _answer(prompter, prompt).value or (YES if default else NO)
    return value == YES


def choose(
    prompter: Prompter,
    body: Sequence[Text],
    options: Sequence[Option],
    *,
    table: Table | None = None,
) -> str:
    """Pick one option; Enter takes the recommended one."""
    default = next((o.id for o in options if o.recommended), options[0].id)
    prompt = Prompt(Kind.CHOOSE, tuple(body), tuple(options), default, table)
    value = _answer(prompter, prompt).value or default
    return value if any(o.id == value for o in options) else default


def ask_text(prompter: Prompter, *body: Text, default: str = "") -> str:
    """Type a value; Enter takes the default."""
    return _answer(prompter, Prompt(Kind.INPUT, body, default=default)).value or default


def task(prompter: Prompter, *body: Text, picture: str | None = None) -> None:
    """A physical task; Enter when done."""
    _answer(prompter, Prompt(Kind.TASK, body, picture=picture))


def typed_confirmation(
    prompter: Prompter, *body: Text, word: str, table: Table | None = None
) -> bool:
    """Ask for a typed word before a sound or a console change; anything else means no."""
    answer = _answer(prompter, Prompt(Kind.TYPED, body, default="", table=table))
    return answer.value.strip().lower() == word.lower()


def typed_word(prompter: Prompter, *body: Text, table: Table | None = None) -> str:
    """The word the person typed before a console change (the write port checks it)."""
    return _answer(prompter, Prompt(Kind.TYPED, body, default="", table=table)).value
