"""The installed-application index behind "type a name, launch that app".

A profile is the right tool for a whole context; opening one program is not
worth writing YAML for. This module finds what is installed — .desktop entries
on Linux, Start Menu shortcuts on Windows, app bundles on macOS, executables
on `PATH` everywhere — and launches one by name from the HUD, the manager
window or `palaunch app`.

The index is built once per process (and refreshed on demand), because
scanning a few thousand directory entries is cheap but not free.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from launcher import fuzzy
from launcher.config import PLATFORM
from launcher.logging_setup import get_logger

log = get_logger("apps")

# Everything launched ad hoc is tracked under one name, so `palaunch status`
# lists it and `palaunch stop "Quick launch"` closes it again.
LAUNCH_GROUP = "Quick launch"

CACHE_SECONDS = 300.0

# `Exec=` field codes: %f %F %u %U %i %c %k %d %D %n %N %v %m
_FIELD_CODE = re.compile(r"(?<!%)%[a-zA-Z]")


@dataclass(frozen=True)
class App:
    """One launchable program, however the platform describes it."""

    name: str
    argv: tuple[str, ...]
    source: str  # desktop | shortcut | bundle | path
    comment: str = ""
    terminal: bool = False
    keywords: tuple[str, ...] = field(default_factory=tuple)

    @property
    def target(self) -> str:
        return " ".join(self.argv)

    @property
    def searchable(self) -> tuple[str, ...]:
        return (self.name, self.comment, Path(self.argv[0]).name, *self.keywords)


# ── platform scanners ────────────────────────────────────────────────────


def _xdg_dirs() -> list[Path]:
    home = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    raw = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    roots = [home, *(Path(part) for part in raw.split(os.pathsep) if part)]
    return [root / "applications" for root in roots]


def parse_desktop_entry(text: str) -> dict[str, str]:
    """The `[Desktop Entry]` group as a flat mapping — later groups ignored.

    Localised keys (`Name[pl]`) are skipped so a translation never shadows the
    plain key, whichever order they appear in.
    """
    values: dict[str, str] = {}
    in_entry = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            in_entry = line.lower() == "[desktop entry]"
            continue
        if not in_entry or not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.endswith("]"):
            continue
        values.setdefault(key, value.strip())
    return values


def _desktop_argv(exec_line: str) -> list[str]:
    cleaned = _FIELD_CODE.sub("", exec_line).replace("%%", "%").strip()
    try:
        return shlex.split(cleaned)
    except ValueError:
        return cleaned.split()


def _scan_desktop_entries() -> list[App]:
    found: dict[str, App] = {}
    for directory in _xdg_dirs():
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.desktop")):
            try:
                entry = parse_desktop_entry(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if entry.get("Type", "Application") != "Application":
                continue
            if entry.get("NoDisplay", "").lower() == "true":
                continue
            if entry.get("Hidden", "").lower() == "true":
                continue
            argv = _desktop_argv(entry.get("Exec", ""))
            name = entry.get("Name") or path.stem
            if not argv or name.lower() in found:
                continue
            found[name.lower()] = App(
                name=name,
                argv=tuple(argv),
                source="desktop",
                comment=entry.get("Comment", ""),
                terminal=entry.get("Terminal", "").lower() == "true",
                keywords=tuple(k for k in entry.get("Keywords", "").split(";") if k),
            )
    return list(found.values())


def _scan_bundles() -> list[App]:
    roots = [
        Path("/Applications"),
        Path("/System/Applications"),
        Path.home() / "Applications",
    ]
    found: dict[str, App] = {}
    for root in roots:
        if not root.is_dir():
            continue
        candidates = list(root.glob("*.app")) + list(root.glob("*/*.app"))
        for bundle in sorted(candidates):
            name = bundle.stem
            found.setdefault(
                name.lower(),
                App(name=name, argv=("open", "-a", str(bundle)), source="bundle"),
            )
    return list(found.values())


def _start_menu_dirs() -> list[Path]:
    parts = [
        os.environ.get("APPDATA", ""),
        os.environ.get("PROGRAMDATA", ""),
    ]
    return [
        Path(part) / "Microsoft" / "Windows" / "Start Menu" / "Programs" for part in parts if part
    ]


def _scan_shortcuts() -> list[App]:
    found: dict[str, App] = {}
    for directory in _start_menu_dirs():
        if not directory.is_dir():
            continue
        for pattern in ("*.lnk", "*.url"):
            for path in sorted(directory.rglob(pattern)):
                name = path.stem
                # `cmd /c start` resolves a shortcut the way Explorer does,
                # which is the only way to honour its arguments and workdir.
                found.setdefault(
                    name.lower(),
                    App(
                        name=name,
                        argv=("cmd", "/c", "start", "", str(path)),
                        source="shortcut",
                        comment=str(path.parent.name),
                    ),
                )
    return list(found.values())


def _scan_path() -> list[App]:
    found: dict[str, App] = {}
    seen_dirs: set[str] = set()
    for part in (os.environ.get("PATH") or "").split(os.pathsep):
        if not part or part in seen_dirs:
            continue
        seen_dirs.add(part)
        directory = Path(part)
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if PLATFORM == "windows":
                stem, ext = os.path.splitext(name)
                if ext.lower() not in (".exe", ".bat", ".cmd"):
                    continue
                name = stem
            else:
                try:
                    if not entry.is_file() or not os.access(entry.path, os.X_OK):
                        continue
                except OSError:
                    continue
            found.setdefault(
                name.lower(),
                App(name=name, argv=(entry.path,), source="path", comment=part),
            )
    return list(found.values())


def _scanners():
    if PLATFORM == "windows":
        return (_scan_shortcuts, _scan_path)
    if PLATFORM == "darwin":
        return (_scan_bundles, _scan_path)
    return (_scan_desktop_entries, _scan_path)


# ── index ────────────────────────────────────────────────────────────────

_cache: list[App] = []
_cached_at = 0.0

# A named app wins over a bare executable of the same name: "Firefox" from a
# .desktop entry carries the arguments and working directory the OS uses.
_SOURCE_BONUS = {"desktop": 12, "shortcut": 12, "bundle": 12, "path": 0}


def index(refresh: bool = False) -> list[App]:
    """Every launchable app this machine advertises, newest scan cached."""
    global _cache, _cached_at
    if not refresh and _cache and (time.monotonic() - _cached_at) < CACHE_SECONDS:
        return _cache
    apps: dict[str, App] = {}
    for scanner in _scanners():
        try:
            for app in scanner():
                apps.setdefault(app.name.lower(), app)
        except Exception:  # a broken entry must not empty the whole index
            log.exception("app scanner %s failed", scanner.__name__)
    _cache = sorted(apps.values(), key=lambda a: a.name.lower())
    _cached_at = time.monotonic()
    log.debug("indexed %d applications", len(_cache))
    return _cache


def search(query: str, limit: int = 8, refresh: bool = False) -> list[App]:
    """Apps matching `query`, best first. An empty query matches nothing."""
    query = query.strip()
    if not query:
        return []
    scored = []
    for app in index(refresh=refresh):
        rank = fuzzy.best(query, app.searchable)
        if rank is None:
            continue
        scored.append((rank + _SOURCE_BONUS.get(app.source, 0), -len(app.name), app))
    scored.sort(key=lambda row: (-row[0], -row[1], row[2].name.lower()))
    return [app for _rank, _length, app in scored[:limit]]


def find(name: str) -> App | None:
    """The single best match for `name`, or None."""
    matches = search(name, limit=1)
    return matches[0] if matches else None


def launch(app: App | str, args: list[str] | None = None, track: bool = True) -> str:
    """Start one app. Returns a one-line description of what happened."""
    resolved = find(app) if isinstance(app, str) else app
    if resolved is None:
        raise LookupError(f"no installed application matches {app!r}")
    from launcher.executor import spawn_detached

    argv = [*resolved.argv, *(args or [])]
    proc = spawn_detached(argv)
    if track:
        from launcher import procs

        procs.record(LAUNCH_GROUP, proc.pid, resolved.name, " ".join(argv)[:300])
    log.info("launched %s (pid %s)", resolved.name, proc.pid)
    return f"launched {resolved.name} (pid {proc.pid})"


def describe(app: App) -> str:
    """The one-line summary the UIs show under an app's name."""
    if app.comment:
        return app.comment
    return app.target


