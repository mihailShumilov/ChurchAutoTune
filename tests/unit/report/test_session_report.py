"""The session report (SPEC §10, M6): every section, offline, both themes, three languages."""

import json
import re
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path

import numpy as np
import pytest

from churchtune.analysis.points import PointResponse, analyze_points
from churchtune.analysis.verdict import Verdict, VerifyResult
from churchtune.audio.fake.room import synthetic_room
from churchtune.design.alignment import JunctionPoint, analyze_alignment
from churchtune.i18n import LANGUAGES, Translator
from churchtune.report.chat import for_chat
from churchtune.report.html import render_html
from churchtune.report.model import AuditRow, ChangeRow, Msg, PointQuality, ReportData
from churchtune.report.sections import SECTION_IDS, build_document
from churchtune.report.session_json import report_json
from churchtune.report.session_md import figures_of, render_markdown

FS = 48000.0
SNAPSHOT = Path(__file__).with_name("report_structure.txt")
KEY_LIKE = re.compile(
    r"\b(?:report|audit|finding|role|variant|output|glossary)-[a-z0-9]+(?:-[a-z0-9]+)+\b"
)


def rooms(count: int) -> dict[str, np.ndarray]:
    return {
        f"p{i + 1}": synthetic_room(np.random.default_rng(40 + i), FS, length_s=1.0, drr_db=12.0)
        for i in range(count)
    }


@pytest.fixture(scope="module")
def data() -> ReportData:
    analysis = analyze_points({n: PointResponse(ir, FS) for n, ir in rooms(4).items()})
    subs, tops = rooms(3), rooms(3)
    junction = {n: JunctionPoint(sub=subs[n], tops=tops[n], both=subs[n] + tops[n]) for n in subs}
    return ReportData(
        venue_name="Test <church> & hall",
        created="2026-10-15T10:00:00",
        tool_version="0.1.0.dev0",
        console_model="X32 Producer",
        roles={"main 8": "top_l", "main 7": "top_r", "main 6": "sub", "main 4": "unused"},
        variant="a_tops_and_sub_on_own_paths",
        analysis=analysis,
        target_db=np.zeros_like(analysis.grid_hz),
        points=tuple(
            PointQuality(n, "ok" if n != "p4" else "warn", 42.0, 58.0, -14.0, 0.3)
            for n in analysis.point_names
        ),
        alignment=analyze_alignment(junction, 100.0, FS),
        applied_state="applied",
        changes=(
            ChangeRow(
                "/main/st/eq/2/g",
                Msg("write-band-gain", {"path": Msg("signal-main-lr"), "band": 2}),
                Msg("value-db", {"db": -4.0}),
                Msg("value-db", {"db": -6.5}),
                -4.0,
                -6.5,
            ),
        ),
        places={"p1": "front rows, left"},
        advice=(Msg("apply-advice-side-delay", {"side": Msg("apply-advice-side-sub"), "ms": 2.5}),),
        undo_file="venues/test/sessions/1/undo/1.json",
        audit=(AuditRow("deep-cut", (3, 5), 400.0, -9.0),),
        sweep={"fs_hz": 48000, "sweep_s": 6.0},
        mic_count=4,
    )


class Structure(HTMLParser):
    """Section ids, headings and the kinds of blocks in each section: the page's skeleton."""

    BLOCKS = frozenset({"p", "ul", "table", "figure"})

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []
        self.depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "section":
            self.lines.append(f"section {values.get('id')}")
            self.depth += 1
        elif self.depth and tag in self.BLOCKS:
            css = f".{values['class']}" if values.get("class") else ""
            self.lines.append(f"  {tag}{css}")

    def handle_endtag(self, tag: str) -> None:
        if tag == "section":
            self.depth -= 1


def structure(page: str) -> str:
    parser = Structure()
    parser.feed(page)
    return "\n".join(parser.lines) + "\n"


