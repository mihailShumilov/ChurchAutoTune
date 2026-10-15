"""A scripted volunteer: answers the wizard's prompts and does the physical tasks in the simulator.

Used by ``ctune selftest`` and the end-to-end tests; it only ever drives the simulated hall.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from churchtune.audio.fake.hall import CALIBRATION_POINT, FakeHall
from churchtune.errors import DevSafeViolation
from churchtune.platform.devsafe import is_dev_safe
from churchtune.wizard.protocol import Answer, Nav, Prompt, StepHeader, Text

Rule = str | Answer | Callable[[Prompt], Answer]


@dataclass
class ScriptedPrompter:
    """Answers by the keys in a prompt; Enter (the default) for everything else.

    The members ``mute``, ``headers``, ``meters`` and ``shown_keys`` serve the end-to-end tests.
    It also acts as the person in the hall: scratches the next microphone when asked, puts the
    microphones together for the calibration and on the named points for each round.
    """

    hall: FakeHall
    rules: dict[str, Rule] = field(default_factory=dict)
    # Presses MUTE on the console when asked (channels from the prompt); None: does nothing.
    mute: Callable[[int], None] | None = None
    scratch_order: list[int] = field(default_factory=list)
    headers: list[StepHeader] = field(default_factory=list)
    prompts: list[Prompt] = field(default_factory=list)
    meters: list[Mapping[str, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # It types the confirmation words by itself: never outside the emulator and the simulator.
        if not is_dev_safe():
            raise DevSafeViolation("the scripted volunteer works only in dev-safe mode")

    def header(self, header: StepHeader) -> None:
        """Remember the step header."""
        self.headers.append(header)

    def _act(self, prompt: Prompt) -> None:
        keys = [text.key for text in prompt.body]
        if "step-mics-scratch" in keys and self.scratch_order:
            self.hall.scratch(self.scratch_order.pop(0))
        if "step-calibration-place" in keys:
            self.hall.place_all(CALIBRATION_POINT)
        for text in prompt.body:
            if text.key == "step-points-place":
                mic = text.args["mic"]
                usb = mic.args["usb"] if isinstance(mic, Text) else None
                if not isinstance(usb, int | str):
                    raise TypeError(f"step-points-place without a microphone input: {mic!r}")
                self.hall.place(int(usb), f"p{text.args['point']}")
            if text.key in ("step-mics-mute-channels", "step-mics-still-open") and self.mute:
                for channel in str(text.args["channels"]).split(", "):
                    self.mute(int(channel))

    def ask(self, prompt: Prompt) -> Answer:
        """Do what the prompt asks in the hall, then answer by the first matching rule."""
        self.prompts.append(prompt)
        self._act(prompt)
        for text in prompt.body:
            rule = self.rules.get(text.key)
            if rule is None:
                continue
            if isinstance(rule, Answer):
                return rule
            if isinstance(rule, str):
                return Answer(rule)
            return rule(prompt)
        return Answer("")

    def progress(self, label: Text, done: int, total: int) -> None:
        """Progress is not shown."""

    def meter(self, levels_dbfs: Mapping[str, float]) -> None:
        """Remember the meter readings."""
        self.meters.append(dict(levels_dbfs))

    def shown_keys(self) -> list[str]:
        """Every message key shown, in order."""
        return [text.key for prompt in self.prompts for text in prompt.body]


QUIT = Answer(nav=Nav.QUIT)
