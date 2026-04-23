"""Profile configuration loading and schema validation."""
from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from platformdirs import user_config_dir

APP_NAME = "profile-auto-launcher"
PLATFORM = platform.system().lower()  # 'windows', 'linux', 'darwin'


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
    default: bool = False
    steps: list[Step] = field(default_factory=list)
    source_path: Path | None = None


def _coerce_step(raw: dict[str, Any]) -> Step:
    step = Step(type=raw["type"], name=raw.get("name", ""))
    for key in ("path", "run", "cwd", "url", "process"):
        if key in raw:
            setattr(step, key, step.resolve_platform_value(raw[key]))
    step.args = raw.get("args", []) or []
    step.detach = bool(raw.get("detach", True))
    step.parallel = bool(raw.get("parallel", False))
    step.seconds = float(raw.get("seconds", 0.0))
    step.set = raw.get("set", {}) or {}
    step.unset = raw.get("unset", []) or []
    return step


def load_profile(path: Path) -> Profile:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    steps = [_coerce_step(s) for s in data.get("steps", [])]
    return Profile(
        name=data.get("name") or path.stem,
        description=data.get("description", ""),
        default=bool(data.get("default", False)),
        steps=steps,
        source_path=path,
    )


def discover_profiles() -> list[Profile]:
    """Load profiles from the user config dir, then fall back to bundled ./profiles."""
    seen: dict[str, Profile] = {}
    for directory in (profiles_dir(), Path.cwd() / "profiles"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.y*ml")):
            try:
                prof = load_profile(path)
            except Exception as exc:  # noqa: BLE001
                print(f"[config] failed to load {path}: {exc}")
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
