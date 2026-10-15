#!/bin/sh
# Install or update ChurchTune for the current user with one command:
#
#   curl -LsSf https://raw.githubusercontent.com/mihailShumilov/ChurchAutoTune/main/scripts/install.sh | sh
#
# What it does, and nothing else:
#   1. installs uv (https://docs.astral.sh/uv/), a pinned version, into ~/.local/bin if it is not
#      there yet;
#   2. installs (or updates) the `ctune` command with its own Python 3.12 (`uv tool install`), no
#      administrator rights, everything inside your home folder;
#   3. adds uv's tool folder to your shell's PATH (your shell's startup file), so that `ctune` is
#      found in a new Terminal window;
#   4. runs the self-check, which uses only a console emulator built into the program and a
#      simulated hall: no real console, no sound card, no sound.
#
# Options (environment variables):
#   CHURCHTUNE_SOURCE  what to install (default: the GitHub repository at CHURCHTUNE_REF);
#                      a local wheel or directory also works
#   CHURCHTUNE_REF     branch or tag of the repository (default: main)
#   CHURCHTUNE_NO_SELFTEST=1  skip the self-check
#   CHURCHTUNE_NO_PATH=1      leave the shell's startup files alone
set -eu

# Everything is inside main, called on the last line: a download cut short runs nothing.
main() {
  repo="https://github.com/mihailShumilov/ChurchAutoTune"
  ref="${CHURCHTUNE_REF:-main}"
  # A source archive, not git+: a fresh Mac has no git until the developer tools are installed.
  source="${CHURCHTUNE_SOURCE:-churchtune @ ${repo}/archive/${ref}.tar.gz}"
  uv_version="0.11.21"

  case "$(uname -s)" in
    Darwin) ;;
    *) say "Note: ChurchTune is made for macOS; on other systems it is not supported yet." ;;
  esac

  if [ "${CHURCHTUNE_NO_PATH:-}" = "1" ]; then
    UV_NO_MODIFY_PATH=1
    export UV_NO_MODIFY_PATH
  fi

  if ! command -v uv >/dev/null 2>&1; then
    command -v curl >/dev/null 2>&1 || die "curl is needed to download uv."
    say "Installing uv ${uv_version} (the tool that installs ChurchTune and its Python)..."
    UV_INSTALL_DIR="$HOME/.local/bin"
    export UV_INSTALL_DIR
    curl -LsSf "https://astral.sh/uv/${uv_version}/install.sh" | sh
    PATH="$UV_INSTALL_DIR:$PATH"
    export PATH
    command -v uv >/dev/null 2>&1 \
      || die "uv was installed but cannot be found; open a new Terminal window and run this again."
  fi
  uv tool --help >/dev/null 2>&1 \
    || die "your uv is too old to install tools; run 'uv self update' and then this again."

  if [ -n "${CHURCHTUNE_SOURCE:-}" ]; then
    say "Installing ChurchTune from ${source}..."
  else
    say "Installing ChurchTune (${ref})..."
  fi
  # --reinstall fetches the source again, so running this once more updates the program.
  uv tool install --python 3.12 --reinstall --force "$source" </dev/null

  bin="$(uv tool dir --bin)"
  ctune="${bin}/ctune"
  [ -x "$ctune" ] || die "the ctune command was not installed into ${bin}."
  run="ctune"
  if [ "${CHURCHTUNE_NO_PATH:-}" = "1" ]; then
    run="$ctune"
  elif ! uv tool update-shell </dev/null >/dev/null 2>&1; then
    say "Note: could not add ${bin} to your PATH; start the program as ${ctune}"
    run="$ctune"
  fi

  if [ "${CHURCHTUNE_NO_SELFTEST:-}" != "1" ]; then
    "$ctune" selftest </dev/null \
      || die "ChurchTune is installed, but the self-check failed (see above)."
  fi

  say ""
  say "Done. Open a new Terminal window and try the wizard safely, without a real console:"
  say "  ${run} --emulator"
}

say() { printf '%s\n' "$*"; }
die() { printf 'ChurchTune install: %s\n' "$*" >&2; exit 1; }

main "$@"
