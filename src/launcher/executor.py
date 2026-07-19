"""Asynchronous profile executor.

Steps run sequentially by default. Consecutive steps marked `parallel: true`
are batched and launched concurrently; the batch is awaited before the next
non-parallel step (or `wait`) runs. Apps and URLs are launch-and-forget
(detached) so the launcher doesn't block on long-lived processes.
"""
from __future__ import annotations

import asyncio
import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from launcher.config import PLATFORM, Profile, Step

_POSIX_VAR = re.compile(r"\$(\w+|\{[^}]*\})")
_WIN_VAR = re.compile(r"%([^%]+)%")

# When the launcher runs windowless (palaunchw / pythonw), a console child
# spawned without this flag pops up its own console window. Inherited stdio
# handles still work, so output capture is unaffected. 0 on non-Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class StepResult:
    step: Step
    ok: bool
    detail: str = ""
    skipped: bool = False

    @property
    def counts_as_failure(self) -> bool:
        return not self.ok and not self.step.optional


def _expand(value: str | None, env: dict[str, str]) -> str | None:
    """Expand `~`, `$VAR`, `${VAR}`, and `%VAR%` against the *local* env dict.

    `os.path.expandvars` reads `os.environ` directly, which would miss
    mutations performed by earlier `env` steps in the same profile run.
    """
    if value is None:
        return None
    expanded = os.path.expanduser(value)

    def posix_sub(match: re.Match[str]) -> str:
        token = match.group(1)
        name = token[1:-1] if token.startswith("{") and token.endswith("}") else token
        return env.get(name, match.group(0))

    expanded = _POSIX_VAR.sub(posix_sub, expanded)
    expanded = _WIN_VAR.sub(lambda m: env.get(m.group(1), m.group(0)), expanded)
    return expanded


def _as_argv(run: list[str] | str | None) -> list[str]:
    if run is None:
        return []
    if isinstance(run, list):
        return [str(x) for x in run]
    return shlex.split(run, posix=(PLATFORM != "windows"))


async def _launch_app(step: Step, env: dict[str, str]) -> StepResult:
    path = _expand(step.path, env)
    if not path:
        return StepResult(step, False, "missing path")
    argv = [path, *[_expand(a, env) or a for a in step.args]]
    try:
        _spawn(argv, env=env, cwd=_expand(step.cwd, env), detach=step.detach)
        return StepResult(step, True, f"launched {path}")
    except FileNotFoundError:
        return StepResult(step, False, f"not found: {path}")
    except OSError as exc:
        return StepResult(step, False, str(exc))


async def _run_command(step: Step, env: dict[str, str]) -> StepResult:
    argv = _as_argv(step.run)
    if not argv:
        return StepResult(step, False, "empty run")
    argv = [_expand(a, env) or a for a in argv]
    try:
        if step.detach:
            _spawn(argv, env=env, cwd=_expand(step.cwd, env), detach=True)
            return StepResult(step, True, f"spawned {argv[0]}")
        proc = await asyncio.create_subprocess_exec(
            *argv,
            env=env,
            cwd=_expand(step.cwd, env),
            stderr=asyncio.subprocess.PIPE,
            creationflags=_NO_WINDOW,
        )
        try:
            if step.timeout:
                _, stderr = await asyncio.wait_for(proc.communicate(), step.timeout)
            else:
                _, stderr = await proc.communicate()
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return StepResult(step, False, f"timed out after {step.timeout}s")
        if proc.returncode == 0:
            return StepResult(step, True, "exit 0")
        return StepResult(
            step, False,
            f"exit {proc.returncode}: {stderr.decode(errors='replace').strip()}",
        )
    except FileNotFoundError:
        return StepResult(step, False, f"not found: {argv[0]}")
    except OSError as exc:
        return StepResult(step, False, str(exc))


async def _open_url(step: Step, env: dict[str, str]) -> StepResult:
    if not step.url:
        return StepResult(step, False, "missing url")
    if not _is_safe_url(step.url):
        return StepResult(step, False, f"refused unsafe url scheme: {step.url}")
    try:
        webbrowser.open(step.url, new=2)
        return StepResult(step, True, step.url)
    except (OSError, webbrowser.Error) as exc:
        return StepResult(step, False, str(exc))


_ALLOWED_URL_SCHEMES = frozenset({"http", "https", "mailto", "ftp", "ftps"})


