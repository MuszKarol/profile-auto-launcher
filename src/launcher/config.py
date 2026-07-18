"""Profile configuration loading and schema validation."""
from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from platformdirs import user_config_dir

APP_NAME = "profile-auto-launcher"
PLATFORM = platform.system().lower()  # 'windows', 'linux', 'darwin'

KNOWN_STEP_TYPES = frozenset({"app", "command", "url", "env", "kill", "wait", "wait_for"})
KNOWN_WHEN_KEYS = frozenset({"platform", "exists", "not_exists", "env", "weekday"})


def config_dir() -> Path:
    override = os.environ.get("PAL_CONFIG_DIR")
    if override:
        return Path(override)
    return Path(user_config_dir(APP_NAME, appauthor=False))


def profiles_dir() -> Path:
    return config_dir() / "profiles"


def settings_path() -> Path:
    return config_dir() / "settings.yaml"


@dataclass
class Step:
    type: str
    name: str = ""
    # type=app/command
    path: str | None = None
    run: list[str] | str | None = None
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    detach: bool = True  # launch-and-forget by default
    parallel: bool = False  # run concurrently with adjacent parallel steps
    enabled: bool = True  # disabled steps are skipped (kept in output as SKIP)
    optional: bool = False  # failure doesn't count towards the profile result
    timeout: float | None = None  # seconds; blocking commands and wait_for budget
    retries: int = 0  # extra attempts after a failure
    when: dict[str, Any] | None = None  # skip the step unless all conditions hold
    # type=wait_for
    interval: float = 1.0  # poll interval in seconds
    # type=url
    url: str | None = None
    # type=wait
    seconds: float = 0.0
    # type=env
    set: dict[str, str] = field(default_factory=dict)
    unset: list[str] = field(default_factory=list)
    # type=kill
    process: str | None = None

    def resolve_platform_value(self, value: Any) -> Any:
        """If value is a dict keyed by platform, pick this platform's entry."""
        if isinstance(value, dict) and any(k in value for k in ("windows", "linux", "darwin")):
            return value.get(PLATFORM) or value.get("default")
        return value


@dataclass
class Profile:
    name: str
    description: str = ""
    icon: str = ""  # short glyph/emoji shown in the HUD and tray
    default: bool = False
    autostart: bool = False  # `palaunch tray` runs this profile once at startup
    tags: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    source_path: Path | None = None


def _coerce_step(raw: dict[str, Any]) -> Step:
    typ = raw.get("type")
    if typ not in KNOWN_STEP_TYPES:
        raise ValueError(
            f"unknown step type {typ!r}; allowed: {sorted(KNOWN_STEP_TYPES)}"
        )
    step = Step(type=typ, name=raw.get("name", ""))
    for key in ("path", "run", "cwd", "url", "process"):
        if key in raw:
            setattr(step, key, step.resolve_platform_value(raw[key]))
    step.args = list(raw.get("args") or [])
    step.detach = bool(raw.get("detach", True))
    step.parallel = bool(raw.get("parallel", False))
    step.enabled = bool(raw.get("enabled", True))
    step.optional = bool(raw.get("optional", False))
    step.retries = max(0, int(raw.get("retries", 0)))
    raw_timeout = raw.get("timeout")
    step.timeout = float(raw_timeout) if raw_timeout is not None else None
    step.seconds = float(raw.get("seconds", 0.0))
    step.interval = max(0.1, float(raw.get("interval", 1.0)))
    raw_when = raw.get("when")
    if raw_when is not None:
        if not isinstance(raw_when, dict):
            raise ValueError("'when' must be a mapping of conditions")
        unknown = set(raw_when) - KNOWN_WHEN_KEYS
        if unknown:
            raise ValueError(
                f"unknown 'when' keys {sorted(unknown)}; allowed: {sorted(KNOWN_WHEN_KEYS)}"
            )
        step.when = raw_when
    raw_set = raw.get("set") or {}
    step.set = {str(k): str(v) for k, v in raw_set.items()}
    step.unset = [str(k) for k in (raw.get("unset") or [])]
    return step


def _read_raw(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _find_parent_file(name: str, start_dir: Path) -> Path | None:
    for base in (start_dir, profiles_dir()):
        for ext in (".yaml", ".yml"):
            candidate = base / f"{name}{ext}"
            if candidate.is_file():
                return candidate
    return None


def _resolve_extends(data: dict[str, Any], directory: Path, seen: set[Path]) -> dict[str, Any]:
    """Merge `extends: <file-stem>` chains: parent steps run first, child
    scalar fields win. `default` and `autostart` are never inherited — a child
    must opt in explicitly, otherwise one parent flag would fan out to every
    derived profile."""
    parent_name = data.get("extends")
    if not parent_name:
        return data
    parent_path = _find_parent_file(str(parent_name), directory)
    if parent_path is None:
        raise ValueError(f"extends: profile file '{parent_name}' not found")
    resolved = parent_path.resolve()
    if resolved in seen:
        raise ValueError(f"extends: cycle detected at '{parent_name}'")
    seen.add(resolved)
    parent = _resolve_extends(_read_raw(parent_path), parent_path.parent, seen)
    # `name` isn't inherited either — two profiles with one name would shadow
    # each other in discovery; a child without `name:` falls back to its stem.
    merged = {k: v for k, v in parent.items() if k not in ("default", "autostart", "name")}
    merged.update({k: v for k, v in data.items() if k not in ("steps", "extends")})
    merged["steps"] = list(parent.get("steps") or []) + list(data.get("steps") or [])
    return merged


def load_profile(path: Path) -> Profile:
    data = _resolve_extends(_read_raw(path), path.parent, {path.resolve()})
    steps = [_coerce_step(s) for s in data.get("steps", [])]
    return Profile(
        name=data.get("name") or path.stem,
        description=data.get("description", ""),
        icon=str(data.get("icon", "")),
        default=bool(data.get("default", False)),
        autostart=bool(data.get("autostart", False)),
        tags=[str(t) for t in (data.get("tags") or [])],
        steps=steps,
        source_path=path,
    )


def _profile_search_dirs() -> list[Path]:
    """User config dir first, then ./profiles ONLY when PAL_INCLUDE_CWD is set.

    CWD discovery is opt-in because running `palaunch` in an arbitrary directory
    that happens to contain `profiles/*.yaml` would otherwise execute whatever
    commands those YAMLs declare — a real footgun for anyone running palaunch
    from a working tree they don't own.
    """
    dirs = [profiles_dir()]
    if os.environ.get("PAL_INCLUDE_CWD") == "1":
        dirs.append(Path.cwd() / "profiles")
    return dirs


def profile_search_dirs() -> list[Path]:
    """Public view of the directories `discover_profiles` scans."""
    return _profile_search_dirs()


def discover_profiles() -> list[Profile]:
    seen: dict[str, Profile] = {}
    for directory in _profile_search_dirs():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.y*ml")):
            try:
                prof = load_profile(path)
            except (OSError, yaml.YAMLError, ValueError) as exc:
                print(f"[config] failed to load {path}: {exc}", file=sys.stderr)
                continue
            seen.setdefault(prof.name, prof)
    return list(seen.values())


def find_profile(name: str) -> Profile | None:
    for prof in discover_profiles():
        if prof.name.lower() == name.lower():
            return prof
    return None


def default_profile(profiles: list[Profile]) -> Profile | None:
    for prof in profiles:
        if prof.default:
            return prof
    return profiles[0] if profiles else None
