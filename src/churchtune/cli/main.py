"""Root Typer application."""

from typing import Annotated

import typer

from churchtune import __version__

app = typer.Typer(
    name="ctune",
    help="Measure the room and tune a Behringer X32 / Midas M32 system.",
    no_args_is_help=True,
    add_completion=False,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_print_version, is_eager=True, help="Show the version and exit."
        ),
    ] = False,
) -> None:
    """Measure the room and tune a Behringer X32 / Midas M32 system."""
