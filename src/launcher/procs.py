"""Tracking of processes a profile started, so it can be torn down again.

Every tracked spawn is appended to `<config dir>/active.json`. `palaunch
status` reports what is still alive; `palaunch stop` terminates it.

PIDs get recycled, so each record also stores the launch timestamp and the
command line. Liveness is checked with the cheapest reliable primitive per
platform — a signal-0 probe on POSIX, `OpenProcess` + wait on Windows.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from launcher.config import PLATFORM, config_dir

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def active_path():
    return config_dir() / "active.json"


@dataclass
class TrackedProc:
    pid: int
    label: str = ""
    cmd: str = ""
    started_at: float = field(default_factory=time.time)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrackedProc:
        return cls(
            pid=int(data.get("pid", 0)),
            label=str(data.get("label", "")),
            cmd=str(data.get("cmd", "")),
            started_at=float(data.get("started_at", 0.0)),
        )


# ── liveness ─────────────────────────────────────────────────────────────


def is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if PLATFORM == "windows":
        return _is_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    # The tray spawns children and never waits on them, so a terminated child
    # lingers as a zombie that signal-0 still reports as alive. Nothing is
    # running behind that pid, so it must count as dead.
    return not _is_zombie(pid)


def _is_zombie(pid: int) -> bool:
    if PLATFORM == "linux":
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return False
        # "pid (comm) state …" — comm may itself contain spaces or brackets,
        # so the state is the first field after the closing parenthesis.
        _, _, rest = stat.partition(")")
        fields = rest.split()
        return bool(fields) and fields[0] == "Z"
    if PLATFORM == "darwin":
        try:
            proc = subprocess.run(
                ["ps", "-o", "state=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return proc.stdout.strip().startswith("Z")
    return False


def _is_alive_windows(pid: int) -> bool:  # pragma: no cover - Windows only
    import ctypes

    SYNCHRONIZE = 0x00100000
    WAIT_TIMEOUT = 0x00000102
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def terminate(pid: int, grace: float = 5.0) -> bool:
    """Ask politely, then insist. True when the process is gone afterwards."""
    if not is_alive(pid):
        return True
    try:
        if PLATFORM == "windows":
            subprocess.run(
                ["taskkill", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
                creationflags=_NO_WINDOW,
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        return not is_alive(pid)

    deadline = time.time() + grace
    while time.time() < deadline:
        if not is_alive(pid):
            return True
        time.sleep(0.2)

    try:
        if PLATFORM == "windows":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
                creationflags=_NO_WINDOW,
            )
        else:
            os.kill(pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    time.sleep(0.2)
    return not is_alive(pid)


# ── registry ─────────────────────────────────────────────────────────────


def _load() -> dict[str, list[dict[str, Any]]]:
    try:
        data = json.loads(active_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _store(data: dict[str, list[dict[str, Any]]]) -> None:
    path = active_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass  # tracking is best-effort; never fail a run over it


def record(profile_name: str, pid: int, label: str, cmd: str) -> None:
    data = _load()
    entries = data.setdefault(profile_name, [])
    entries.append(asdict(TrackedProc(pid=pid, label=label, cmd=cmd)))
    _store(data)


def tracked(profile_name: str) -> list[TrackedProc]:
    return [TrackedProc.from_dict(e) for e in _load().get(profile_name, [])]


def alive(profile_name: str) -> list[TrackedProc]:
    return [p for p in tracked(profile_name) if is_alive(p.pid)]


def prune(profile_name: str | None = None) -> None:
    """Drop dead pids (and profiles left with none) from the registry."""
    data = _load()
    names = [profile_name] if profile_name else list(data)
    for name in names:
        surviving = [e for e in data.get(name, []) if is_alive(int(e.get("pid", 0)))]
        if surviving:
            data[name] = surviving
        else:
            data.pop(name, None)
    _store(data)


def clear(profile_name: str) -> None:
    data = _load()
    data.pop(profile_name, None)
    _store(data)


def active_profiles() -> dict[str, list[TrackedProc]]:
    """Profile name -> still-running tracked processes."""
    result = {}
    for name in _load():
        living = alive(name)
        if living:
            result[name] = living
    return result


def stop_profile(profile_name: str, grace: float = 5.0) -> tuple[int, int]:
    """Terminate every tracked process. Returns (stopped, still running)."""
    living = alive(profile_name)
    stopped = sum(1 for proc in living if terminate(proc.pid, grace))
    clear(profile_name)
    return stopped, len(living) - stopped
