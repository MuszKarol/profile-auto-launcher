"""rsync-style directory mirroring, with or without rsync installed.

Two things need it: the `rsync:` step type, and pushing the profiles directory
to another machine. Where the real `rsync` binary exists it is used — nothing
built here matches it over SSH — and everywhere else (Windows, a stripped
container) the built-in walker does the same job for local paths: copy what
differs, leave what matches, optionally delete what the source no longer has.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from launcher.logging_setup import get_logger

log = get_logger("mirror")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# user@host:/path, host:/path, rsync://host/module — anything rsync itself
# would treat as a network target, and the built-in walker cannot.
_REMOTE = re.compile(r"^(rsync://|[^/\\:]+(@[^/\\:]+)?:)")

# A copy is skipped when size matches and mtimes agree to within this many
# seconds: FAT and SMB round timestamps, so exact comparison re-copies forever.
MTIME_SLACK = 2.0


class MirrorError(RuntimeError):
    """The mirror could not be completed."""


@dataclass
class MirrorReport:
    backend: str  # "rsync" | "builtin"
    copied: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0
    dry_run: bool = False

    def __str__(self) -> str:
        prefix = "would " if self.dry_run else ""
        return (
            f"{self.backend}: {prefix}copy {self.copied}, {prefix}update {self.updated}, "
            f"{prefix}delete {self.deleted}, unchanged {self.skipped}"
        )


def rsync_binary() -> str:
    """Path to the rsync executable, or an empty string when absent."""
    return shutil.which("rsync") or ""


def is_remote(target: str) -> bool:
    """True for targets only the real rsync can reach (host:path, rsync://)."""
    # A Windows drive letter is not a remote host, however much it looks like one.
    if re.match(r"^[A-Za-z]:[\\/]", target):
        return False
    return bool(_REMOTE.match(target))


def _excluded(relative: str, patterns: tuple[str, ...]) -> bool:
    name = relative.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(relative, p) or fnmatch.fnmatch(name, p) for p in patterns)


def _same_file(source: Path, target: Path) -> bool:
    try:
        src_stat, dst_stat = source.stat(), target.stat()
    except OSError:
        return False
    return (
        src_stat.st_size == dst_stat.st_size
        and abs(src_stat.st_mtime - dst_stat.st_mtime) <= MTIME_SLACK
    )


def _builtin_mirror(
    source: Path,
    target: Path,
    delete: bool,
    dry_run: bool,
    excludes: tuple[str, ...],
) -> MirrorReport:
    report = MirrorReport(backend="builtin", dry_run=dry_run)
    if not source.is_dir():
        raise MirrorError(f"source directory does not exist: {source}")
    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)

    wanted: set[str] = set()
    for root, dirnames, filenames in os.walk(source):
        base = Path(root).relative_to(source)
        # Pruning in place is what keeps os.walk out of an excluded subtree.
        dirnames[:] = [
            d for d in sorted(dirnames) if not _excluded(str(base / d).replace("\\", "/"), excludes)
        ]
        for name in dirnames:
            relative = str(base / name).replace("\\", "/")
            wanted.add(relative)
            if not dry_run:
                (target / relative).mkdir(parents=True, exist_ok=True)
        for name in sorted(filenames):
            relative = str(base / name).replace("\\", "/")
            if _excluded(relative, excludes):
                continue
            wanted.add(relative)
            src_file, dst_file = source / relative, target / relative
            if _same_file(src_file, dst_file):
                report.skipped += 1
                continue
            existed = dst_file.exists()
            if not dry_run:
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)
            if existed:
                report.updated += 1
            else:
                report.copied += 1

    if delete and target.is_dir():
        for root, dirnames, filenames in os.walk(target, topdown=False):
            base = Path(root).relative_to(target)
            for name in filenames + dirnames:
                relative = str(base / name).replace("\\", "/")
                if relative in wanted or _excluded(relative, excludes):
                    continue
                victim = target / relative
                report.deleted += 1
                if dry_run:
                    continue
                if victim.is_dir() and not victim.is_symlink():
                    shutil.rmtree(victim, ignore_errors=True)
                else:
                    victim.unlink(missing_ok=True)
    return report


def _rsync_argv(
    source: str, target: str, delete: bool, dry_run: bool, excludes: tuple[str, ...]
) -> list[str]:
    argv = [rsync_binary() or "rsync", "-a", "--itemize-changes"]
    if delete:
        argv.append("--delete")
    if dry_run:
        argv.append("--dry-run")
    for pattern in excludes:
        argv += ["--exclude", pattern]
    # The trailing slash is the difference between "copy the directory" and
    # "copy its contents"; every caller here means the contents.
    return [*argv, source.rstrip("/\\") + "/", target]


def _parse_itemized(output: str) -> tuple[int, int, int]:
    copied = updated = deleted = 0
    for line in output.splitlines():
        if line.startswith("*deleting"):
            deleted += 1
        elif len(line) > 1 and line[0] in "<>" and line[1] == "f":
            if line[2:11].startswith("+++++++"):
                copied += 1
            else:
                updated += 1
    return copied, updated, deleted


def _rsync_mirror(
    source: str, target: str, delete: bool, dry_run: bool, excludes: tuple[str, ...]
) -> MirrorReport:
    argv = _rsync_argv(source, target, delete, dry_run, excludes)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MirrorError(str(exc)) from exc
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}"
        raise MirrorError(f"rsync: {message}")
    copied, updated, deleted = _parse_itemized(proc.stdout)
    return MirrorReport(
        backend="rsync", copied=copied, updated=updated, deleted=deleted, dry_run=dry_run
    )


def mirror(
    source: str | Path,
    target: str | Path,
    delete: bool = False,
    dry_run: bool = False,
    exclude: tuple[str, ...] | list[str] = (),
    backend: str = "auto",
) -> MirrorReport:
    """Make `target` match `source`.

    `backend` is "auto" (rsync when installed), "rsync" or "builtin". A remote
    target always needs rsync, and says so rather than half-copying.
    """
    source_text, target_text = str(source), str(target)
    excludes = tuple(exclude)
    remote = is_remote(target_text) or is_remote(source_text)
    if backend not in ("auto", "rsync", "builtin"):
        raise MirrorError(f"unknown backend {backend!r}")
    if remote and backend == "builtin":
        raise MirrorError("a remote target needs rsync; the built-in mirror is local only")
    use_rsync = backend == "rsync" or (backend == "auto" and (remote or bool(rsync_binary())))
    if use_rsync and not rsync_binary():
        if remote:
            raise MirrorError(f"rsync is not installed, and {target_text} is a remote target")
        use_rsync = False
    if use_rsync:
        return _rsync_mirror(source_text, target_text, delete, dry_run, excludes)
    return _builtin_mirror(Path(source_text), Path(target_text), delete, dry_run, excludes)
