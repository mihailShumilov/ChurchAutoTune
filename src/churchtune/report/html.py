"""The report as one self-contained HTML file (SPEC §10).

Styles and charts are inside the file (PNG as data URIs): it opens without internet, follows the
system's light or dark theme and prints on white. Every text is escaped.
"""

import base64
import html

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

STYLE = """
:root {
  --bg: #fbfaf7; --ink: #1f1f1e; --ink2: #5f5e57; --line: #dcdad2; --card: #ffffff;
  --accent: #1c5cab; --ok: #1b7f4b; --warn: #a15c00;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #161615; --ink: #ecebe6; --ink2: #b3b1a8; --line: #3a3935; --card: #22221f;
    --accent: #7fb0f0; --ok: #6fd3a0; --warn: #f0b45c;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.55 -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}
main { max-width: 920px; margin: 0 auto; padding: 24px 16px 64px; }
header h1 { font-size: 1.7rem; margin: 0 0 4px; }
header p { color: var(--ink2); margin: 0 0 16px; }
nav ol { columns: 2; padding-left: 1.2em; color: var(--ink2); }
nav a { color: var(--accent); text-decoration: none; }
section { border-top: 1px solid var(--line); padding-top: 12px; margin-top: 24px; }
h2 { font-size: 1.25rem; margin: 0 0 8px; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 12px; font-size: 0.95rem; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line);
  vertical-align: top; }
th { color: var(--ink2); font-weight: 600; }
figure { margin: 12px 0; background: #ffffff; border: 1px solid var(--line); border-radius: 8px;
  padding: 8px; }
figure img { width: 100%; height: auto; display: block; }
figcaption { color: #5f5e57; font-size: 0.9rem; padding: 6px 4px 0; }
ul.checklist { list-style: none; padding-left: 0; }
ul.checklist li::before { content: "\\2610\\00a0\\00a0"; color: var(--accent); }
code { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 0.9em; }
@media (max-width: 640px) { nav ol { columns: 1; } table { font-size: 0.85rem; } }
@media print {
  :root { --bg: #ffffff; --ink: #000000; --ink2: #444444; --line: #bbbbbb; --card: #ffffff; }
  nav { display: none; }
  main { max-width: none; padding: 0; }
  section, figure, table { break-inside: avoid; }
}
"""


def render_msg(msg: Msg, tr: Translate) -> str:
    """The text of ``msg``, nested texts rendered first."""
    args = {name: render_msg(v, tr) if isinstance(v, Msg) else v for name, v in msg.args.items()}
    return tr(msg.key, args)


def _cell(cell: Cell, tr: Translate) -> str:
    return html.escape(render_msg(cell, tr) if isinstance(cell, Msg) else cell)


def _block(block: Block, tr: Translate) -> str:
    if isinstance(block, Paragraph):
        return f"<p>{html.escape(render_msg(block.text, tr))}</p>"
    if isinstance(block, Bullets):
        css = ' class="checklist"' if block.checklist else ""
        items = "".join(f"<li>{html.escape(render_msg(m, tr))}</li>" for m in block.items)
        return f"<ul{css}>{items}</ul>"
    if isinstance(block, Table):
        head = "".join(f"<th>{_cell(h, tr)}</th>" for h in block.head)
        rows = "".join(
            "<tr>" + "".join(f"<td>{_cell(c, tr)}</td>" for c in row) + "</tr>"
            for row in block.rows
        )
        return f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"
    return _figure(block, tr)


def _figure(figure: Figure, tr: Translate) -> str:
    data = base64.b64encode(figure.png).decode("ascii")
    caption = html.escape(render_msg(figure.caption, tr))
    return (
        f'<figure><img src="data:image/png;base64,{data}" alt="{caption}">'
        f"<figcaption>{caption}</figcaption></figure>"
    )


def render_html(document: Document, tr: Translate, lang: str) -> str:
    """The whole report as an HTML page in ``lang``."""
    title = html.escape(render_msg(document.title, tr))
    toc = "".join(
        f'<li><a href="#{s.id}">{html.escape(render_msg(s.title, tr))}</a></li>'
        for s in document.sections
    )
    body = "".join(
        f'<section id="{s.id}"><h2>{html.escape(render_msg(s.title, tr))}</h2>'
        + "".join(_block(b, tr) for b in s.blocks)
        + "</section>"
        for s in document.sections
    )
    subtitle = html.escape(render_msg(document.subtitle, tr))
    contents = html.escape(tr("report-contents", None))
    return (
        f'<!doctype html><html lang="{html.escape(lang)}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        f"<title>{title}</title><style>{STYLE}</style></head><body><main>"
        f"<header><h1>{title}</h1><p>{subtitle}</p></header>"
        f'<nav aria-label="{contents}"><ol>{toc}</ol></nav>{body}</main></body></html>\n'
    )
