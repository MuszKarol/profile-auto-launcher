"""Git-backed sync of the profiles directory.

Profiles are plain YAML, so the simplest cross-machine story is a git repo in
the config directory. `palaunch sync` commits local edits, rebases on the
remote and pushes — the same three commands you would type, with the error
messages surfaced instead of swallowed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from launcher.config import profiles_dir
from launcher.logging_setup import get_logger

log = get_logger("sync")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SyncError(RuntimeError):
    """Git is missing, or a git command failed."""


def _git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    if not shutil.which("git"):
        raise SyncError("git is not installed")
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd or profiles_dir()),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SyncError(str(exc)) from exc
    if check and proc.returncode != 0:
        message = (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}"
        raise SyncError(f"git {' '.join(args)}: {message}")
    return proc.stdout.strip()


def is_repo() -> bool:
    directory = profiles_dir()
    if not directory.is_dir():
        return False
    try:
        return _git("rev-parse", "--is-inside-work-tree", check=False).strip() == "true"
    except SyncError:
        return False


def init(remote: str = "") -> str:
    """Create the repo (idempotent) and point it at `remote` if given."""
    directory = profiles_dir()
    directory.mkdir(parents=True, exist_ok=True)
    if not is_repo():
        _git("init", "-b", "main")
        gitignore = directory / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*.log\n", encoding="utf-8")
    if remote:
        existing = _git("remote", check=False)
        if "origin" in existing.split():
            _git("remote", "set-url", "origin", remote)
        else:
            _git("remote", "add", "origin", remote)
    return str(directory)


def status() -> str:
    if not is_repo():
        return "not a git repository — run `palaunch sync --init <remote-url>`"
    dirty = _git("status", "--short")
    remote = _git("remote", "get-url", "origin", check=False) or "(no remote)"
    branch = _git("rev-parse", "--abbrev-ref", "HEAD", check=False) or "(no commits)"
    changes = dirty or "clean"
    return f"remote: {remote}\nbranch: {branch}\nchanges:\n{changes}"


def sync(message: str = "", push: bool = True) -> str:
    """Commit local changes, rebase onto the remote, push. Returns a summary."""
    if not is_repo():
        raise SyncError("profiles directory is not a git repo — run with --init first")

    steps: list[str] = []
    if _git("status", "--porcelain"):
        _git("add", "-A")
        from datetime import datetime

        default = f"palaunch sync {datetime.now().isoformat(timespec='minutes')}"
        _git("commit", "-m", message or default)
        steps.append("committed local changes")
    else:
        steps.append("nothing to commit")

    has_remote = "origin" in _git("remote", check=False).split()
    if not has_remote:
        steps.append("no remote configured — local commit only")
        return "; ".join(steps)

    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    _git("fetch", "origin", branch, check=False)
    # Rebase keeps a linear history across machines; a merge commit per sync
    # would bury the actual profile edits.
    rebase = _git("rebase", f"origin/{branch}", check=False)
    steps.append(
        "rebased on origin" if "up to date" not in rebase.lower() else "already up to date"
    )

    if push:
        _git("push", "-u", "origin", branch)
        steps.append("pushed")
    return "; ".join(steps)
