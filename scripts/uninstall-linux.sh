#!/usr/bin/env bash
# Removes the autostart entry, the menu entry and the package.
# Profiles and settings in the config directory are left alone.

set -euo pipefail

PALAUNCH_BIN="$(command -v palaunch || true)"
if [ -n "$PALAUNCH_BIN" ]; then
    "$PALAUNCH_BIN" install --cli --uninstall || true
fi

# Older versions registered a systemd user unit; remove it if it is still there.
SYSTEMD_UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/profile-auto-launcher.service"
if [ -f "$SYSTEMD_UNIT" ]; then
    systemctl --user disable --now profile-auto-launcher.service || true
    rm -v "$SYSTEMD_UNIT"
    systemctl --user daemon-reload || true
fi

python3 -m pip uninstall -y profile-auto-launcher || true

echo ""
echo "Uninstall complete. Profiles kept in your config directory."
