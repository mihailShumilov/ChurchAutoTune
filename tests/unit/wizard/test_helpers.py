"""Wizard helpers: console words, the protocol, hall size, open mic channels, calibration."""

import json

import numpy as np
import pytest

from churchtune.domain.snapshot import SignalRef, SourceKind
from churchtune.i18n import Translator
from churchtune.storage.models import MicCalibrationFile, MicCalibrationModel
from churchtune.wizard.labels import issue_args, label_text, signal_text
from churchtune.wizard.protocol import Answer, Kind, Nav, Option, Prompt, Text, render_text, to_dict
from churchtune.wizard.state import Navigate
from churchtune.wizard.steps.finish import _point_corrections
from churchtune.wizard.steps.measure import _numbers, place_text
from churchtune.wizard.steps.setup import parse_hall_size
from churchtune.wizard.ui import typed_confirmation


def ru(text: Text) -> str:
    return render_text(text, Translator("ru"))


def test_console_words_are_translated():
    assert ru(signal_text(SignalRef(SourceKind.MATRIX, 3))) == "матрица 3"
    assert ru(signal_text(SignalRef(SourceKind.MAIN_L))) == "Main L"
    label = label_text("main 8")
    assert isinstance(label, Text)
    assert ru(label) == "выход 8"
    assert label_text("something else") == "something else"
    assert label_text("weird 3") == "weird 3"


def test_issue_parameters_become_texts():
    args = issue_args(
        {
            "paths": "bus 3, matrix 5",
            "items": "main 8: 1.5 ms; matrix 3: polarity inverted; odd",
            "role": "fill",
            "freq_hz": 80,
            "bus": "free text",
        }
    )
    issue = Text("issue-monitors-on-outputs", {"paths": args["paths"]})
    assert (
        ru(issue)
        == "Мониторы на выходах: шина 3, матрица 5. Тестовый сигнал не должен в них попасть."
    )
    items = ru(Text("issue-existing-alignment", {"items": args["items"]}))
    assert "выход 8: задержка 1,5 мс" in items
    assert "матрица 3: перевёрнута полярность" in items
    assert items.endswith("odd.")
    assert args["freq_hz"] == 80
    assert args["bus"] == "free text"
    assert isinstance(args["role"], Text)
    assert issue_args(None) == {}


def test_prompts_serialize_to_plain_data():
    prompt = Prompt(
        Kind.CHOOSE,
        (Text("step-welcome-goal"),),
        (Option("full", Text("step-welcome-goal-full"), True),),
        default="full",
    )
    data = to_dict(prompt)
    assert json.loads(json.dumps(data))["options"][0]["id"] == "full"
    assert to_dict(Answer("x")) == {"value": "x", "nav": None}


def test_nested_texts_and_lists_render_inside_messages():
    text = Text("common-mic", {"number": 2, "usb": 11})
    assert ru(text) == "Микрофон 2 (USB-вход 11)"
    listed = Text("alignment-polarity", {"where": (Text("signal-bus", {"number": 1}), "x")})
    assert ru(listed) == "шина 1, x: перевёрнута полярность"


@pytest.mark.parametrize(
    ("typed", "size"),
    [("14 x 22", (14.0, 22.0)), ("14,5х20", (14.5, 20.0)), ("14 22", (14.0, 22.0))],
)
def test_hall_sizes(typed, size):
    hall = parse_hall_size(typed)
    assert hall is not None
    assert (hall.width_m, hall.depth_m) == size


@pytest.mark.parametrize("typed", ["14", "a x b", "1 x 2", "14 x 22 x 3", "500 x 20"])
def test_not_hall_sizes(typed):
    assert parse_hall_size(typed) is None


def test_places_of_the_default_plan_and_of_others():
    points = tuple(f"p{n}" for n in range(1, 9))
    assert ru(place_text(points, "p1")) == "передние ряды, слева"
    assert ru(place_text(("a", "b"), "b")) == "место 2"


class Words:
    def __init__(self, *answers):
        self.answers = list(answers)

    def ask(self, prompt):
        return self.answers.pop(0)


def test_typed_confirmation_needs_the_word():
    assert typed_confirmation(Words(Answer(" Apply ")), Text("x"), word="apply")
    assert not typed_confirmation(Words(Answer("yes")), Text("x"), word="apply")
    with pytest.raises(Navigate):
        typed_confirmation(Words(Answer(nav=Nav.QUIT)), Text("x"), word="apply")


def test_a_rejected_microphone_keeps_raw_points_and_the_others_are_corrected():
    centres = (125.0, 1000.0, 8000.0)
    good = MicCalibrationModel(
        usb_input=10, model="m", sensitivity_db=1.0, delay_ms=0.0, inverted=False,
        shape_db=(0.0, 0.0, 0.0),
    )  # fmt: skip
    bad = good.model_copy(update={"usb_input": 11, "problems": ("shape",)})
    calibration = MicCalibrationFile(set_id="s", created="2026-10-01T10:00:00",
                                     centres_hz=centres, mics=(good, bad))  # fmt: skip
    curves = _point_corrections(calibration, {"p1": 10, "p2": 11, "p3": None})
    assert set(curves) == {"p1"}
    assert np.allclose(curves["p1"], 1.0)
    assert _point_corrections(None, {"p1": 10}) == {}


@pytest.mark.parametrize(
    ("numbers", "text"),
    [([1, 2, 3, 4, 5, 6, 7, 11, 12], "1–7, 11, 12"), ([12], "12"), ([3, 1, 2, 2], "1–3"), ([], "")],
)
def test_number_lists_are_short(numbers, text):
    assert _numbers(numbers) == text
