"""The Textual wizard app (SPEC §6.2, §11, skill wizard-ux).

The wizard engine runs in a worker thread and talks to the screen through ``TuiPrompter``: each
prompt is shown, the engine waits for the answer. Ctrl+C and Space silence the sound at once on
every screen (they are keys in Textual, not SIGINT); the engine then saves and leaves.
"""

import queue
import threading
from collections.abc import Callable, Mapping
from typing import ClassVar

from rich.text import Text as RichText
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.widgets import Input, OptionList, ProgressBar, Static
from textual.widgets.option_list import Option as ListOption

from churchtune.app.widgets import LevelMeter, PromptView, StepHeaderView, render
from churchtune.i18n import Translator
from churchtune.wizard.engine import RunResult
from churchtune.wizard.protocol import Answer, Kind, Nav, Prompt, StepHeader, Text

WizardRun = Callable[["TuiPrompter"], RunResult]
INPUT_KINDS = (Kind.INPUT, Kind.TYPED)


def key_hints(translate: Translator, prompt: Prompt) -> str:
    """The keys of this screen; colour-free and translated (no English footer)."""
    kind = "choose" if prompt.options else prompt.kind.value
    hints = [translate(f"tui-keys-{kind}")]
    if prompt.can_back:
        hints.append(translate("tui-keys-back"))
    hints.append(translate("tui-keys-quit"))
    return " · ".join(hints)


class TuiPrompter:
    """The ``Prompter`` of the Textual app; called from the engine's worker thread."""

    def __init__(self, app: "WizardApp") -> None:
        """Bind to the app."""
        self.app = app
        self.answers: queue.Queue[Answer] = queue.Queue()
        self.closed = threading.Event()
        self.quit_requested = threading.Event()

    def close(self) -> None:
        """The screen is gone: the waiting question is answered "save and quit"."""
        self.closed.set()
        self.answers.put(Answer(nav=Nav.QUIT))

    def call_ui(self, call: Callable[..., None], *args: object) -> bool:
        """Run ``call`` on the UI thread; False once the screen is gone."""
        if self.closed.is_set():
            return False
        try:
            self.app.call_from_thread(call, *args)
        except RuntimeError:  # the app stopped between the check and the call
            return False
        return True

    def header(self, header: StepHeader) -> None:
        """Show the step header."""
        self.call_ui(self.app.show_header, header)

    def ask(self, prompt: Prompt) -> Answer:
        """Show the prompt and wait for the answer.

        "Save and quit" pressed while no question was open answers the next question; an answer
        that arrives as the screen goes away counts as "save and quit" too.
        """
        if self.quit_requested.is_set() or not self.call_ui(self.app.show_prompt, prompt):
            return Answer(nav=Nav.QUIT)
        answer = self.answers.get()
        return Answer(nav=Nav.QUIT) if self.closed.is_set() else answer

    def progress(self, label: Text, done: int, total: int) -> None:
        """Show progress of a long operation."""
        self.call_ui(self.app.show_progress, label, done, total)

    def meter(self, levels_dbfs: Mapping[str, float]) -> None:
        """Show live levels."""
        self.call_ui(self.app.show_meter, dict(levels_dbfs))


