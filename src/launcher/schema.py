"""JSON Schema generation for profile files.

Editors with the YAML language server validate and autocomplete a file that
carries a `$schema` modeline, which turns the profile format into something
discoverable while typing instead of something to look up in the README.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from launcher.conditions import KNOWN_WHEN_KEYS
from launcher.config import (
    KNOWN_FILE_ACTIONS,
    KNOWN_STEP_TYPES,
    KNOWN_WINDOW_KEYS,
    config_dir,
)

SCHEMA_ID = "https://github.com/MuszKarol/profile-auto-launcher/profile.schema.json"

_PLATFORM_KEYED = {
    "oneOf": [
        {"type": "string"},
        {
            "type": "object",
            "properties": {
                "windows": {"type": "string"},
                "linux": {"type": "string"},
                "darwin": {"type": "string"},
                "default": {"type": "string"},
            },
            "additionalProperties": False,
        },
    ]
}

_STRING_OR_LIST = {
    "oneOf": [
        {"type": "string"},
        {"type": "array", "items": {"type": "string"}},
        {"type": "object"},
    ]
}


def _when_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "All conditions must hold for the step to run.",
        "properties": {key: {} for key in sorted(KNOWN_WHEN_KEYS)},
        "additionalProperties": False,
    }


def _window_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {key: {} for key in sorted(KNOWN_WINDOW_KEYS)},
        "additionalProperties": False,
    }


def _step_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["type"],
        "properties": {
            "type": {"enum": sorted(KNOWN_STEP_TYPES)},
            "name": {"type": "string"},
            "id": {"type": "string", "description": "Referenced by depends_on"},
            "path": _PLATFORM_KEYED,
            "run": _STRING_OR_LIST,
            "args": _STRING_OR_LIST,
            "cwd": _PLATFORM_KEYED,
            "script": _PLATFORM_KEYED,
            "shell": {"enum": ["auto", "bash", "sh", "zsh", "powershell", "cmd"]},
            "url": _PLATFORM_KEYED,
            "process": _PLATFORM_KEYED,
            "profile": {"type": "string"},
            "plugin": _PLATFORM_KEYED,
            "config": {"type": "object"},
            "title": {"type": "string"},
            "message": {"type": "string"},
            "method": {"enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]},
            "headers": {"type": "object", "additionalProperties": {"type": "string"}},
            "body": {},
            "expect_status": {
                "oneOf": [
                    {"type": "integer"},
                    {"type": "array", "items": {"type": "integer"}},
                ]
            },
            "action": {"enum": sorted(KNOWN_FILE_ACTIONS)},
            "src": _PLATFORM_KEYED,
            "dest": _PLATFORM_KEYED,
            "content": {"type": "string"},
            "delete": {"type": "boolean", "description": "rsync: delete extraneous files"},
            "exclude": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            },
            "backend": {"enum": ["auto", "rsync", "builtin"]},
            "set": {"type": "object", "additionalProperties": {"type": "string"}},
            "unset": {"type": "array", "items": {"type": "string"}},
            "seconds": {"type": "number", "minimum": 0},
            "interval": {"type": "number", "minimum": 0.1},
            "timeout": {"type": "number", "minimum": 0},
            "retries": {"type": "integer", "minimum": 0},
            "retry_delay": {"type": "number", "minimum": 0},
            "detach": {"type": "boolean"},
            "track": {"type": "boolean"},
            "parallel": {"type": "boolean"},
            "enabled": {"type": "boolean"},
            "optional": {"type": "boolean"},
            "depends_on": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            },
            "when": _when_schema(),
            "window": _window_schema(),
            "on_failure": {"type": "array", "items": {"$ref": "#/$defs/step"}},
        },
        "additionalProperties": False,
    }


def build() -> dict[str, Any]:
    step_list = {"type": "array", "items": {"$ref": "#/$defs/step"}}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "Profile Auto Launcher profile",
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "icon": {
                "type": "string",
                "description": "Accepted for older profiles; the interface draws no per-row icons",
            },
            "default": {"type": "boolean"},
            "autostart": {"type": "boolean"},
            "hotkey": {"type": "string", "examples": ["<ctrl>+<alt>+d"]},
            "extends": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "vars": {"type": "object", "additionalProperties": {"type": "string"}},
            "triggers": {"type": "array", "items": {"$ref": "#/$defs/trigger"}},
            "steps": step_list,
            "teardown": step_list,
        },
        "additionalProperties": False,
        "$defs": {
            "step": _step_schema(),
            "trigger": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "at": {"type": "string", "pattern": r"^\d{1,2}:\d{2}$"},
                    "every": {"type": "string", "pattern": r"^\d+(\.\d+)?[smhd]?$"},
                    "weekday": {"type": "array", "items": {"type": "string"}},
                    "when": _when_schema(),
                },
                "additionalProperties": False,
            },
        },
    }


def schema_path() -> Path:
    return config_dir() / "profile.schema.json"


def write(path: Path | None = None) -> Path:
    target = path or schema_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    return target


def modeline(path: Path | None = None) -> str:
    """The comment that hooks a profile file up to the YAML language server."""
    target = path or schema_path()
    return f"# yaml-language-server: $schema={target.as_uri()}"
