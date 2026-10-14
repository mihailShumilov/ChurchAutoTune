"""The Textual wizard app: answers reach the engine, the sound keys work on every screen."""

import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

import pytest
from textual.pilot import Pilot
from textual.widgets import OptionList

from churchtune.app.app import TuiPrompter, WizardApp, key_hints
from churchtune.app.widgets import meter_bar
from churchtune.i18n import Translator
from churchtune.wizard.engine import RunResult
from churchtune.wizard.protocol import (
    Answer,
    Kind,
    Nav,
    Option,
    Prompt,
    StepHeader,
    Table,
    Text,
)
from churchtune.wizard.run import new_state

WAIT_STEPS = 200


def t(key: str, **args: str | int | float) -> Text:
    return Text(key, args)


VENUE = Prompt(
    Kind.CHOOSE,
    (t("step-welcome-what"), t("step-welcome-venue")),
    (
        Option("hall", t("step-welcome-venue-known", venue="Main hall"), recommended=True),
        Option("new", t("step-welcome-venue-new")),
    ),
    default="hall",
)
NAME = Prompt(Kind.INPUT, (t("step-welcome-venue-name"),), default="")
READY = Prompt(Kind.INFO, (t("step-read-done", count=1234),))
MAPPED = Prompt(
    Kind.CONFIRM,
    (t("step-topology-what"), t("step-topology-correct")),
    (Option("yes", t("common-yes"), recommended=True), Option("no", t("common-no"))),
    default="yes",
    table=Table(
        (t("topology-h-output"), t("topology-h-role")),
        (
            (t("output-main", number=8), t("role-top-l")),
            (t("output-main", number=6), t("role-sub")),
        ),
    ),
)

WARNING = Prompt(
    Kind.CHOOSE,
    (t("step-points-sweeps"), t("sound-warning"), t("sound-stop-keys")),
    (Option("play", t("sound-play"), recommended=True), Option("later", t("sound-later"))),
    default="play",
)
PLACE = Prompt(
    Kind.TASK,
    (
        t("step-points-put", count=2),
        Text(
            "step-points-place",
            {
                "point": 3,
                "total": 8,
                "place": t("point-front-right"),
                "mic": t("common-mic", number=1, usb=10),
            },
        ),
        Text(
            "step-points-place",
            {
                "point": 4,
                "total": 8,
                "place": t("point-middle-left"),
                "mic": t("common-mic", number=2, usb=11),
            },
        ),
        t("step-points-height"),
    ),
)


class Script:
    """A stand-in engine: shows a header and prompts, records the answers."""

    def __init__(self, prompts: Sequence[Prompt], levels: dict[str, float] | None = None) -> None:
        self.prompts = prompts
        self.levels = levels
        self.answers: list[Answer] = []

    def __call__(self, prompter: TuiPrompter) -> RunResult:
        prompter.header(StepHeader(1, 13, t("step-welcome-title"), "Main hall"))
        if self.levels:
            prompter.meter(self.levels)
        for prompt in self.prompts:
            answer = prompter.ask(prompt)
            self.answers.append(answer)
            if answer.nav is not None:
                return RunResult(False, new_state("en", datetime(2026, 10, 14, tzinfo=UTC)))
        return RunResult(True, new_state("en", datetime(2026, 10, 14, tzinfo=UTC)))


def make_app(script: Script, lang: str = "en") -> tuple[WizardApp, threading.Event]:
    stop = threading.Event()
    return WizardApp(Translator(lang), script, stop), stop


async def until(pilot: Pilot[object], condition: Callable[[], bool]) -> None:
    for _ in range(WAIT_STEPS):
        if condition():
            return
        await pilot.pause(0.01)
    raise AssertionError("the screen did not change")


async def shown(pilot: Pilot[object], app: WizardApp, prompt: Prompt) -> None:
    await until(pilot, lambda: app.current is prompt)
    await pilot.pause()


