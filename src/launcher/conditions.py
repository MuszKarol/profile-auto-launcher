"""`when:` condition evaluation.

All keys in a `when` mapping must hold (AND). Unknown keys are rejected when
the profile loads, so anything reaching this module is a key we understand.
A probe that cannot answer (no Wi-Fi tooling, no battery) makes its condition
false — a step guarded by a condition we cannot verify should not run.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

from launcher.config import PLATFORM

KNOWN_WHEN_KEYS = frozenset(
    {
        "platform",
        "exists",
        "not_exists",
        "env",
        "weekday",
        "time_between",
        "process_running",
        "process_not_running",
        "wifi_ssid",
        "on_battery",
        "hostname",
        "command",
    }
)

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _parse_time(value: Any) -> dtime | None:
    text = str(value).strip()
    for fmt in ("%H:%M", "%H:%M:%S", "%H"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def _in_time_window(spec: Any, now: dtime) -> bool:
    """`time_between: ["22:00", "06:00"]` — windows may wrap past midnight."""
    parts = _as_list(spec)
    if len(parts) != 2:
        return False
    start, end = _parse_time(parts[0]), _parse_time(parts[1])
    if start is None or end is None:
        return False
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end


def _probe_command(spec: Any, env: dict[str, str], expand) -> bool:
    argv = [str(a) for a in _as_list(spec)]
    if len(argv) == 1:
        import shlex

        argv = shlex.split(argv[0], posix=(PLATFORM != "windows"))
    argv = [expand(a, env) or a for a in argv]
    if not argv:
        return False
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            timeout=10,
            check=False,
            env=env,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def matches(
    when: dict[str, Any], env: dict[str, str], expand=None, now: datetime | None = None
) -> bool:
    """Evaluate a `when` mapping. `expand` resolves `$VAR` inside path values."""
    if expand is None:

        def expand(value, _env):  # noqa: ANN001 - local shim
            return os.path.expanduser(str(value))

    moment = now or datetime.now()

    for key, expected in when.items():
        if key == "platform":
            if PLATFORM not in [str(v).lower() for v in _as_list(expected)]:
                return False
        elif key == "exists":
            if not all(Path(expand(str(p), env) or "").exists() for p in _as_list(expected)):
                return False
        elif key == "not_exists":
            if any(Path(expand(str(p), env) or "").exists() for p in _as_list(expected)):
                return False
        elif key == "env":
            for k, v in dict(expected).items():
                if env.get(str(k)) != str(v):
                    return False
        elif key == "weekday":
            today = _WEEKDAYS[moment.weekday()]
            if today not in [str(v)[:3].lower() for v in _as_list(expected)]:
                return False
        elif key == "time_between":
            if not _in_time_window(expected, moment.time()):
                return False
        elif key == "hostname":
            import socket

            host = socket.gethostname().lower()
            if host not in [str(v).lower() for v in _as_list(expected)]:
                return False
        elif key == "process_running":
            from launcher import sysprobe

            if not all(sysprobe.is_running(str(p)) for p in _as_list(expected)):
                return False
        elif key == "process_not_running":
            from launcher import sysprobe

            if any(sysprobe.is_running(str(p)) for p in _as_list(expected)):
                return False
        elif key == "wifi_ssid":
            from launcher import sysprobe

            ssid = sysprobe.wifi_ssid()
            if ssid is None or ssid not in [str(v) for v in _as_list(expected)]:
                return False
        elif key == "on_battery":
            from launcher import sysprobe

            state = sysprobe.on_battery()
            if state is None or state is not bool(expected):
                return False
        elif key == "command" and not _probe_command(expected, env, expand):
            return False
    return True
