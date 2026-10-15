"""``ctune report``: the report of a wizard session again, or its text for a chat (SPEC §10, §12).

The report is made from the session's files alone: no console, no sound.
"""

import shlex
from pathlib import Path
from typing import Annotated

import typer

from churchtune.cli.context import EXIT_OK, fail, settings_of
from churchtune.cli.package_writes import describe_undo
from churchtune.errors import ChurchTuneError
from churchtune.i18n import LANGUAGES, Translator, detect_language
from churchtune.report.chat import for_chat
from churchtune.storage.files import read_wizard_state
from churchtune.storage.paths import Workspace
from churchtune.wizard.labels import short_path
from churchtune.wizard.reporting import REPORT_DIR, report_data, write_report


def _newest_session(workspace: Workspace) -> Path | None:
    sessions = [s for venue in workspace.venues() for s in workspace.sessions_of(venue)]
    return max(sessions, key=lambda p: p.name, default=None)


def report_command(
    ctx: typer.Context,
    session: Annotated[
        Path | None, typer.Argument(help="Session directory; the newest by default.")
    ] = None,
    lang: Annotated[
        str | None, typer.Option("--lang", help="en, ru or uk; the session's by default.")
    ] = None,
    chat: Annotated[
        bool, typer.Option("--for-chat", help="Print a short text to paste into a chat instead.")
    ] = False,
) -> None:
    """Write the report of a wizard session (HTML, Markdown, JSON), or print its chat text."""
    workspace = settings_of(ctx).workspace
    directory = session or _newest_session(workspace)
    fallback = Translator(lang if lang in LANGUAGES else detect_language())
    if directory is None:
        fail(fallback("err-no-session"))
    try:
        state = read_wizard_state(directory)
    except (ValueError, OSError):
        fail(fallback("err-bad-session"))
    language = lang or state.lang
    if language not in LANGUAGES:
        fail(fallback("err-unknown-language"))
    translate = Translator(language)
    try:
        data = report_data(state, directory, workspace, describe=describe_undo)
        if chat:
            typer.echo(for_chat(data, translate), nl=False)
            raise typer.Exit(EXIT_OK)
        files = write_report(data, directory / REPORT_DIR, translate, language)
    except (ChurchTuneError, OSError, ValueError) as error:
        fail(translate("err-report-failed", {"error": str(error)}))
    command = f"open {shlex.quote(str(files.html))}"
    typer.echo(
        translate("step-finish-report", {"path": short_path(files.html), "command": command})
    )
    raise typer.Exit(EXIT_OK)