@pytest.mark.asyncio
async def test_answers_flow_from_the_screen_to_the_engine() -> None:
    script = Script([VENUE, NAME, READY])
    app, stop = make_app(script)
    async with app.run_test(size=(100, 30)) as pilot:
        await shown(pilot, app, VENUE)
        await pilot.press("down", "enter")
        await shown(pilot, app, NAME)
        await pilot.press(*"Big", "space", *"hall", "enter")
        await shown(pilot, app, READY)
        assert not stop.is_set()  # Space typed into the field did not count as "stop the sound"
        await pilot.press("enter")
        await until(pilot, lambda: len(script.answers) == 3)
    assert [a.value for a in script.answers] == ["new", "Big hall", ""]
    assert stop.is_set()  # leaving the app always silences
    assert app.return_value is not None
    assert app.return_value.finished


@pytest.mark.asyncio
async def test_enter_takes_the_recommended_option() -> None:
    script = Script([VENUE])
    app, _ = make_app(script)
    async with app.run_test() as pilot:
        await shown(pilot, app, VENUE)
        await pilot.press("enter")
        await until(pilot, lambda: len(script.answers) == 1)
    assert script.answers[0].value == "hall"


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["ctrl+c", "space"])
async def test_sound_keys_silence_on_a_screen_without_a_field(key: str) -> None:
    script = Script([READY])
    app, stop = make_app(script)
    async with app.run_test() as pilot:
        await shown(pilot, app, READY)
        await pilot.press(key)
        assert stop.is_set()
        assert app.current is READY  # silencing is not an answer
        await pilot.press("enter")
        await until(pilot, lambda: len(script.answers) == 1)


@pytest.mark.asyncio
async def test_ctrl_c_silences_while_typing() -> None:
    script = Script([NAME])
    app, stop = make_app(script)
    async with app.run_test() as pilot:
        await shown(pilot, app, NAME)
        await pilot.press("ctrl+c")
        assert stop.is_set()
        await pilot.press("enter")
        await until(pilot, lambda: len(script.answers) == 1)


@pytest.mark.asyncio
async def test_escape_goes_back_only_where_allowed() -> None:
    no_back = Prompt(Kind.INFO, (t("step-read-done", count=1),), can_back=False)
    script = Script([no_back, READY])
    app, _ = make_app(script)
    async with app.run_test() as pilot:
        await shown(pilot, app, no_back)
        await pilot.press("escape")
        assert app.current is no_back
        await pilot.press("enter")
        await shown(pilot, app, READY)
        await pilot.press("escape")
        await until(pilot, lambda: len(script.answers) == 2)
    assert script.answers[1].nav is Nav.BACK


@pytest.mark.asyncio
async def test_ctrl_q_saves_and_quits() -> None:
    script = Script([VENUE, READY])
    app, _ = make_app(script)
    async with app.run_test() as pilot:
        await shown(pilot, app, VENUE)
        await pilot.press("ctrl+q")
        await until(pilot, lambda: len(script.answers) == 1)
    assert script.answers[0].nav is Nav.QUIT
    assert app.return_value is not None
    assert not app.return_value.finished


@pytest.mark.asyncio
async def test_closing_the_app_mid_question_unblocks_the_engine() -> None:
    script = Script([READY, VENUE])
    app, stop = make_app(script)
    async with app.run_test() as pilot:
        await shown(pilot, app, READY)
        app.exit()
    assert stop.is_set()
    for _ in range(WAIT_STEPS):
        if script.answers:
            break
        threading.Event().wait(0.01)
    assert script.answers == [Answer(nav=Nav.QUIT)]


@pytest.mark.asyncio
async def test_progress_and_meter_are_shown() -> None:
    script = Script([READY], levels={"10": -16.0, "11": -40.0})
    app, _ = make_app(script)
    async with app.run_test() as pilot:
        await shown(pilot, app, READY)
        assert app.query_one("#meter").display
        app.show_progress(t("step-read-progress"), 3, 10)
        await pilot.pause()
        assert app.query_one("#progress").display
        app.show_progress(t("step-read-progress"), 10, 10)
        await pilot.pause()
        assert not app.query_one("#progress").display
        await pilot.press("enter")
        await until(pilot, lambda: len(script.answers) == 1)


