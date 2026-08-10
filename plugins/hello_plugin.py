#!/usr/bin/env python3
"""Example `type: plugin` executable.

The launcher writes one JSON object to stdin::

    {"profile": "Evening", "step": "log the day",
     "config": {...}, "env": {...}}

and reads one JSON object back from stdout::

    {"ok": true, "detail": "shown in the HUD", "env": {"KEY": "value"}}

Everything in the response is optional: a plugin that just exits 0 counts as a
success, so an ordinary script works as a plugin without knowing the protocol.
Keys returned under "env" are merged into the run environment and are visible
to every later step.

Try it:

    mkdir -p ~/.config/profile-auto-launcher/plugins
    cp plugins/hello_plugin.py ~/.config/profile-auto-launcher/plugins/
    chmod +x ~/.config/profile-auto-launcher/plugins/hello_plugin.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


def main() -> int:
    try:
        request = json.load(sys.stdin)
    except (ValueError, OSError):
        request = {}

    config = request.get("config") or {}
    message = str(config.get("message", "no message"))
    log_file = Path(config.get("log_file", Path.home() / "palaunch-plugin.log")).expanduser()

    try:
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(
                f"{datetime.now().isoformat(timespec='seconds')} "
                f"{request.get('profile', '?')}/{request.get('step', '?')}: {message}\n"
            )
    except OSError as exc:
        json.dump({"ok": False, "detail": f"could not write {log_file}: {exc}"}, sys.stdout)
        return 1

    json.dump(
        {
            "ok": True,
            "detail": f"logged to {log_file}",
            "env": {"PAL_LAST_PLUGIN_RUN": datetime.now().isoformat(timespec="seconds")},
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
