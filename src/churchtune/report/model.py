"""What a session report is made of (SPEC §10), independent of language and file format.

``ReportData`` holds the facts of one wizard session; ``build_document`` (``sections``) turns them
into a ``Document`` of blocks whose texts are message keys (``Msg``). The renderers translate the
keys with a function they are given: this package never imports the catalogues (layer rule).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from churchtune.analysis.points import Analysis
from churchtune.analysis.verdict import VerifyResult
from churchtune.design.alignment import AlignmentResult
from churchtune.domain.arrays import FloatArray

# Renders a message key with plain arguments in the report's language.
Translate = Callable[[str, Mapping[str, str | int | float] | None], str]


@dataclass(frozen=True, slots=True)
class Msg:
    """A text to translate: a message key and its arguments (which may be texts too)."""

    key: str
    args: Mapping[str, "str | int | float | Msg"] = field(default_factory=dict)


Cell = str | Msg


@dataclass(frozen=True, slots=True)
class Paragraph:
    """One paragraph."""

    text: Msg


@dataclass(frozen=True, slots=True)
class Bullets:
    """A list; ``checklist`` renders as boxes to tick."""

    items: tuple[Msg, ...]
    checklist: bool = False


@dataclass(frozen=True, slots=True)
class Table:
    """A table with a header row."""

    head: tuple[Msg, ...]
    rows: tuple[tuple[Cell, ...], ...]


@dataclass(frozen=True, slots=True)
class Figure:
    """A picture (PNG) with a caption; ``name`` is its file name next to the Markdown report."""

    name: str
    png: bytes
    caption: Msg


Block = Paragraph | Bullets | Table | Figure


@dataclass(frozen=True, slots=True)
class Section:
    """One section of the report; ``id`` is stable (anchors, tests)."""

    id: str
    title: Msg
    blocks: tuple[Block, ...]


@dataclass(frozen=True, slots=True)
class Document:
    """The whole report."""

    title: Msg
    subtitle: Msg
    sections: tuple[Section, ...]


@dataclass(frozen=True, slots=True)
class PointQuality:
    """How one measured point came out (SPEC §8.2)."""

    name: str
    verdict: str
    snr_db: float
    pnr_db: float
    peak_dbfs: float
    repeat_db: float | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChangeRow:
    """One console setting written: what it is and what it was and became.

    ``what``, ``before`` and ``after`` are in words ("Main LR, band 3: frequency", "232 Hz");
    ``path`` and the plain values are kept for the JSON.
    """

    path: str
    what: Cell
    before: Cell
    after: Cell
    before_value: float | bool | str
    after_value: float | bool | str


@dataclass(frozen=True, slots=True)
class AuditRow:
    """One piece of channel advice (nothing is written; SPEC S11)."""

    code: str
    channels: tuple[int, ...]
    freq_hz: float | None = None
    gain_db: float | None = None


@dataclass(frozen=True, slots=True)
class ReportData:
    """The facts of one session the report tells about.

    Attributes:
        venue_name: The venue as the person named it.
        created: When the session was made (ISO text, for the JSON).
        created_text: The same in words of the report's language.
        tool_version: ChurchTune version.
        console_model: The console's model string, if one was read.
        roles: Speaker roles by path label ("matrix 3" -> "sub").
        variant: The system variant (topology), if known.
        analysis: The baseline analysis.
        target_db: The house curve on the analysis grid.
        points: Quality of each baseline point.
        alignment: The sub / tops junction, when it was measured apart.
        applied_state: "applied", "nothing" or "reverted"; None when the person never got there.
        changes: What was written.
        undo_command: The terminal command that puts the old values back, ready to paste.
        changes_incomplete: The plan stopped before every write was sent; ``changes`` lists
            what reached the console.
        verify: The check measurement's numbers and verdict.
        verify_analysis: The check measurement's analysis (same points).
        audit: Channel advice.
        sweep: Measurement parameters for the appendix (name -> value).
        calibration: Which microphone calibration was used, if any.
        mic_count: Microphones used.
        places: Where each point is, in words (point name -> text); names stand for themselves
            without it.
        advice: What the person was advised to set by hand (delay, polarity); never written.
    """

    venue_name: str
    created: str
    tool_version: str
    created_text: Cell = ""
    console_model: str | None = None
    roles: Mapping[str, str] = field(default_factory=dict)
    variant: str | None = None
    analysis: Analysis | None = None
    target_db: FloatArray | None = None
    points: tuple[PointQuality, ...] = ()
    alignment: AlignmentResult | None = None
    applied_state: str | None = None
    changes: tuple[ChangeRow, ...] = ()
    undo_command: str | None = None
    changes_incomplete: bool = False
    verify: VerifyResult | None = None
    verify_analysis: Analysis | None = None
    audit: tuple[AuditRow, ...] = ()
    sweep: Mapping[str, str | float | int] = field(default_factory=dict)
    calibration: str | None = None
    mic_count: int = 1
    places: Mapping[str, Cell] = field(default_factory=dict)
    advice: tuple[Msg, ...] = ()
