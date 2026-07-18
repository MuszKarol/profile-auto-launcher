#!/usr/bin/env bash
# Installs profile-auto-launcher for the current user and registers it
# to autostart at login via an XDG .desktop entry.
#
# Usage:
#   scripts/install-linux.sh              # install + enable autostart
#   scripts/install-linux.sh --no-autostart
#   scripts/install-linux.sh --systemd    # use a systemd user unit instead

set -euo pipefail

MODE="desktop"        # desktop | systemd
ENABLE_AUTOSTART=1

for arg in "$@"; do
    case "$arg" in
        --no-autostart) ENABLE_AUTOSTART=0 ;;
        --systemd)      MODE="systemd" ;;
        --desktop)      MODE="desktop" ;;
        -h|--help)
            cat <<'USAGE'
Installs profile-auto-launcher for the current user and enables autostart.

Usage:
  scripts/install-linux.sh                 install + enable XDG autostart
  scripts/install-linux.sh --no-autostart  install package only
  scripts/install-linux.sh --systemd       use a systemd user unit instead
  scripts/install-linux.sh --desktop       use .desktop entry (default)
USAGE
            exit 0 ;;
        *)
            echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
AUTOSTART_DIR="$XDG_CONFIG_HOME/autostart"
SYSTEMD_DIR="$XDG_CONFIG_HOME/systemd/user"
PROFILES_DIR="$XDG_CONFIG_HOME/profile-auto-launcher/profiles"
DESKTOP_FILE="$AUTOSTART_DIR/profile-auto-launcher.desktop"
SYSTEMD_UNIT="$SYSTEMD_DIR/profile-auto-launcher.service"

echo "==> Installing profile-auto-launcher from $REPO_DIR"

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found. Install Python 3.10+ first." >&2
    exit 1
fi

PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_OK="$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 10) else 0)')"
if [ "$PY_OK" != "1" ]; then
    echo "ERROR: Python $PY_VER is too old; need 3.10+." >&2
    exit 1
fi

echo "==> Using Python $PY_VER"

if ! python3 -m pip --version >/dev/null 2>&1; then
    echo "==> pip not found — installing via ensurepip..."
    python3 -m ensurepip --upgrade || {
        echo "ERROR: ensurepip failed. Install pip manually: https://pip.pypa.io/en/stable/installation/" >&2
        exit 1
    }
fi

python3 -m pip install --user --upgrade "$REPO_DIR"'[full]'

PALAUNCH_BIN="$(command -v palaunch || true)"
if [ -z "$PALAUNCH_BIN" ]; then
    USER_BASE="$(python3 -c 'import site; print(site.USER_BASE)')"
    PALAUNCH_BIN="$USER_BASE/bin/palaunch"
fi

if [ ! -x "$PALAUNCH_BIN" ]; then
    echo "ERROR: palaunch not found at $PALAUNCH_BIN." >&2
    echo "       Add \$HOME/.local/bin to your PATH and re-run." >&2
    exit 1
fi
echo "==> palaunch binary: $PALAUNCH_BIN"

mkdir -p "$PROFILES_DIR"
for f in "$REPO_DIR"/profiles/*.yaml; do
    [ -e "$f" ] || continue
    dest="$PROFILES_DIR/$(basename "$f")"
    if [ ! -f "$dest" ]; then
        cp "$f" "$dest"
        echo "    installed profile: $(basename "$f")"
    fi
done
echo "==> Profiles dir:    $PROFILES_DIR"

if [ "$ENABLE_AUTOSTART" = "0" ]; then
    echo "==> Autostart skipped (--no-autostart)."
    echo "Done. Start manually with:  $PALAUNCH_BIN tray"
    exit 0
fi

if [ "$MODE" = "systemd" ]; then
    mkdir -p "$SYSTEMD_DIR"
    cat > "$SYSTEMD_UNIT" <<EOF
[Unit]
Description=Profile Auto Launcher (context-based workflow launcher)
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=$PALAUNCH_BIN tray
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now profile-auto-launcher.service
    echo "==> systemd user unit: $SYSTEMD_UNIT (enabled + started)"
else
    mkdir -p "$AUTOSTART_DIR"
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Profile Auto Launcher
Comment=Context-based workflow automation launcher
Exec=$PALAUNCH_BIN tray
Icon=system-run
Terminal=false
X-GNOME-Autostart-enabled=true
Categories=Utility;
EOF
    echo "==> Autostart entry: $DESKTOP_FILE"
fi

echo ""
echo "Installation complete. Launcher will start at your next login."
echo "To start it now, run:  $PALAUNCH_BIN tray &"