def test_the_html_structure_matches_the_snapshot(data):
    """Snapshot of the page's skeleton (SPEC M6). Update the file only after reviewing the diff."""
    tr = Translator("en")
    page = render_html(build_document(data, tr), tr, "en")
    assert structure(page) == SNAPSHOT.read_text(encoding="utf-8")


def test_every_section_of_spec_10_is_there_in_order(data):
    tr = Translator("en")
    document = build_document(data, tr)
    assert tuple(s.id for s in document.sections) == SECTION_IDS


def test_the_page_opens_offline_in_both_themes_and_prints(data):
    tr = Translator("en")
    page = render_html(build_document(data, tr), tr, "en")
    assert "http://" not in page
    assert "https://" not in page
    assert "<script" not in page
    assert "<link" not in page  # styles inside the file
    assert "prefers-color-scheme: dark" in page
    assert "@media print" in page
    assert 'name="color-scheme" content="light dark"' in page
    assert page.count('src="data:image/png;base64,') == 4


def test_names_are_escaped(data):
    tr = Translator("en")
    page = render_html(build_document(data, tr), tr, "en")
    assert "Test &lt;church&gt; &amp; hall" in page
    assert "<church>" not in page


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_text_is_translated_in_every_language(data, lang):
    tr = Translator(lang)
    document = build_document(data, tr)
    page = render_html(document, tr, lang)
    markdown = render_markdown(document, tr)
    chat = for_chat(data, tr)
    assert f'<html lang="{lang}">' in page
    for text in (page, markdown, chat):
        assert not KEY_LIKE.findall(text)


def test_the_markdown_has_every_section_and_its_charts(data):
    tr = Translator("en")
    document = build_document(data, tr)
    markdown = render_markdown(document, tr)
    for section in document.sections:
        assert f"## {tr(section.title.key, None)}" in markdown
    names = [f.name for f in figures_of(document)]
    assert len(names) == len(set(names)) == 4
    assert all(f"]({name})" in markdown for name in names)
    assert "- [ ] " in markdown  # the checklist


def test_the_json_is_plain_data_without_language(data):
    out = json.loads(json.dumps(report_json(data)))
    assert out["schema_version"] == 1
    assert out["venue"] == "Test <church> & hall"
    assert len(out["baseline"]["average_db"]) == len(out["baseline"]["thirds_hz"])
    assert out["junction"]["fc_hz"] == 100.0
    assert out["changes"] == [{"path": "/main/st/eq/2/g", "before": -4.0, "after": -6.5}]
    assert all(f["key"].startswith(("finding-", "glossary-")) for f in out["findings"])


def test_the_chat_text_is_a_few_kilobytes(data):
    text = for_chat(data, Translator("en"))
    assert 1000 < len(text.encode("utf-8")) <= 6000
    assert "Average:" in text
    assert "main speaker, left" in text  # roles in words, not codes


def test_a_session_that_only_measured_says_so():
    tr = Translator("en")
    bare = ReportData(venue_name="Hall", created="2026-10-15", tool_version="0.1.0.dev0")
    document = build_document(bare, tr)
    assert tuple(s.id for s in document.sections) == SECTION_IDS
    page = render_html(document, tr, "en")
    assert "nothing was written to the console" in page
    assert "<figure>" not in page


@pytest.mark.parametrize("verdict", [Verdict.WORSE, Verdict.NO_CHANGE])
def test_after_the_check_and_the_undo_nothing_asks_to_undo_again(data, verdict):
    """The check said worse, the person put the old values back: the console is as it was."""
    worse = VerifyResult(
        points=("p1",),
        m_before_db=2.0,
        m_after_db=3.0,
        worst_point="p1",
        worst_delta_db=1.5,
        band_delta_db=4.0,
        band_point="p1",
        band_hz=125.0,
        spread_delta_db=0.5,
        verdict=verdict,
    )
    tr = Translator("en")
    page = render_html(
        build_document(replace(data, verify=worse, applied_state="reverted"), tr), tr, "en"
    )
    assert "already been put back" in page
    assert "the command is in" not in page  # there is no undo command to point to
    assert "Put the old values back" not in page
    assert "ctune undo" not in page
