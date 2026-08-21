"""Data model behind the graphical configuration panel.

Everything the panel needs to know that is *not* Tk lives here: which settings
exist and how each one is edited, how a typed-in string becomes a stored value,
and the read-only reports (paths, profiles, history) the panel renders.

Keeping it separate is what makes the panel testable — the whole model runs
without a display, so the rules that decide whether `max_parallel: -1` is
accepted are checked in CI rather than by clicking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from launcher import settings


@dataclass(frozen=True)
class Field:
    """One editable setting: how to label it, render it, and validate it."""

    key: str
    label: str
    kind: str  # bool | int | str | choice
    help: str = ""
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True)
class Group:
    title: str
    fields: tuple[Field, ...]


GROUPS: tuple[Group, ...] = (
    Group(
        "Appearance",
        (
            Field(
                "theme",
                "Theme",
                "choice",
                "Colours for the HUD, editor and this panel.",
                choices=("auto", "dark", "light"),
            ),
            Field("hud_width", "HUD width (px)", "int", minimum=420, maximum=3000),
            Field("hud_height", "HUD height (px)", "int", minimum=300, maximum=3000),
        ),
    ),
    Group(
        "Behaviour",
        (
            Field(
                "hotkey", "Global hotkey", "str", "Opens the HUD from anywhere, e.g. <alt>+<space>."
            ),
            Field(
                "notifications", "Desktop notifications", "bool", "Toast when a profile finishes."
            ),
            Field("confirm_stop", "Confirm before stopping", "bool"),
            Field(
                "scheduler",
                "Honour profile triggers",
                "bool",
                "Runs profile `triggers:` while the tray is up.",
            ),
            Field(
                "window_management",
                "Place windows on screen",
                "bool",
                "Applies each step's `window:` block.",
            ),
            Field(
                "max_parallel",
                "Max parallel steps",
                "int",
                "0 means unlimited.",
                minimum=0,
                maximum=256,
            ),
            Field(
                "editor", "Text editor", "str", "Blank falls back to $EDITOR, then the OS default."
            ),
        ),
    ),
    Group(
        "Logging and history",
        (
            Field(
                "log_level",
                "Log level",
                "choice",
                choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
            ),
            Field(
                "log_max_bytes",
                "Log size before rotating",
                "int",
                minimum=10_000,
                maximum=100_000_000,
            ),
            Field("log_backups", "Rotated logs kept", "int", minimum=0, maximum=50),
            Field("history_limit", "Run records kept", "int", minimum=10, maximum=100_000),
        ),
    ),
    Group(
        "Sync",
        (
            Field(
                "sync_remote",
                "Git remote for profiles",
                "str",
                "Used by `palaunch sync` and the Sync tab.",
            ),
        ),
    ),
)


def all_fields() -> list[Field]:
    return [field for group in GROUPS for field in group.fields]


def field_for(key: str) -> Field | None:
    return next((f for f in all_fields() if f.key == key), None)


class ValidationError(ValueError):
    """A submitted value is not usable for the field it belongs to."""


def coerce(field: Field, raw: Any) -> Any:
    """Turn a widget's raw value into what `settings.yaml` should hold."""
    if field.kind == "bool":
        return bool(raw)
    if field.kind == "choice":
        value = str(raw).strip()
        if field.choices and value not in field.choices:
            raise ValidationError(f"{field.label}: choose one of {', '.join(field.choices)}")
        return value
    if field.kind == "int":
        text = str(raw).strip()
        try:
            value = int(text)
        except ValueError:
            raise ValidationError(f"{field.label}: '{raw}' is not a whole number") from None
        if field.minimum is not None and value < field.minimum:
            raise ValidationError(f"{field.label}: must be at least {field.minimum}")
        if field.maximum is not None and value > field.maximum:
            raise ValidationError(f"{field.label}: must be at most {field.maximum}")
        return value
    return str(raw).strip()


def current_values() -> dict[str, Any]:
    """The effective value of every field the panel can edit."""
    conf = settings.load()
    return {field.key: getattr(conf, field.key) for field in all_fields()}


def defaults() -> dict[str, Any]:
    blank = settings.Settings()
    return {field.key: getattr(blank, field.key) for field in all_fields()}


def env_overrides() -> dict[str, str]:
    """Settings currently forced by a `PAL_*` variable -> the variable name.

    The panel says so out loud: writing the file would look like it worked and
    then be ignored, because environment variables win in `settings.load()`.
    """
    import os

    forced = {
        name: field_name
        for name, (field_name, _cast) in settings._ENV_OVERRIDES.items()
        if os.environ.get(name)
    }
    result = {field_name: name for name, field_name in forced.items()}
    if os.environ.get("PAL_NOTIFY") is not None:
        result["notifications"] = "PAL_NOTIFY"
    return {key: var for key, var in result.items() if field_for(key) is not None}


