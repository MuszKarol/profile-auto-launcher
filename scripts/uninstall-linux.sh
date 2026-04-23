#!/usr/bin/env bash
# Removes autostart entries and uninstalls the package.
# Leaves user profiles in ~/.config/profile-auto-launcher/ untouched.

set -euo pipefail

XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
DESKTOP_FILE="$XDG_CONFIG_HOME/autostart/profile-auto-launcher.desktop"
SYSTEMD_UNIT="$XDG_CONFIG_HOME/systemd/user/profile-auto-launcher.service"

if systemctl --user is-enabled profile-auto-launcher.service >/dev/null 2>&1; then
    systemctl --user disable --now profile-auto-launcher.service || true
fi
if [ -f "$SYSTEMD_UNIT" ]; then
    rm -v "$SYSTEMD_UNIT"
    systemctl --user daemon-reload || true
fi
if [ -f "$DESKTOP_FILE" ]; then
    rm -v "$DESKTOP_FILE"
fi

python3 -m pip uninstall -y profile-auto-launcher || true

echo ""
echo "Uninstall complete. Profiles kept at $XDG_CONFIG_HOME/profile-auto-launcher/."
