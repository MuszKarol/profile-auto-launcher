"""Turning "I want a profile for X" into a valid profile file.

The old path to a new profile was a name prompt and an empty YAML file, which
left the interesting part — what the profile should actually do — entirely to
the user. This module builds the whole thing from a few answers: which apps to
open, which pages, what to close first. It has no Tk in it, so the wizard, the
CLI and the tests all go through the same code.

Everything it produces is validated with the real loader before it is written,
so a scaffolded profile can never be one `palaunch validate` rejects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from launcher.config import _coerce_step, profiles_dir

# The two-window layout a "set up my desk" profile almost always wants.
TILE_POSITIONS = ("left-half", "right-half")


@dataclass(frozen=True)
class Preset:
    """A starting point offered by the new-profile wizard."""

    key: str
    title: str
    icon: str
    description: str
    tags: tuple[str, ...] = ()
    close: tuple[str, ...] = ()


PRESETS: tuple[Preset, ...] = (
    Preset("blank", "Blank", "▣", "Start from nothing"),
    Preset(
        "dev",
        "Development",
        "🛠",
        "Editor, terminal and the services a project needs",
        tags=("code", "work"),
    ),
    Preset(
        "work",
        "Work",
        "💼",
        "Mail, calendar, chat and the documents of the day",
        tags=("office", "work"),
    ),
    Preset(
        "focus",
        "Focus",
        "🎯",
        "Close the distractions, open only what the task needs",
        tags=("focus",),
        close=("slack", "discord", "telegram"),
    ),
    Preset(
        "gaming",
        "Gaming",
        "🎮",
        "Launcher up, background noise down",
        tags=("play",),
        close=("teams", "outlook"),
    ),
)


def preset(key: str) -> Preset:
    return next((p for p in PRESETS if p.key == key), PRESETS[0])


@dataclass
class Draft:
    """The answers a new profile is built from."""

    name: str
    description: str = ""
    icon: str = ""
    tags: list[str] = field(default_factory=list)
    hotkey: str = ""
    apps: list[str] = field(default_factory=list)  # app names or executable paths
    urls: list[str] = field(default_factory=list)
    close: list[str] = field(default_factory=list)  # processes to end first
    tile: bool = True  # place the first two apps side by side
    default: bool = False
    notify_on_stop: bool = False


def slugify(name: str) -> str:
    """A filename stem: lowercase, spaces and punctuation folded to dashes."""
    cleaned = "".join(char if char.isalnum() else "-" for char in name.strip().lower())
    slug = "-".join(part for part in cleaned.split("-") if part)
    return slug or "profile"


def profile_path(name: str, directory: Path | None = None) -> Path:
    return (directory or profiles_dir()) / f"{slugify(name)}.yaml"


def _resolve_app(reference: str) -> tuple[str, list[str]]:
    """(path, args) for an app name, an executable path, or a command line."""
    from launcher import apps

    reference = reference.strip()
    if not reference:
        return "", []
    if Path(reference).is_file():
        return reference, []
    match = apps.find(reference)
    if match is None:
        return reference, []
    return match.argv[0], list(match.argv[1:])


def build(draft: Draft) -> dict[str, Any]:
    """The profile mapping for a draft — validated, ready to be written."""
    if not draft.name.strip():
        raise ValueError("a profile needs a name")

    steps: list[dict[str, Any]] = []
    for process in [p.strip() for p in draft.close if p.strip()]:
        steps.append({"type": "kill", "name": f"close {process}", "process": process})

    placed = 0
    for reference in draft.apps:
        path, args = _resolve_app(reference)
        if not path:
            continue
        step: dict[str, Any] = {
            "type": "app",
            "name": Path(reference).stem or reference,
            "path": path,
        }
        if args:
            step["args"] = args
        if draft.tile and placed < len(TILE_POSITIONS):
            step["window"] = {"monitor": 1, "position": TILE_POSITIONS[placed]}
        placed += 1
        steps.append(step)

    for url in [u.strip() for u in draft.urls if u.strip()]:
        steps.append({"type": "url", "name": url[:40], "url": url})

    profile: dict[str, Any] = {"name": draft.name.strip()}
    if draft.description.strip():
        profile["description"] = draft.description.strip()
    if draft.icon.strip():
        profile["icon"] = draft.icon.strip()
    tags = [tag.strip() for tag in draft.tags if tag.strip()]
    if tags:
        profile["tags"] = tags
    if draft.hotkey.strip():
        profile["hotkey"] = draft.hotkey.strip()
    if draft.default:
        profile["default"] = True
    profile["steps"] = steps
    if draft.notify_on_stop:
        profile["teardown"] = [
            {"type": "notify", "name": "closed", "message": f"{draft.name.strip()} closed"}
        ]

    for section in ("steps", "teardown"):
        for index, raw in enumerate(profile.get(section) or []):
            try:
                _coerce_step(raw)
            except ValueError as exc:
                raise ValueError(f"{section}[{index + 1}]: {exc}") from exc
    return profile


def to_yaml(profile: dict[str, Any], modeline: str = "") -> str:
    body = yaml.safe_dump(profile, sort_keys=False, allow_unicode=True, width=100)
    return f"{modeline}\n{body}" if modeline else body


def write(draft: Draft, path: Path | None = None, overwrite: bool = False) -> Path:
    """Create the profile file. Refuses to clobber an existing one."""
    from launcher import schema

    target = path or profile_path(draft.name)
    if target.exists() and not overwrite:
        raise FileExistsError(f"profile file already exists: {target}")
    profile = build(draft)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_yaml(profile, schema.modeline(schema.write())), encoding="utf-8")
    return target


STARTER = """\
{modeline}
name: {name}
description: Describe what this profile sets up
icon: "\\U0001F680"

# vars are available as {{{{ vars.NAME }}}} anywhere below
vars:
  session: {slug}

steps:
  - type: env
    set:
      PAL_SESSION: "{{{{ vars.session }}}}"

  - type: url
    name: example tab
    url: https://example.com

  # - type: app
  #   name: VS Code
  #   path:
  #     windows: "%LOCALAPPDATA%\\\\Programs\\\\Microsoft VS Code\\\\Code.exe"
  #     linux:   /usr/bin/code
  #   window:
  #     monitor: 1
  #     position: left-half

  # - type: command
  #   name: pull latest
  #   detach: false
  #   timeout: 30        # seconds; kill if it runs longer
  #   retries: 1         # retry once on failure
  #   retry_delay: 2     # wait 2s, then 4s, then 8s…
  #   optional: true     # failure doesn't fail the profile
  #   run: ["git", "pull", "--ff-only"]

  # - type: rsync        # mirror a directory (rsync when installed)
  #   name: back up notes
  #   src: ~/notes
  #   dest: /mnt/backup/notes
  #   delete: true

# teardown runs on `palaunch stop {name}`
# teardown:
#   - type: notify
#     message: "{name} closed"
"""


def starter_yaml(name: str) -> str:
    """The commented template `palaunch new` writes — a tour of the format."""
    from launcher import schema

    return STARTER.format(name=name, slug=slugify(name), modeline=schema.modeline(schema.write()))