def validate(submitted: dict[str, Any]) -> dict[str, Any]:
    """Coerce every submitted value, raising on the first unusable one."""
    values: dict[str, Any] = {}
    for key, raw in submitted.items():
        field = field_for(key)
        if field is None:
            continue
        values[key] = coerce(field, raw)
    return values


def changes(submitted: dict[str, Any]) -> dict[str, Any]:
    """Validated values that actually differ from what is stored today."""
    current = current_values()
    return {k: v for k, v in validate(submitted).items() if current.get(k) != v}


def apply(submitted: dict[str, Any]) -> dict[str, Any]:
    """Persist the submitted settings. Returns the subset that changed."""
    diff = changes(submitted)
    if diff:
        settings.save(diff)
    return diff


# ── read-only reports ────────────────────────────────────────────────────


def paths_report() -> list[tuple[str, str]]:
    """The same facts as `palaunch where`, as label/value pairs."""
    from launcher import schema, windows
    from launcher.config import config_dir, profiles_dir, settings_path
    from launcher.logging_setup import log_path
    from launcher.state import history_path, state_path

    return [
        ("Config directory", str(config_dir())),
        ("Profiles", str(profiles_dir())),
        ("Settings file", str(settings_path())),
        ("Log file", str(log_path())),
        ("Run history", str(history_path())),
        ("State", str(state_path())),
        ("JSON Schema", str(schema.schema_path())),
        ("Window backend", windows.backend_name()),
    ]


def profile_rows() -> list[dict[str, Any]]:
    """One row per discovered profile, with live process counts folded in."""
    from launcher import procs
    from launcher.config import discover_profiles

    try:
        active = procs.active_profiles()
    except Exception:  # a corrupt registry must not empty the profile list
        active = {}
    rows = []
    for profile in discover_profiles():
        rows.append(
            {
                "name": profile.name,
                "icon": profile.icon,
                "description": profile.description,
                "steps": len(profile.steps),
                "default": profile.default,
                "hotkey": profile.hotkey,
                "running": len(active.get(profile.name, [])),
                "path": str(profile.source_path) if profile.source_path else "",
            }
        )
    return rows


def history_rows(limit: int = 30, profile: str | None = None) -> list[str]:
    from launcher.state import load_history

    lines = []
    for record in load_history(limit=limit, profile=profile):
        mark = "✓" if record["failed"] == 0 else "✗"
        kind = "" if record.get("kind") == "run" else f" [{record.get('kind')}]"
        lines.append(
            f"{mark} {record['ts']}  {record['profile']:<16}{kind} "
            f"{record['ok']} ok, {record['failed']} failed, "
            f"{record['skipped']} skipped  ({record['duration']}s)"
        )
    return lines


def stats_rows(limit: int = 30, profile: str | None = None) -> list[str]:
    from launcher.state import step_stats

    rows = step_stats(profile=profile)[:limit]
    if not rows:
        return []
    lines = [f"{'profile':<14}{'step':<26}{'runs':>5}{'fail%':>7}{'avg s':>8}{'max s':>8}"]
    for row in rows:
        lines.append(
            f"{row['profile'][:13]:<14}{row['step'][:25]:<26}{row['runs']:>5}"
            f"{row['failure_rate'] * 100:>6.0f}%{row['avg_duration']:>8.2f}"
            f"{row['max_duration']:>8.2f}"
        )
    return lines


def hotkey_rows() -> list[str]:
    from launcher import hotkey
    from launcher.config import discover_profiles

    return hotkey.describe(discover_profiles())


def open_path(path: Any) -> str:
    """Open `path` in the configured editor, or the OS default handler.

    Shared by `palaunch edit` and the panel's "Open folder" buttons so both
    honour the `editor` setting. Raises OSError when nothing could be started.
    """
    import os
    import subprocess

    from launcher.config import PLATFORM

    target = str(path)
    editor = settings.load().editor or os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if editor:
        subprocess.run([editor, target], check=False)
        return f"opened {target} in {editor}"
    if PLATFORM == "windows":
        os.startfile(target)  # type: ignore[attr-defined]
    elif PLATFORM == "darwin":
        subprocess.run(["open", target], check=False)
    else:
        subprocess.run(["xdg-open", target], check=False)
    return f"opened {target}"