def _is_safe_url(url: str) -> bool:
    """Reject schemes that webbrowser.open would happily resolve to local files
    or shell-handled URIs (file://, javascript:, data:, custom protocols)."""
    scheme = url.split(":", 1)[0].lower() if ":" in url else ""
    return scheme in _ALLOWED_URL_SCHEMES


async def _apply_env(step: Step, env: dict[str, str]) -> StepResult:
    for k, v in step.set.items():
        env[k] = str(v)
    for k in step.unset:
        env.pop(k, None)
    return StepResult(step, True, f"set={list(step.set)} unset={step.unset}")


async def _kill_process(step: Step, env: dict[str, str]) -> StepResult:
    if not step.process:
        return StepResult(step, False, "missing process")
    try:
        if PLATFORM == "windows":
            subprocess.run(
                ["taskkill", "/F", "/IM", step.process],
                capture_output=True, check=False, creationflags=_NO_WINDOW,
            )
        else:
            # `-x` requires whole-name match; re.escape neuters regex metacharacters
            # so a profile entry like `process: .` cannot accidentally match every
            # 1-char process name. Without these flags, pkill's default substring +
            # regex match against the full command line is alarmingly broad.
            subprocess.run(
                ["pkill", "-x", re.escape(step.process)],
                capture_output=True, check=False,
            )
        return StepResult(step, True, f"kill {step.process}")
    except OSError as exc:
        return StepResult(step, False, str(exc))


async def _probe_tcp(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 3.0)
        writer.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


def _probe_http(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3.0) as resp:
            return resp.status < 500
    except urllib.error.HTTPError as exc:
        return exc.code < 500  # server answered — it's up, even if 4xx
    except (urllib.error.URLError, OSError, ValueError):
        return False


async def _probe_command(argv: list[str], env: dict[str, str]) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, env=env,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
        return await proc.wait() == 0
    except (OSError, FileNotFoundError):
        return False


async def _wait_for(step: Step, env: dict[str, str]) -> StepResult:
    """Poll a tcp/http endpoint or a command until it's ready (or timeout)."""
    target = _expand(step.url, env)
    argv = [_expand(a, env) or a for a in _as_argv(step.run)]
    if not target and not argv:
        return StepResult(step, False, "wait_for needs url: or run:")

    async def ready() -> bool:
        if target:
            scheme = target.split(":", 1)[0].lower()
            if scheme == "tcp":
                hostport = target[len("tcp://"):]
                host, _, port = hostport.rpartition(":")
                if not host or not port.isdigit():
                    return False
                return await _probe_tcp(host, int(port))
            if scheme in ("http", "https"):
                return await asyncio.to_thread(_probe_http, target)
            return False
        return await _probe_command(argv, env)

    if target and target.split(":", 1)[0].lower() not in ("tcp", "http", "https"):
        return StepResult(step, False, f"wait_for url must be tcp:// or http(s)://, got {target}")

    budget = step.timeout or 30.0
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget
    label = target or " ".join(argv)
    while True:
        if await ready():
            return StepResult(step, True, f"{label} is ready")
        if loop.time() >= deadline:
            return StepResult(step, False, f"{label} not ready after {budget}s")
        await asyncio.sleep(step.interval)


_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else [value]


def when_matches(when: dict[str, Any], env: dict[str, str]) -> bool:
    """All conditions must hold (AND). Unknown keys are rejected at load time."""
    for key, expected in when.items():
        if key == "platform":
            if PLATFORM not in [str(v).lower() for v in _as_list(expected)]:
                return False
        elif key == "exists":
            if not all(Path(_expand(str(p), env) or "").exists() for p in _as_list(expected)):
                return False
        elif key == "not_exists":
            if any(Path(_expand(str(p), env) or "").exists() for p in _as_list(expected)):
                return False
        elif key == "env":
            for k, v in dict(expected).items():
                if env.get(str(k)) != str(v):
                    return False
        elif key == "weekday":
            today = _WEEKDAYS[datetime.now().weekday()]
            if today not in [str(v)[:3].lower() for v in _as_list(expected)]:
                return False
    return True