def running() -> list[tuple[int, str]]:
    """(pid, label) for the apps launched this way that are still alive."""
    from launcher import procs

    return [(proc.pid, proc.label) for proc in procs.alive(LAUNCH_GROUP)]


def stop_all() -> tuple[int, int]:
    """Close every ad-hoc launch. Returns (closed, survived)."""
    from launcher import procs

    return procs.stop_profile(LAUNCH_GROUP)


def _which(command: str) -> str:
    """`shutil.which`, kept here so callers do not import shutil for one line."""
    import shutil

    return shutil.which(command) or ""


def resolve_command(command: str) -> list[str]:
    """Turn typed text into an argv, honouring quotes and a bare app name."""
    try:
        argv = shlex.split(command, posix=(PLATFORM != "windows"))
    except ValueError:
        argv = command.split()
    if not argv:
        return []
    if _which(argv[0]) or Path(argv[0]).is_file():
        return argv
    app = find(argv[0])
    return [*app.argv, *argv[1:]] if app else argv


def open_path(target: str) -> str:
    """Hand a file, folder or URL to the desktop's default handler."""
    if PLATFORM == "windows":
        os.startfile(target)  # type: ignore[attr-defined]
    elif PLATFORM == "darwin":
        subprocess.run(["open", target], check=False)
    else:
        subprocess.run(["xdg-open", target], check=False)
    return f"opened {target}"
