"""Console words as translated texts: "matrix 3", "bus 1", "output 8" (skill wizard-ux).

The driver describes signals with short codes (``SignalRef.label()``: ``matrix 3``, ``main_l``)
and topology issues carry them in their parameters. These helpers turn such codes into message
keys, so the interface shows "матриця 3" instead of "matrix 3".
"""

import re
import shlex
from collections.abc import Mapping
from pathlib import Path

from churchtune.domain.snapshot import OutputKind, SignalRef, SourceKind
from churchtune.storage.paths import DEFAULT_HOME
from churchtune.wizard.protocol import ArgValue, Text
from churchtune.wizard.ui import kebab, t

_LABEL = re.compile(r"^(?P<kind>[a-z_]+)(?: (?P<number>\d+))?$")
_DELAY = re.compile(r"^(?P<ms>[0-9.]+) ms$")
SOURCE_KINDS = frozenset(k.value for k in SourceKind)
MAIN_LR = "main_lr"  # the Main LR strip as one EQ block
OUTPUT_KINDS = frozenset(k.value for k in OutputKind)


def signal_text(ref: SignalRef) -> Text:
    """A mix signal: "matrix 3", "Main L", "direct out of channel 10"."""
    return t(f"signal-{kebab(ref.kind.value)}", number=ref.number)


def output_text(kind: str, number: int) -> Text:
    """A physical output: "output 8", "aux out 2"."""
    return t(f"output-{kind}", number=number)


def label_text(label: str) -> Text | str:
    """A driver label (``matrix 3``, ``main 8``) as a text; anything else stays as it is."""
    if label == MAIN_LR:
        return t("signal-main-lr")
    match = _LABEL.match(label)
    if match is None:
        return label
    kind, number = match["kind"], int(match["number"] or 0)
    if kind in OUTPUT_KINDS and number:
        return output_text(kind, number)
    if kind in SOURCE_KINDS:
        return t(f"signal-{kebab(kind)}", number=number)
    return label


def _alignment_item(item: str) -> Text | str:
    """``main 8: 1.5 ms`` or ``matrix 3: polarity inverted`` as a text."""
    where, _, what = item.partition(": ")
    delay = _DELAY.match(what)
    if delay is not None:
        return t("alignment-delay", where=label_text(where), ms=float(delay["ms"]))
    if what == "polarity inverted":
        return t("alignment-polarity", where=label_text(where))
    return item


def issue_args(params: Mapping[str, str | float | int] | None) -> dict[str, ArgValue]:
    """Parameters of a topology issue with console words translated."""
    args: dict[str, ArgValue] = {}
    for name, value in (params or {}).items():
        if not isinstance(value, str):
            args[name] = value
        elif name == "items":
            args[name] = tuple(_alignment_item(i) for i in value.split("; "))
        elif name == "role":
            args[name] = t(f"role-{kebab(value)}")
        elif name in ("path", "paths", "mains", "matrices"):
            args[name] = tuple(label_text(v) for v in value.split(", "))
        else:
            args[name] = value
    return args


def short_path(path: Path) -> str:
    """A path as short as the shell understands it (``~`` for the home folder)."""
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def undo_command(root: Path, undo_path: Path, console: str | None) -> str:
    """The terminal command that puts an undo file's old values back, ready to paste.

    It names the console (the wizard finds it by itself and stores no address) and the data
    folder when it is not the default one; every part is quoted for the shell, the home folder
    kept as ``~`` so the command stays short.
    """
    parts = ["ctune"]
    if root.resolve() != DEFAULT_HOME.expanduser().resolve():
        parts += ["--home", _shell_path(root)]
    if console:
        parts += ["--console", shlex.quote(console)]
    return " ".join([*parts, "undo", _shell_path(undo_path)])


def _shell_path(path: Path) -> str:
    try:
        return "~/" + shlex.quote(str(path.relative_to(Path.home())))
    except ValueError:
        return shlex.quote(str(path))
