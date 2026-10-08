"""Root Typer application: global options and the expert commands."""

import os
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer

from churchtune import __version__
from churchtune.cli import analyze, apply, check, measure, sub
from churchtune.cli.context import EXIT_REFUSED, Settings, load_venue, parse_console, say
from churchtune.errors import ChurchTuneError, DevSafeViolation
from churchtune.storage.paths import workspace_from_env

CONSOLE_VAR = "CTUNE_CONSOLE"
AUDIO_VAR = "CTUNE_AUDIO"

app = typer.Typer(
    name="ctune",
    help="Measure the room and tune a Behringer X32 / Midas M32 system.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def main(
    ctx: typer.Context,
    console: Annotated[
        str | None,
        typer.Option("--console", envvar=CONSOLE_VAR, help="Console address, HOST[:PORT]."),
    ] = None,
    audio: Annotated[
        str | None,
        typer.Option("--audio", envvar=AUDIO_VAR, help="Audio device name, or 'fake'."),
    ] = None,
    home: Annotated[
        Path | None,
        typer.Option("--home", help="Data directory (default: CTUNE_HOME or ~/ChurchTune)."),
    ] = None,
    version: Annotated[  # noqa: ARG001 - handled by its eager callback
        bool,
        typer.Option(
            "--version", callback=_print_version, is_eager=True, help="Show the version and exit."
        ),
    ] = False,
) -> None:
    """Measure the room and tune a Behringer X32 / Midas M32 system."""
    workspace = workspace_from_env(home, os.environ)
    venue = load_venue(workspace)
    if console:
        host, port = parse_console(console)
        venue = replace(venue, console_host=host, console_port=port)
    ctx.obj = Settings(workspace, venue, audio or venue.audio_device or "fake")


app.command("check")(check.check_command)
app.command("backup")(check.backup_command)
app.command("level")(measure.level_command)
app.command("measure")(measure.measure_command)
app.command("verify")(measure.verify_command)
app.command("analyze")(analyze.analyze_command)
app.command("propose")(analyze.propose_command)
app.command("export-for-chat")(analyze.export_command)
app.command("apply")(apply.apply_command)
app.command("undo")(apply.undo_command)
app.command("sub")(sub.sub_command)


def run() -> None:
    """Entry point: run the app and turn domain errors into a message and an exit code."""
    try:
        app()
    except DevSafeViolation as error:
        say(f"ОТКАЗ (dev-safe): {error}")
        raise SystemExit(EXIT_REFUSED) from None
    except ChurchTuneError as error:
        say(f"!! {error}")
        raise SystemExit(1) from None
