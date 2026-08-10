"""Global user settings (`settings.yaml`) shared by the CLI, HUD, tray and scheduler.

Precedence, highest first:

1. ``PAL_*`` environment variables — one-off overrides, handy for testing
2. ``<config dir>/settings.yaml``
3. the defaults below

Reading is cached per-process; call :func:`reload` after writing the file.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from typing import Any

import yaml

from launcher.config import settings_path

# Env var name -> (settings field, coercion). Env always wins so that
# `PAL_NOTIFY=0 palaunch run Dev` keeps working regardless of the file.
_ENV_OVERRIDES: dict[str, tuple[str, Any]] = {
    "PAL_HOTKEY": ("hotkey", str),
    "PAL_THEME": ("theme", str),
    "PAL_LOG_LEVEL": ("log_level", str),
    "PAL_MAX_PARALLEL": ("max_parallel", int),
    "PAL_EDITOR": ("editor", str),
    "PAL_SYNC_REMOTE": ("sync_remote", str),
}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "off", "")


@dataclass
class Settings:
    hotkey: str = "<alt>+<space>"
    theme: str = "auto"  # auto | dark | light
    notifications: bool = True
    log_level: str = "INFO"
    log_max_bytes: int = 1_000_000
    log_backups: int = 3
    history_limit: int = 500  # run records kept in history.jsonl
    max_parallel: int = 8  # 0 = unlimited
    editor: str = ""  # empty -> $EDITOR / $VISUAL / OS default
    confirm_stop: bool = False
    sync_remote: str = ""
    scheduler: bool = True  # honour profile `triggers:` while the tray runs
    window_management: bool = True
    hud_width: int = 620
    hud_height: int = 420
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        extra = data.pop("extra", {})
        data.update(extra)
        return data


_KNOWN_FIELDS = {f.name for f in fields(Settings)} - {"extra"}
_cache: Settings | None = None


def _coerce(name: str, raw: Any, default: Any) -> Any:
    if isinstance(default, bool):
        return _as_bool(raw)
    if isinstance(default, int):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    return str(raw)


def _read_file() -> dict[str, Any]:
    path = settings_path()
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def load() -> Settings:
    """Return the effective settings, reading `settings.yaml` at most once."""
    global _cache
    if _cache is not None:
        return _cache
    defaults = Settings()
    raw = _read_file()
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key in _KNOWN_FIELDS:
            values[key] = _coerce(key, value, getattr(defaults, key))
    unknown = {k: v for k, v in raw.items() if k not in _KNOWN_FIELDS}
    for env_name, (field_name, _cast) in _ENV_OVERRIDES.items():
        env_value = os.environ.get(env_name)
        if env_value:
            values[field_name] = _coerce(field_name, env_value, getattr(defaults, field_name))
    if os.environ.get("PAL_NOTIFY") is not None:
        values["notifications"] = _as_bool(os.environ["PAL_NOTIFY"])
    _cache = Settings(**values, extra=unknown)
    return _cache


def reload() -> Settings:
    """Drop the cache and re-read the file (used after `palaunch config set`)."""
    global _cache
    _cache = None
    return load()


def get(name: str, default: Any = None) -> Any:
    settings = load()
    if hasattr(settings, name):
        return getattr(settings, name)
    return settings.extra.get(name, default)


def save(values: dict[str, Any]) -> None:
    """Merge `values` into settings.yaml, preserving unrelated keys."""
    raw = _read_file()
    raw.update(values)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw, sort_keys=True, allow_unicode=True), encoding="utf-8")
    reload()


def known_keys() -> list[str]:
    return sorted(_KNOWN_FIELDS)