class WizardApp(App[RunResult | None]):
    """The full-screen wizard."""

    TITLE = "ChurchTune"
    CSS = """
    StepHeaderView { padding: 0 1; background: $boost; }
    #status { padding: 0 1; text-style: bold; }
    #keys { padding: 0 1; background: $boost; }
    #body { height: 1fr; padding: 0 1; }
    OptionList { height: auto; max-height: 12; }
    LevelMeter { padding: 0 1; height: auto; }
    ProgressBar { padding: 0 1; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "silence", show=False, priority=True),
        Binding("space", "silence_if_not_typing", show=False, priority=True),
        Binding("escape", "go_back", show=False),
        Binding("ctrl+q", "save_quit", show=False, priority=True),
    ]

    def __init__(
        self,
        translate: Translator,
        run: WizardRun,
        stop: threading.Event,
        shutdown: threading.Event | None = None,
    ) -> None:
        """Prepare the app; ``run`` starts the engine with a prompter.

        ``stop`` is the sound stop flag; ``shutdown`` is set when the app goes away, before
        ``stop``, so the engine never clears the stop flag after that.
        """
        super().__init__()
        self.translate = translate
        self.run_wizard = run
        self.stop = stop
        self.shutdown = shutdown or threading.Event()
        self.prompter = TuiPrompter(self)
        self.current: Prompt | None = None
        self.engine_done = threading.Event()

    def compose(self) -> ComposeResult:
        """Header, body (prompt, options or input, meters, progress), translated key hints."""
        yield StepHeaderView(id="header")
        with VerticalScroll(id="body"):
            yield PromptView(id="prompt")
            yield OptionList(id="options")
            yield Input(id="input")
            yield LevelMeter(id="meter")
            yield ProgressBar(id="progress", show_eta=False)
        yield Static(id="status")
        yield Static(id="keys")

    def on_mount(self) -> None:
        """Hide what the first prompt does not need and start the engine."""
        for widget_id in ("options", "input", "meter", "progress"):
            self.query_one(f"#{widget_id}").display = False
        self.start_engine()

    @work(thread=True, exclusive=True)
    def start_engine(self) -> None:
        """Run the wizard in a worker thread; leave the app with its result."""
        try:
            result = self.run_wizard(self.prompter)
            self.prompter.call_ui(self.exit, result)
        finally:
            self.engine_done.set()

    def on_unmount(self) -> None:
        """Leaving the app silences the sound and lets the engine save and stop."""
        self.shutdown.set()
        self.stop.set()
        self.prompter.close()

    # ------------------------------------------------------------------ shown by the engine
    def show_header(self, header: StepHeader) -> None:
        """Show the step header; meters and progress belong to one step."""
        self.query_one(StepHeaderView).show(self.translate, header)
        self.query_one("#meter").display = False
        self.query_one("#progress").display = False
        self.query_one("#status", Static).update("")

    def show_prompt(self, prompt: Prompt) -> None:
        """Show a prompt and focus what answers it."""
        self.current = prompt
        self.query_one("#status", Static).update("")
        self.query_one(PromptView).show(self.translate, prompt)
        options = self.query_one(OptionList)
        field = self.query_one(Input)
        options.display = bool(prompt.options)
        field.display = prompt.kind in INPUT_KINDS
        if prompt.options:
            options.clear_options()
            for option in prompt.options:
                label = render(self.translate, option.label)
                if option.recommended:
                    label = f"{label} {self.translate('common-recommended')}"
                options.add_option(ListOption(label, id=option.id))
            default = next((i for i, o in enumerate(prompt.options) if o.id == prompt.default), 0)
            options.highlighted = default
            options.focus()
        elif field.display:
            field.value = prompt.default or ""
            field.focus()
        else:
            self.query_one("#body").focus()
        self.query_one("#keys", Static).update(RichText(key_hints(self.translate, prompt)))

    def show_progress(self, label: Text, done: int, total: int) -> None:
        """Show progress."""
        bar = self.query_one(ProgressBar)
        bar.display = 0 < done < total
        bar.update(total=total, progress=done)
        self.query_one("#status", Static).update(RichText(render(self.translate, label)))

    def show_meter(self, levels_dbfs: dict[str, float]) -> None:
        """Show live levels."""
        meter = self.query_one(LevelMeter)
        meter.display = True
        meter.show(self.translate, levels_dbfs)

    # ------------------------------------------------------------------ answers
    def _answer(self, answer: Answer) -> None:
        if self.current is None:
            return
        self.current = None
        self.query_one(OptionList).display = False
        self.query_one(Input).display = False
        self.prompter.answers.put(answer)

    @on(OptionList.OptionSelected)
    def _chosen(self, event: OptionList.OptionSelected) -> None:
        self._answer(Answer(event.option.id or ""))

    @on(Input.Submitted)
    def _typed(self, event: Input.Submitted) -> None:
        self._answer(Answer(event.value))

    def key_enter(self) -> None:
        """Enter on a screen without options or input: continue."""
        if (
            self.current is not None
            and not self.current.options
            and self.current.kind not in INPUT_KINDS
        ):
            self._answer(Answer(""))

    def action_go_back(self) -> None:
        """Back to the previous step (when the screen allows it)."""
        if self.current is not None and self.current.can_back:
            self._answer(Answer(nav=Nav.BACK))

    def action_save_quit(self) -> None:
        """Save and quit: the engine saves after the answer."""
        if self.current is not None:
            self._answer(Answer(nav=Nav.QUIT))
        else:  # busy (reading, measuring): stop the sound, quit at the next question
            self.prompter.quit_requested.set()
            self.stop.set()
            self.query_one("#status", Static).update(RichText(self.translate("tui-quit-pending")))

    def action_silence(self) -> None:
        """Silence now (the measurement steps watch this flag)."""
        self.stop.set()
        self.notify(self.translate("sound-stopped"), severity="warning")

    def action_silence_if_not_typing(self) -> None:
        """Space silences, except while typing into a field."""
        if isinstance(self.focused, Input):
            self.focused.insert_text_at_cursor(" ")
            return
        self.action_silence()
