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

KNOWN_STEP_TYPES = frozenset({
    "app", "command", "script", "url", "env", "kill", "wait", "wait_for",
    "profile", "notify", "http", "plugin", "file",
})

# Steps that must carry at least one of these keys to be runnable at all.
REQUIRED_STEP_FIELDS: dict[str, tuple[str, ...]] = {
    "app": ("path",),
    "command": ("run",),
    "script": ("script",),
    "url": ("url",),
    "kill": ("process",),
    "wait_for": ("url", "run"),
    "profile": ("profile",),
    "notify": ("message", "title"),
    "http": ("url",),
    "plugin": ("plugin",),
    "file": ("action",),
}

KNOWN_PROFILE_KEYS = frozenset({
    "name", "description", "icon", "default", "autostart", "tags", "vars",
    "hotkey", "triggers", "steps", "teardown", "extends",
})

KNOWN_WINDOW_KEYS = frozenset({
    "monitor", "position", "workspace", "state", "x", "y", "width", "height",
    "match", "timeout", "focus",
})

KNOWN_TRIGGER_KEYS = frozenset({"at", "every", "weekday", "when", "name"})

KNOWN_FILE_ACTIONS = frozenset({"copy", "symlink", "mkdir", "remove", "write", "append"})

# Values may be written as {windows: …, linux: …, darwin: …, default: …}
PLATFORM_KEYED_FIELDS = (
    "path", "run", "cwd", "url", "process", "script", "src", "dest",
    "plugin", "title", "message", "content", "profile", "action",
)


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
class Trigger:
    """A time-based rule that fires a profile while the tray is running."""

    at: str = ""  # "HH:MM" — daily at that wall-clock time
    every: str = ""  # "45s" / "30m" / "2h" — fixed interval
    weekday: list[str] = field(default_factory=list)  # limits `at`/`every`
    when: dict[str, Any] | None = None  # extra conditions, same schema as steps
    name: str = ""


@dataclass
class Step:
    type: str
    name: str = ""
    id: str = ""  # referenced by `depends_on`
    # type=app/command/script
    path: str | None = None
    run: list[str] | str | None = None
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    detach: bool = True  # launch-and-forget by default
    track: bool = True  # register the pid so `palaunch stop` can close it
    window: dict[str, Any] | None = None  # placement for the spawned window
    # type=script
    script: str | None = None
    shell: str = "auto"  # auto | bash | sh | powershell | cmd
    # flow control
    parallel: bool = False  # run concurrently with adjacent parallel steps
    depends_on: list[str] = field(default_factory=list)  # explicit DAG edges
    enabled: bool = True  # disabled steps are skipped (kept in output as SKIP)
    optional: bool = False  # failure doesn't count towards the profile result
    timeout: float | None = None  # seconds; blocking commands and wait_for budget
    retries: int = 0  # extra attempts after a failure
    retry_delay: float = 0.0  # seconds before the first retry (doubles each time)
    when: dict[str, Any] | None = None  # skip the step unless all conditions hold
    on_failure: list["Step"] = field(default_factory=list)  # compensation steps
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
    # type=profile
    profile: str | None = None
    # type=notify
    title: str | None = None
    message: str | None = None
    # type=http
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    body: Any = None
    expect_status: list[int] = field(default_factory=list)
    # type=plugin
    plugin: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    # type=file
    action: str | None = None
    src: str | None = None
    dest: str | None = None
    content: str | None = None

    @property
    def label(self) -> str:
        return self.name or self.id or self.type

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
    vars: dict[str, str] = field(default_factory=dict)  # {{ vars.NAME }} sources
    hotkey: str = ""  # profile-specific global shortcut, handled by the tray
    triggers: list[Trigger] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    teardown: list[Step] = field(default_factory=list)  # `palaunch stop`
    source_path: Path | None = None


