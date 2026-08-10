"""`{{ ... }}` template interpolation for profile values.

Runs *before* the shell-style `$VAR` / `%VAR%` expansion in the executor, so a
value can mix both. Supported placeholders::

    {{ profile }}          profile name
    {{ profile.icon }}     profile icon / description / any scalar field
    {{ now }}              2026-08-10T14:03:12   (ISO, seconds)
    {{ now:%H-%M }}        strftime with a custom format
    {{ date }} {{ time }}  2026-08-10 / 14:03:12
    {{ hostname }}         machine hostname
    {{ user }}             current username
    {{ home }}             home directory
    {{ platform }}         windows | linux | darwin
    {{ env.NAME }}         environment variable (run-local, sees `env:` steps)
    {{ vars.NAME }}        profile-level `vars:` entry
    {{ secret.NAME }}      OS credential store lookup

An unknown placeholder is left untouched rather than replaced by an empty
string — silently dropping a token from a command line is how you end up
running `rm -rf ` with the wrong argument list.
"""
from __future__ import annotations

import getpass
import os
import platform as _platform
import re
import socket
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_TOKEN = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


class InterpolationError(ValueError):
    """A placeholder could not be resolved and the value must not be used."""


@dataclass
class Context:
    profile_name: str = ""
    profile_fields: dict[str, Any] = field(default_factory=dict)
    vars: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    now: datetime = field(default_factory=datetime.now)
    # Secret values resolved during this run. The executor scrubs them from
    # step details and log lines so a token never reaches disk.
    secrets_used: set[str] = field(default_factory=set)

    @classmethod
    def for_profile(cls, profile, env: dict[str, str]) -> "Context":
        return cls(
            profile_name=profile.name,
            profile_fields={
                "name": profile.name,
                "icon": profile.icon,
                "description": profile.description,
                "tags": ",".join(profile.tags),
            },
            vars=dict(getattr(profile, "vars", {}) or {}),
            env=env,
        )


def _resolve(token: str, ctx: Context) -> str | None:
    """Return the replacement, or None to leave the placeholder verbatim."""
    head, sep, tail = token.partition(":")
    head = head.strip()
    fmt = tail.strip() if sep else ""

    if head == "now":
        return ctx.now.strftime(fmt) if fmt else ctx.now.isoformat(timespec="seconds")
    if head == "date":
        return ctx.now.strftime(fmt or "%Y-%m-%d")
    if head == "time":
        return ctx.now.strftime(fmt or "%H:%M:%S")
    if head == "hostname":
        return socket.gethostname()
    if head == "user":
        try:
            return getpass.getuser()
        except Exception:
            return os.environ.get("USER") or os.environ.get("USERNAME") or ""
    if head == "home":
        return str(Path.home())
    if head == "platform":
        return _platform.system().lower()
    if head == "profile":
        return ctx.profile_name

    prefix, dot, name = head.partition(".")
    if not dot:
        return None
    if prefix == "profile":
        value = ctx.profile_fields.get(name)
        return None if value is None else str(value)
    if prefix == "env":
        value = ctx.env.get(name)
        return None if value is None else str(value)
    if prefix == "vars":
        value = ctx.vars.get(name)
        return None if value is None else str(value)
    if prefix == "secret":
        from launcher import secrets

        try:
            value = secrets.get(name)
        except secrets.SecretError as exc:
            raise InterpolationError(str(exc)) from exc
        if value:
            ctx.secrets_used.add(value)
        return value
    return None


def scrub(text: str, ctx: Context) -> str:
    """Replace every secret resolved so far with `***`."""
    for value in ctx.secrets_used:
        if value:
            text = text.replace(value, "***")
    return text


def render(value: str, ctx: Context) -> str:
    """Substitute every recognised placeholder in `value`."""
    if "{{" not in value:
        return value

    def sub(match: re.Match[str]) -> str:
        replacement = _resolve(match.group(1), ctx)
        return match.group(0) if replacement is None else replacement

    return _TOKEN.sub(sub, value)


def render_any(value: Any, ctx: Context) -> Any:
    """Recursively render strings inside lists and dicts."""
    if isinstance(value, str):
        return render(value, ctx)
    if isinstance(value, list):
        return [render_any(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: render_any(v, ctx) for k, v in value.items()}
    return value


def has_secret(value: Any) -> bool:
    """True when the value references a secret — such details stay out of logs."""
    if isinstance(value, str):
        return any("secret." in m for m in _TOKEN.findall(value))
    if isinstance(value, (list, tuple)):
        return any(has_secret(v) for v in value)
    if isinstance(value, dict):
        return any(has_secret(v) for v in value.values())
    return False
