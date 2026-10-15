"""The report in Markdown (SPEC §10): the same document, charts as PNG files next to it."""

from churchtune.report.html import render_msg
from churchtune.report.model import (
    Block,
    Bullets,
    Cell,
    Document,
    Figure,
    Msg,
    Paragraph,
    Table,
    Translate,
)


def _cell(cell: Cell, tr: Translate) -> str:
    text = render_msg(cell, tr) if isinstance(cell, Msg) else cell
    return text.replace("|", "\\|").replace("\n", " ")


def _block(block: Block, tr: Translate) -> str:
    if isinstance(block, Paragraph):
        return render_msg(block.text, tr)
    if isinstance(block, Bullets):
        mark = "- [ ] " if block.checklist else "- "
        return "\n".join(mark + render_msg(m, tr) for m in block.items)
    if isinstance(block, Table):
        head = "| " + " | ".join(_cell(h, tr) for h in block.head) + " |"
        rule = "|" + "---|" * len(block.head)
        rows = ["| " + " | ".join(_cell(c, tr) for c in row) + " |" for row in block.rows]
        return "\n".join([head, rule, *rows])
    return _figure(block, tr)


def _figure(figure: Figure, tr: Translate) -> str:
    caption = render_msg(figure.caption, tr)
    return f"![{caption}]({figure.name})\n\n*{caption}*"


def render_markdown(document: Document, tr: Translate) -> str:
    """The whole report as Markdown; figures refer to ``Figure.name`` beside the file."""
    parts = [f"# {render_msg(document.title, tr)}", render_msg(document.subtitle, tr)]
    for section in document.sections:
        parts.append(f"## {render_msg(section.title, tr)}")
        parts.extend(_block(b, tr) for b in section.blocks)
    return "\n\n".join(parts) + "\n"


def figures_of(document: Document) -> list[Figure]:
    """Every figure of the document, to be written next to the Markdown file."""
    return [b for s in document.sections for b in s.blocks if isinstance(b, Figure)]
