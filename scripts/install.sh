#!/bin/sh
# Install ChurchTune for the current user with one command:
#
#   curl -LsSf https://raw.githubusercontent.com/mihailShumilov/ChurchAutoTune/main/scripts/install.sh | sh
#
# What it does, and nothing else:
#   1. installs uv (https://docs.astral.sh/uv/) into ~/.local/bin if it is not there yet;
#   2. installs the `ctune` command with its own Python 3.12 (`uv tool install`), no admin rights;
#   3. runs the self-check, which uses only a console emulator built into the program and a
#      simulated hall: no real console, no sound card, nothing is played.
#
# Options (environment variables):
#   CHURCHTUNE_SOURCE  what to install (default: the GitHub repository at CHURCHTUNE_REF);
#                      a local wheel or directory also works
#   CHURCHTUNE_REF     branch or tag of the repository (default: main)
#   CHURCHTUNE_NO_SELFTEST=1  skip the self-check
#   CHURCHTUNE_NO_PATH=1      do not add uv's tool folder to the shell's PATH
set -eu

REPO="https://github.com/mihailShumilov/ChurchAutoTune"
REF="${CHURCHTUNE_REF:-main}"
# A source archive, not git+: a fresh Mac has no git until the developer tools are installed.
SOURCE="${CHURCHTUNE_SOURCE:-churchtune @ ${REPO}/archive/${REF}.tar.gz}"

say() { printf '%s\n' "$*"; }
die() { printf 'ChurchTune install: %s\n' "$*" >&2; exit 1; }

case "$(uname -s)" in
  Darwin) ;;
  *) say "Note: ChurchTune is made for macOS; on other systems it is not supported yet." ;;
esac

if ! command -v uv >/dev/null 2>&1; then
  command -v curl >/dev/null 2>&1 || die "curl is needed to download uv."
  say "Installing uv (the tool that installs ChurchTune and its Python)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="$HOME/.local/bin:$PATH"
  export PATH
  command -v uv >/dev/null 2>&1 || die "uv was installed but cannot be found; open a new terminal and run this again."
fi

say "Installing ChurchTune from ${SOURCE}..."
uv tool install --python 3.12 --force "$SOURCE"
if [ "${CHURCHTUNE_NO_PATH:-}" != "1" ]; then
  uv tool update-shell >/dev/null 2>&1 || true
fi

BIN="$(uv tool dir --bin)"
CTUNE="${BIN}/ctune"
[ -x "$CTUNE" ] || die "the ctune command was not installed into ${BIN}."

if [ "${CHURCHTUNE_NO_SELFTEST:-}" != "1" ]; then
  "$CTUNE" selftest
fi

say ""
say "Done. Open a new terminal window and type:  ctune"
