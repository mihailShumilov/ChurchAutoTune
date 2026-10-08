from typer.testing import CliRunner

import churchtune
from churchtune.cli import app


def test_cli_version_prints_package_version():
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == churchtune.__version__
