"""`palaunch record` — watch what you start, then write it out as a profile.

Writing the first profile by hand is the steepest part of adopting this tool.
Recording removes it: open the apps you normally open, and the launcher turns
the processes that appeared (and stayed) into `type: app` steps.

Only long-lived processes count. Anything that exits during the session was a
build script or a helper, not something worth relaunching.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from launcher import sysprobe
from launcher.config import PLATFORM
from launcher.logging_setup import get_logger

log = get_logger("record")

# Processes that show up whenever anything is launched and are never worth
# recording. Matched case-insensitively against the process name.
_NOISE = {
    "windows": {
        "conhost.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "svchost.exe",
        "runtimebroker.exe", "backgroundtaskhost.exe", "dllhost.exe",
        "applicationframehost.exe", "sihost.exe", "taskhostw.exe", "python.exe",
        "pythonw.exe", "wmiprvse.exe", "consent.exe", "searchprotocolhost.exe",
    },
    "linux": {
        "sh", "bash", "zsh", "dash", "python3", "python", "gpg-agent", "dbus-daemon",
        "xdg-desktop-portal", "gvfsd", "tracker-miner-fs", "at-spi2-registryd",
        "ibus-daemon", "pipewire", "wireplumber", "sleep", "ps", "which",
    },
    "darwin": {
        "sh", "bash", "zsh", "python3", "python", "mdworker_shared", "quicklookd",
        "distnoted", "cfprefsd", "com.apple.WebKit.WebContent", "sleep",
    },
}

# Multi-process apps (browsers, Electron) spawn dozens of helpers. Recording
# the main binary once is what the user meant.
_HELPER_MARKERS = (
    "--type=", "helper", "crashpad", "gpu-process", "utility", "renderer",
    "zygote", "sandbox",
)


@dataclass
class Candidate:
    name: str
    exe: str
    pid: int
    seen_at: float


def _is_noise(name: str, exe: str) -> bool:
    lowered = name.lower()
    if lowered in _NOISE.get(PLATFORM, set()):
        return True
    combined = f"{name} {exe}".lower()
    return any(marker in combined for marker in _HELPER_MARKERS)


def _snapshot() -> dict[int, tuple[str, str]]:
    return {pid: (name, exe) for pid, name, exe in sysprobe.processes()}


def observe(
    duration: float = 120.0,
    poll: float = 2.0,
    on_tick: Callable[[float, int], None] | None = None,
) -> list[Candidate]:
    """Watch for `duration` seconds and return the apps that appeared and stayed."""
    baseline = set(_snapshot())
    candidates: dict[str, Candidate] = {}
    deadline = time.time() + duration
    try:
        while time.time() < deadline:
            time.sleep(poll)
            for pid, (name, exe) in _snapshot().items():
                if pid in baseline or not name or _is_noise(name, exe):
                    continue
                key = (exe or name).lower()
                candidates.setdefault(
                    key, Candidate(name=name, exe=exe or name, pid=pid, seen_at=time.time())
                )
            if on_tick:
                on_tick(max(0.0, deadline - time.time()), len(candidates))
    except KeyboardInterrupt:
        log.info("recording interrupted early with %d candidates", len(candidates))

    # Anything that has already exited was a helper or a one-shot command.
    from launcher import procs

    survivors = [c for c in candidates.values() if procs.is_alive(c.pid)]
    survivors.sort(key=lambda c: c.seen_at)
    return survivors


def to_yaml(name: str, candidates: list[Candidate], description: str = "") -> str:
    """Render the observed apps as a ready-to-edit profile file."""
    import yaml

    steps = [
        {
            "type": "app",
            "name": candidate.name,
            "path": candidate.exe,
            "optional": True,
            "parallel": True,
        }
        for candidate in candidates
    ]
    document = {
        "name": name,
        "description": description or f"Recorded session ({len(steps)} apps)",
        "icon": "\U0001F4FC",
        "steps": steps,
    }
    header = (
        "# Recorded by `palaunch record`. Paths are whatever was running —\n"
        "# review them, drop what you don't want, and add url/env steps.\n"
    )
    body = yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)
    if not steps:
        body += "\n# Nothing was recorded — start the apps *after* the recording begins.\n"
    return header + body


def write_profile(name: str, candidates: list[Candidate], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_yaml(name, candidates), encoding="utf-8")
    return path
