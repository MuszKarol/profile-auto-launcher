#!/usr/bin/env bash
# Installs profile-auto-launcher for the current user.
#
# The package install is all this script does itself; everything after it —
# sample profiles, the JSON Schema, the menu entry and the login item — is
# `palaunch install`, so the shell script and the setup window cannot drift
# apart. Works on Linux and macOS.
#
#   scripts/install-linux.sh                 install + register autostart
#   scripts/install-linux.sh --no-autostart  install only
#   scripts/install-linux.sh --gui           open the setup window at the end

set -euo pipefail

AUTOSTART=1
GUI=0

for arg in "$@"; do
    case "$arg" in
        --no-autostart) AUTOSTART=0 ;;
        --gui)          GUI=1 ;;
        -h|--help)
            sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *)
            echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "==> Installing profile-auto-launcher from $REPO_DIR"

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found. Install Python 3.10+ first." >&2
    exit 1
fi

PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if [ "$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 10) else 0)')" != "1" ]; then
    echo "ERROR: Python $PY_VER is too old; need 3.10+." >&2
    exit 1
fi
echo "==> Using Python $PY_VER"

if ! python3 -m pip --version >/dev/null 2>&1; then
    echo "==> pip not found — installing via ensurepip..."
    python3 -m ensurepip --upgrade
fi

python3 -m pip install --user --upgrade "$REPO_DIR"'[full]'

PALAUNCH_BIN="$(command -v palaunch || echo "$(python3 -c 'import site; print(site.USER_BASE)')/bin/palaunch")"
if [ ! -x "$PALAUNCH_BIN" ]; then
    echo "ERROR: palaunch not found at $PALAUNCH_BIN." >&2
    echo "       Add \$HOME/.local/bin to your PATH and re-run." >&2
    exit 1
fi
echo "==> palaunch binary: $PALAUNCH_BIN"

if [ "$GUI" = "1" ]; then
    exec "$PALAUNCH_BIN" install
fi

if [ "$AUTOSTART" = "1" ]; then
    "$PALAUNCH_BIN" install --cli
else
    "$PALAUNCH_BIN" install --cli --no-autostart
fi

echo ""
echo "Done. Open the launcher with:  $PALAUNCH_BIN pick"