def _spawn(argv: list[str], *, env: dict[str, str], cwd: str | None, detach: bool) -> None:
    """Launch a process fully detached so it outlives the launcher."""
    kwargs: dict = {"env": env, "cwd": cwd, "close_fds": True}
    if detach:
        if PLATFORM == "windows":
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        kwargs["stdin"] = subprocess.DEVNULL
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    subprocess.Popen(argv, **kwargs)


DISPATCH = {
    "app": _launch_app,
    "command": _run_command,
    "url": _open_url,
    "env": _apply_env,
    "kill": _kill_process,
    "wait_for": _wait_for,
}


async def _run_step(step: Step, env: dict[str, str]) -> StepResult:
    if not step.enabled:
        return StepResult(step, True, "disabled", skipped=True)
    if step.when is not None and not when_matches(step.when, env):
        return StepResult(step, True, "condition not met", skipped=True)
    handler = DISPATCH.get(step.type)
    if handler is None:
        return StepResult(step, False, f"unknown step type: {step.type}")
    result = await handler(step, env)
    attempt = 0
    while not result.ok and attempt < step.retries:
        attempt += 1
        result = await handler(step, env)
        result.detail += f" (attempt {attempt + 1}/{step.retries + 1})"
    return result


def describe_step(step: Step) -> str:
    """Human-readable one-liner of what a step *would* do (dry-run)."""
    if step.type == "app":
        args = " ".join(step.args)
        return f"launch {step.path}" + (f" {args}" if args else "")
    if step.type == "command":
        argv = step.run if isinstance(step.run, str) else " ".join(_as_argv(step.run))
        mode = "spawn" if step.detach else "run and wait for"
        return f"{mode}: {argv}"
    if step.type == "url":
        return f"open {step.url}"
    if step.type == "env":
        return f"set {list(step.set)} / unset {step.unset}"
    if step.type == "kill":
        return f"kill process {step.process}"
    if step.type == "wait":
        return f"sleep {step.seconds}s"
    if step.type == "wait_for":
        target = step.url or (step.run if isinstance(step.run, str) else " ".join(_as_argv(step.run)))
        return f"poll {target} until ready (≤{step.timeout or 30.0}s)"
    return f"unknown step type {step.type!r}"


def dry_run_profile(profile: Profile) -> list[StepResult]:
    """Describe every step without executing anything."""
    results = []
    for step in profile.steps:
        detail = describe_step(step)
        if step.when:
            detail += f" [when: {step.when}]"
        if not step.enabled:
            results.append(StepResult(step, True, f"disabled — would {detail}", skipped=True))
        else:
            results.append(StepResult(step, True, f"would {detail}"))
    return results


async def run_profile_async(profile: Profile, on_result=None) -> list[StepResult]:
    env = os.environ.copy()
    results: list[StepResult] = []
    batch: list[asyncio.Task[StepResult]] = []

    async def drain() -> None:
        if not batch:
            return
        for res in await asyncio.gather(*batch):
            results.append(res)
            if on_result:
                on_result(res)
        batch.clear()

    for step in profile.steps:
        if step.type == "wait" and step.enabled and (
            step.when is None or when_matches(step.when, env)
        ):
            await drain()
            await asyncio.sleep(step.seconds)
            res = StepResult(step, True, f"waited {step.seconds}s")
            results.append(res)
            if on_result:
                on_result(res)
            continue
        if step.parallel:
            batch.append(asyncio.create_task(_run_step(step, env)))
            continue
        await drain()
        res = await _run_step(step, env)
        results.append(res)
        if on_result:
            on_result(res)
    await drain()
    return results


def run_profile(profile: Profile, on_result=None) -> list[StepResult]:
    """Synchronous entry point — runs an asyncio loop internally.

    Must be called from a thread without a running event loop. The previous
    fallback `get_event_loop().run_until_complete(...)` could not actually
    drive a loop that was already running, so it was dead code masquerading
    as a safety net.
    """
    from launcher.state import record_run

    results = asyncio.run(run_profile_async(profile, on_result))
    record_run(profile.name)
    return results


def format_result(res: StepResult) -> str:
    if res.skipped:
        tag = "SKP"
    elif res.ok:
        tag = "OK "
    else:
        tag = "err" if res.step.optional else "ERR"
    label = res.step.name or res.step.type
    return f"[{tag}] {res.step.type}: {label} — {res.detail}"


def print_results(results: Iterable[StepResult]) -> None:
    for r in results:
        print(format_result(r), file=sys.stderr)