def _coerce_window(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("'window' must be a mapping")
    unknown = set(raw) - KNOWN_WINDOW_KEYS
    if unknown:
        raise ValueError(
            f"unknown 'window' keys {sorted(unknown)}; allowed: {sorted(KNOWN_WINDOW_KEYS)}"
        )
    return dict(raw)


def _coerce_when(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    from launcher.conditions import KNOWN_WHEN_KEYS

    if not isinstance(raw, dict):
        raise ValueError("'when' must be a mapping of conditions")
    unknown = set(raw) - KNOWN_WHEN_KEYS
    if unknown:
        raise ValueError(
            f"unknown 'when' keys {sorted(unknown)}; allowed: {sorted(KNOWN_WHEN_KEYS)}"
        )
    return raw


def _coerce_step(raw: dict[str, Any]) -> Step:
    if not isinstance(raw, dict):
        raise ValueError(f"step must be a mapping, got {type(raw).__name__}")
    typ = raw.get("type")
    if typ not in KNOWN_STEP_TYPES:
        raise ValueError(
            f"unknown step type {typ!r}; allowed: {sorted(KNOWN_STEP_TYPES)}"
        )
    step = Step(type=typ, name=raw.get("name", ""), id=str(raw.get("id", "")))
    for key in PLATFORM_KEYED_FIELDS:
        if key in raw:
            setattr(step, key, step.resolve_platform_value(raw[key]))
    raw_args = step.resolve_platform_value(raw.get("args"))
    step.args = [str(a) for a in (raw_args or [])]
    step.detach = bool(raw.get("detach", True))
    step.track = bool(raw.get("track", True))
    step.parallel = bool(raw.get("parallel", False))
    step.enabled = bool(raw.get("enabled", True))
    step.optional = bool(raw.get("optional", False))
    step.retries = max(0, int(raw.get("retries", 0)))
    step.retry_delay = max(0.0, float(raw.get("retry_delay", 0.0)))
    step.shell = str(raw.get("shell", "auto")).lower()
    raw_timeout = raw.get("timeout")
    step.timeout = float(raw_timeout) if raw_timeout is not None else None
    step.seconds = float(raw.get("seconds", 0.0))
    step.interval = max(0.1, float(raw.get("interval", 1.0)))
    step.method = str(raw.get("method", "GET")).upper()
    step.body = raw.get("body")
    step.headers = {str(k): str(v) for k, v in (raw.get("headers") or {}).items()}
    step.expect_status = [int(s) for s in _as_list(raw.get("expect_status"))]
    step.config = dict(raw.get("config") or {})
    step.window = _coerce_window(raw.get("window"))
    step.when = _coerce_when(raw.get("when"))
    step.depends_on = [str(d) for d in _as_list(raw.get("depends_on"))]
    step.on_failure = [_coerce_step(s) for s in (raw.get("on_failure") or [])]
    raw_set = raw.get("set") or {}
    step.set = {str(k): str(v) for k, v in raw_set.items()}
    step.unset = [str(k) for k in (raw.get("unset") or [])]
    if step.action is not None:
        step.action = str(step.action).lower()
        if step.action not in KNOWN_FILE_ACTIONS:
            raise ValueError(
                f"unknown file action {step.action!r}; allowed: {sorted(KNOWN_FILE_ACTIONS)}"
            )
    _check_required(step)
    return step


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _check_required(step: Step) -> None:
    required = REQUIRED_STEP_FIELDS.get(step.type)
    if not required:
        return
    if not any(getattr(step, key, None) for key in required):
        joined = " or ".join(f"'{k}'" for k in required)
        raise ValueError(f"step type '{step.type}' requires {joined}")


def _coerce_trigger(raw: Any) -> Trigger:
    if not isinstance(raw, dict):
        raise ValueError("each trigger must be a mapping")
    unknown = set(raw) - KNOWN_TRIGGER_KEYS
    if unknown:
        raise ValueError(
            f"unknown trigger keys {sorted(unknown)}; allowed: {sorted(KNOWN_TRIGGER_KEYS)}"
        )
    trigger = Trigger(
        at=str(raw.get("at", "")),
        every=str(raw.get("every", "")),
        weekday=[str(d)[:3].lower() for d in _as_list(raw.get("weekday"))],
        when=_coerce_when(raw.get("when")),
        name=str(raw.get("name", "")),
    )
    if not trigger.at and not trigger.every:
        raise ValueError("a trigger needs 'at:' or 'every:'")
    if trigger.at and not _valid_clock(trigger.at):
        raise ValueError(f"trigger 'at' must be HH:MM, got {trigger.at!r}")
    if trigger.every and parse_duration(trigger.every) is None:
        raise ValueError(f"trigger 'every' must be like '30m'/'2h', got {trigger.every!r}")
    return trigger


def _valid_clock(text: str) -> bool:
    hh, _, mm = text.partition(":")
    return hh.isdigit() and mm.isdigit() and 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59


def parse_duration(text: str) -> float | None:
    """'45s' / '30m' / '2h' / '90' (seconds) -> seconds, or None if malformed."""
    raw = str(text).strip().lower()
    if not raw:
        return None
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    factor = 1
    if raw[-1] in units:
        factor = units[raw[-1]]
        raw = raw[:-1]
    try:
        value = float(raw)
    except ValueError:
        return None
    return value * factor if value > 0 else None


def _read_raw(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError("profile file must contain a YAML mapping at the top level")
    return data


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
    derived profile. `hotkey` is excluded for the same reason: two profiles
    claiming one shortcut is a silent conflict."""
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
    not_inherited = ("default", "autostart", "name", "hotkey")
    merged = {k: v for k, v in parent.items() if k not in not_inherited}
    merged.update({k: v for k, v in data.items() if k not in ("steps", "teardown", "extends", "vars")})
    merged["steps"] = list(parent.get("steps") or []) + list(data.get("steps") or [])
    merged["teardown"] = list(parent.get("teardown") or []) + list(data.get("teardown") or [])
    merged["vars"] = {**(parent.get("vars") or {}), **(data.get("vars") or {})}
    return merged


def load_profile(path: Path) -> Profile:
    data = _resolve_extends(_read_raw(path), path.parent, {path.resolve()})
    unknown = set(data) - KNOWN_PROFILE_KEYS
    if unknown:
        raise ValueError(
            f"unknown profile keys {sorted(unknown)}; allowed: {sorted(KNOWN_PROFILE_KEYS)}"
        )
    steps = [_coerce_step(s) for s in (data.get("steps") or [])]
    teardown = [_coerce_step(s) for s in (data.get("teardown") or [])]
    _check_step_ids(steps)
    _check_step_ids(teardown)
    return Profile(
        name=data.get("name") or path.stem,
        description=data.get("description", ""),
        icon=str(data.get("icon", "")),
        default=bool(data.get("default", False)),
        autostart=bool(data.get("autostart", False)),
        tags=[str(t) for t in (data.get("tags") or [])],
        vars={str(k): str(v) for k, v in (data.get("vars") or {}).items()},
        hotkey=str(data.get("hotkey", "")),
        triggers=[_coerce_trigger(t) for t in (data.get("triggers") or [])],
        steps=steps,
        teardown=teardown,
        source_path=path,
    )


def _check_step_ids(steps: list[Step]) -> None:
    """`depends_on` is only meaningful if every referenced id exists and is unique."""
    ids: set[str] = set()
    for step in steps:
        if not step.id:
            continue
        if step.id in ids:
            raise ValueError(f"duplicate step id '{step.id}'")
        ids.add(step.id)
    for step in steps:
        for dep in step.depends_on:
            if dep not in ids:
                raise ValueError(
                    f"step '{step.label}' depends_on unknown id '{dep}'"
                )
            if dep == step.id:
                raise ValueError(f"step '{step.label}' depends on itself")


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


def profile_files() -> list[Path]:
    return [
        path
        for directory in _profile_search_dirs()
        if directory.is_dir()
        for path in sorted(directory.glob("*.y*ml"))
    ]


def discover_profiles() -> list[Profile]:
    seen: dict[str, Profile] = {}
    for path in profile_files():
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
