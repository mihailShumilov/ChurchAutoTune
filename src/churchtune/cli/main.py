"""Root Typer application: global options and the expert commands."""

import os
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer

from churchtune import __version__
from churchtune.cli import (
    analyze,
    apply,
    apply_packages,
    check,
    console,
    hwcheck,
    measure,
    mics,
    report,
    selftest,
    sub,
    wizard,
)
from churchtune.cli.context import EXIT_REFUSED, Settings, load_venue, parse_console, say
from churchtune.errors import ChurchTuneError, DevSafeViolation
from churchtune.platform.devsafe import DEV_SAFE_VAR
from churchtune.storage.paths import workspace_from_env

CONSOLE_VAR = "CTUNE_CONSOLE"
AUDIO_VAR = "CTUNE_AUDIO"
SELFTEST = "selftest"

app = typer.Typer(
    name="ctune",
    help="Measure the room and tune a Behringer X32 / Midas M32 system.",
    invoke_without_command=True,
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
    emulator: Annotated[
        bool,
        typer.Option(
            "--emulator",
            help="Try it safely: a built-in console emulator and a simulated hall, no sound.",
        ),
    ] = False,
    version: Annotated[  # noqa: ARG001 - handled by its eager callback
        bool,
        typer.Option(
            "--version", callback=_print_version, is_eager=True, help="Show the version and exit."
        ),
    ] = False,
) -> None:
    """Measure the room and tune a Behringer X32 / Midas M32 system."""
    if ctx.invoked_subcommand == SELFTEST:
        return  # it builds its own emulator, hall and folder; the person's settings play no part
    workspace = workspace_from_env(home, os.environ)
    venue = load_venue(workspace)
    if console:
        host, port = parse_console(console)
        venue = replace(venue, console_host=host, console_port=port)
    if emulator:
        from churchtune.cli.demo import prepare_demo  # noqa: PLC0415 - dev only
        from churchtune_emulator.x32 import LOOPBACK, X32Emulator  # noqa: PLC0415 - dev only

        # Only the emulator and the simulated hall for the whole run: no discovery broadcast
        # can find the church's real console next to it (dev-safe refuses non-loopback hosts).
        os.environ[DEV_SAFE_VAR] = "1"
        emu = X32Emulator().start()
        ctx.call_on_close(emu.stop)
        venue = replace(venue, console_host=LOOPBACK, console_port=emu.port)
        audio = "fake"
        hall_link = prepare_demo(emu)
    else:
        hall_link = None
    ctx.obj = Settings(workspace, venue, audio or venue.audio_device or "fake",
                       hall_link=hall_link)  # fmt: skip
    if ctx.invoked_subcommand is None:
        wizard.wizard_command(ctx)  # SPEC §12: plain ``ctune`` is the wizard


app.command("check")(check.check_command)
app.command("backup")(check.backup_command)
app.command("level")(measure.level_command)
app.command("measure")(measure.measure_command)
app.command("verify")(measure.verify_command)
app.command("analyze")(analyze.analyze_command)
app.command("propose")(analyze.propose_command)
app.command("export-for-chat")(analyze.export_command)
app.command("apply")(apply.apply_command)
app.command("apply-packages")(apply_packages.apply_packages_command)
app.command("undo")(apply.undo_command)
app.command("sub")(sub.sub_command)
app.command("hwcheck")(hwcheck.hwcheck_command)
app.command("doctor")(console.doctor_command)
app.command("snapshot")(console.snapshot_command)
app.command("emulator")(console.emulator_command)
app.command("devices")(mics.devices_command)
app.command("noise")(mics.noise_command)
app.command("pathcheck")(mics.pathcheck_command)
app.add_typer(mics.mics_app, name="mics")
app.command("wizard")(wizard.wizard_command)
app.command("resume")(wizard.resume_command)
app.command("report")(report.report_command)
app.command(SELFTEST)(selftest.selftest_command)


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