def test_key_hints_follow_the_screen() -> None:
    translate = Translator("en")
    assert key_hints(translate, VENUE) == (
        "[↑ ↓] choose · [Enter] confirm · [Esc] back · [Ctrl+Q] save and quit"
    )
    no_back = Prompt(Kind.TASK, (t("step-mics-plug"),), can_back=False)
    assert key_hints(translate, no_back) == "Do it, then [Enter] · [Ctrl+Q] save and quit"


def test_meter_bar_marks_the_target_zone_without_colour() -> None:
    assert meter_bar(-16.0, "-16 dBFS").endswith("-16 dBFS ✓")
    assert not meter_bar(-40.0, "-40 dBFS").endswith("✓")
    assert meter_bar(-90.0, "").startswith("·")
    assert meter_bar(3.0, "").startswith("█" * 30)


# ---------------------------------------------------------------------------- snapshots
SIZES = [(80, 24), (120, 40)]


@pytest.mark.parametrize("lang", ["en", "ru"])
@pytest.mark.parametrize("size", SIZES, ids=["80x24", "120x40"])
@pytest.mark.parametrize(
    ("name", "prompts", "levels"),
    [
        ("choose", [VENUE], None),
        ("table", [MAPPED], None),
        ("meter", [READY], {"1": -16.0, "2": -38.5}),
        ("warning", [WARNING], None),
        ("place", [PLACE], None),
    ],
    ids=["choose", "table", "meter", "warning", "place"],
)
def test_screen_snapshots(  # type: ignore[no-untyped-def]
    snap_compare, lang, size, name, prompts, levels
) -> None:
    script = Script(prompts, levels)
    app, _ = make_app(script, lang)

    async def run_before(pilot: Pilot[object]) -> None:
        await shown(pilot, app, prompts[0])

    assert snap_compare(app, terminal_size=size, run_before=run_before)


@pytest.mark.asyncio
async def test_ctrl_q_while_busy_quits_at_the_next_question() -> None:
    busy = threading.Event()
    answers: list[Answer] = []

    def engine(prompter: TuiPrompter) -> RunResult:
        prompter.progress(t("step-read-progress"), 1, 10)
        busy.wait(5)
        answers.append(prompter.ask(READY))
        return RunResult(False, new_state("en", datetime(2026, 10, 14, tzinfo=UTC)))

    stop = threading.Event()
    app = WizardApp(Translator("en"), engine, stop)
    async with app.run_test() as pilot:
        await until(pilot, lambda: app.query_one("#progress").display)
        await pilot.press("ctrl+q")
        assert stop.is_set()
        busy.set()
        await until(pilot, lambda: bool(answers))
    assert answers == [Answer(nav=Nav.QUIT)]


def test_an_answer_that_arrives_as_the_screen_closes_counts_as_quit() -> None:
    app = WizardApp(Translator("en"), Script([]), threading.Event())
    prompter = app.prompter
    prompter.call_ui = lambda *_args: True  # type: ignore[method-assign]
    prompter.answers.put(Answer("yes"))
    prompter.closed.set()
    assert prompter.ask(READY) == Answer(nav=Nav.QUIT)


@pytest.mark.asyncio
async def test_the_recommended_option_is_named_in_words() -> None:
    script = Script([VENUE])
    app, _ = make_app(script, "ru")
    async with app.run_test() as pilot:
        await shown(pilot, app, VENUE)
        options = app.query_one(OptionList)
        assert "(рекомендуется)" in str(options.get_option_at_index(0).prompt)
        await pilot.press("enter")
        await until(pilot, lambda: len(script.answers) == 1)
