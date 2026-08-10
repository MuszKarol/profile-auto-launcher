"""Read-only probes of the machine: running processes, Wi-Fi, power source.

`psutil` is used when installed (fast, uniform); otherwise each probe shells
out to a platform tool. Every probe returns a "don't know" value rather than
raising, so a condition that cannot be evaluated fails closed at the caller.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from launcher.config import PLATFORM

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(argv: list[str], timeout: float = 5.0) -> str | None:
    if not shutil.which(argv[0]) and not Path(argv[0]).is_file():
        return None
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
            check=False, creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _psutil():
    try:
        import psutil

        return psutil
    except ImportError:
        return None


# ── processes ────────────────────────────────────────────────────────────


def processes() -> list[tuple[int, str, str]]:
    """(pid, name, exe path) for every visible process. Empty when unknown."""
    psutil = _psutil()
    if psutil is not None:
        found = []
        for proc in psutil.process_iter(["pid", "name", "exe"]):
            try:
                info = proc.info
                found.append((int(info["pid"]), info["name"] or "", info.get("exe") or ""))
            except Exception:
                continue
        return found

    if PLATFORM == "windows":
        out = _run(["tasklist", "/FO", "CSV", "/NH"])
        if not out:
            return []
        found = []
        for line in out.splitlines():
            parts = [p.strip('" ') for p in line.split('","')]
            if len(parts) >= 2 and parts[1].isdigit():
                found.append((int(parts[1]), parts[0], ""))
        return found

    out = _run(["ps", "-eo", "pid=,comm="])
    if not out:
        return []
    found = []
    for line in out.splitlines():
        pid, _, comm = line.strip().partition(" ")
        if not pid.isdigit():
            continue
        name = comm.strip()
        found.append((int(pid), Path(name).name, _exe_path(int(pid), name)))
    return found


def _exe_path(pid: int, fallback: str) -> str:
    """`ps` reports a bare command name; /proc has the real binary path.

    A recorded profile is only reusable if it points at an actual executable,
    so this is worth the extra readlink per process on Linux.
    """
    if PLATFORM == "linux":
        try:
            import os

            return os.readlink(f"/proc/{pid}/exe")
        except OSError:
            pass
    return fallback if "/" in fallback else ""


def process_names() -> set[str]:
    """Lower-cased process names currently running."""
    return {name.lower() for _pid, name, _exe in processes() if name}


def is_running(name: str) -> bool:
    """Match on the exact process name, with and without a `.exe` suffix."""
    needle = name.lower()
    names = process_names()
    return needle in names or f"{needle}.exe" in names or needle.removesuffix(".exe") in names


# ── network ──────────────────────────────────────────────────────────────


def wifi_ssid() -> str | None:
    """Current Wi-Fi network name, or None when unknown / not connected."""
    if PLATFORM == "windows":
        out = _run(["netsh", "wlan", "show", "interfaces"])
        if out:
            for line in out.splitlines():
                key, _, value = line.partition(":")
                key = key.strip().lower()
                # "SSID" but not "BSSID"; localized Windows keeps the acronym
                if key == "ssid" and value.strip():
                    return value.strip()
        return None

    if PLATFORM == "darwin":
        airport = (
            "/System/Library/PrivateFrameworks/Apple80211.framework"
            "/Versions/Current/Resources/airport"
        )
        out = _run([airport, "-I"])
        if out:
            for line in out.splitlines():
                key, _, value = line.partition(":")
                if key.strip() == "SSID":
                    return value.strip()
        out = _run(["networksetup", "-getairportnetwork", "en0"])
        if out and ":" in out:
            return out.split(":", 1)[1].strip() or None
        return None

    out = _run(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"])
    if out:
        for line in out.splitlines():
            active, _, ssid = line.partition(":")
            if active == "yes" and ssid:
                return ssid
    out = _run(["iwgetid", "-r"])
    if out and out.strip():
        return out.strip()
    return None


# ── power ────────────────────────────────────────────────────────────────


def on_battery() -> bool | None:
    """True on battery, False on AC, None when it cannot be determined."""
    psutil = _psutil()
    if psutil is not None:
        try:
            battery = psutil.sensors_battery()
        except Exception:
            battery = None
        if battery is not None:
            return not battery.power_plugged

    if PLATFORM == "linux":
        for supply in sorted(Path("/sys/class/power_supply").glob("A*")):
            online = supply / "online"
            if online.is_file():
                try:
                    return online.read_text().strip() == "0"
                except OSError:
                    continue
        return None

    if PLATFORM == "darwin":
        out = _run(["pmset", "-g", "batt"])
        if out:
            if "AC Power" in out:
                return False
            if "Battery Power" in out:
                return True
        return None

    if PLATFORM == "windows":
        out = _run([
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "(Get-CimInstance Win32_Battery).BatteryStatus",
        ])
        if out and out.strip():
            # BatteryStatus 2 == "AC connected"; 1 == discharging
            first = out.strip().splitlines()[0].strip()
            if first.isdigit():
                return int(first) == 1
        return None
    return None
