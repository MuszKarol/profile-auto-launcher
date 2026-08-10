"""Asynchronous profile executor.

Steps form a dependency graph. Without an explicit `depends_on`, edges are
derived from the classic layout rules: a normal step waits for everything
before it, and consecutive `parallel: true` steps form a batch that runs
concurrently. `depends_on` overrides those defaults and is the only case where
a failed dependency cascades into skipping its dependents — implicit edges
must not turn one broken step into a dead profile.

Apps and URLs are launch-and-forget (detached) so the launcher doesn't block
on long-lived processes; pids of tracked spawns are recorded so `palaunch
stop` can close what a profile opened.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from launcher import conditions, interp
from launcher.config import PLATFORM, Profile, Step
from launcher.logging_setup import get_logger

log = get_logger("executor")

_POSIX_VAR = re.compile(r"\$(\w+|\{[^}]*\})")
_WIN_VAR = re.compile(r"%([^%]+)%")

# When the launcher runs windowless (palaunchw / pythonw), a console child
# spawned without this flag pops up its own console window. Inherited stdio
# handles still work, so output capture is unaffected. 0 on non-Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

MAX_PROFILE_DEPTH = 5  # guards `type: profile` recursion


@dataclass
class StepResult:
    step: Step
    ok: bool
    detail: str = ""
    skipped: bool = False
    duration: float = 0.0
    attempts: int = 1

    @property
    def counts_as_failure(self) -> bool:
        return not self.ok and not self.step.optional


@dataclass
class RunContext:
    """Everything a handler needs: mutable env, interpolation, bookkeeping."""

    profile: Profile
    env: dict[str, str] = field(default_factory=dict)
    ctx: interp.Context = field(default_factory=interp.Context)
    track: bool = True
    depth: int = 0
    chain: tuple[str, ...] = ()  # profile names already on the stack

    def scrub(self, text: str) -> str:
        return interp.scrub(text, self.ctx)


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


def _value(raw: str | None, run: RunContext) -> str | None:
    """`{{ template }}` first, then shell-style variable expansion."""
    if raw is None:
        return None
    return _expand(interp.render(raw, run.ctx), run.env)


def _values(raw: Iterable[str], run: RunContext) -> list[str]:
    return [_value(str(item), run) or "" for item in raw]


def _as_argv(run_spec: list[str] | str | None) -> list[str]:
    if run_spec is None:
        return []
    if isinstance(run_spec, list):
        return [str(x) for x in run_spec]
    return shlex.split(run_spec, posix=(PLATFORM != "windows"))


# ── process spawning ─────────────────────────────────────────────────────


def _spawn(
    argv: list[str], *, env: dict[str, str], cwd: str | None, detach: bool
) -> subprocess.Popen:
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
    return subprocess.Popen(argv, **kwargs)


def _track(run: RunContext, step: Step, proc: subprocess.Popen, argv: list[str]) -> None:
    if not (run.track and step.track):
        return
    from launcher import procs

    procs.record(run.profile.name, proc.pid, step.label, run.scrub(" ".join(argv))[:300])


async def _place_window(step: Step, run: RunContext, pid: int | None) -> str:
    if not step.window:
        return ""
    from launcher import windows

    spec = interp.render_any(step.window, run.ctx)
    try:
        detail = await asyncio.to_thread(windows.apply, spec, pid, step.name)
        return f"; window: {detail}"
    except windows.WindowError as exc:
        log.warning("window placement for %s failed: %s", step.label, exc)
        return f"; window skipped ({exc})"


# ── handlers ─────────────────────────────────────────────────────────────


async def _launch_app(step: Step, run: RunContext) -> StepResult:
    path = _value(step.path, run)
    if not path:
        return StepResult(step, False, "missing path")
    argv = [path, *_values(step.args, run)]
    try:
        proc = _spawn(argv, env=run.env, cwd=_value(step.cwd, run), detach=step.detach)
    except FileNotFoundError:
        return StepResult(step, False, f"not found: {path}")
    except OSError as exc:
        return StepResult(step, False, str(exc))
    _track(run, step, proc, argv)
    detail = f"launched {path} (pid {proc.pid})"
    return StepResult(step, True, detail + await _place_window(step, run, proc.pid))


async def _run_command(step: Step, run: RunContext) -> StepResult:
    argv = _values(_as_argv(step.run), run)
    argv += _values(step.args, run)
    if not argv:
        return StepResult(step, False, "empty run")
    return await _exec_argv(step, run, argv)


async def _exec_argv(step: Step, run: RunContext, argv: list[str]) -> StepResult:
    cwd = _value(step.cwd, run)
    try:
        if step.detach:
            proc = _spawn(argv, env=run.env, cwd=cwd, detach=True)
            _track(run, step, proc, argv)
            detail = f"spawned {argv[0]} (pid {proc.pid})"
            return StepResult(step, True, detail + await _place_window(step, run, proc.pid))
        process = await asyncio.create_subprocess_exec(
            *argv,
            env=run.env,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=_NO_WINDOW,
        )
        try:
            if step.timeout:
                stdout, stderr = await asyncio.wait_for(process.communicate(), step.timeout)
            else:
                stdout, stderr = await process.communicate()
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return StepResult(step, False, f"timed out after {step.timeout}s")
        if process.returncode == 0:
            tail = stdout.decode(errors="replace").strip().splitlines()
            suffix = f": {tail[-1][:160]}" if tail else ""
            return StepResult(step, True, run.scrub(f"exit 0{suffix}"))
        message = stderr.decode(errors="replace").strip() or stdout.decode(errors="replace").strip()
        return StepResult(step, False, run.scrub(f"exit {process.returncode}: {message[:400]}"))
    except FileNotFoundError:
        return StepResult(step, False, f"not found: {argv[0]}")
    except OSError as exc:
        return StepResult(step, False, str(exc))


def _shell_argv(shell: str, script_path: str) -> list[str] | None:
    if shell == "auto":
        shell = "powershell" if PLATFORM == "windows" else "bash"
    if shell in ("bash", "sh", "zsh"):
        interpreter = shutil.which(shell) or shutil.which("sh")
        return [interpreter, script_path] if interpreter else None
    if shell == "powershell":
        interpreter = shutil.which("pwsh") or shutil.which("powershell")
        if not interpreter:
            return None
        return [interpreter, "-NoProfile", "-NonInteractive", "-File", script_path]
    if shell == "cmd":
        return ["cmd.exe", "/c", script_path]
    return None


_SHELL_SUFFIX = {"powershell": ".ps1", "cmd": ".bat"}


async def _run_script(step: Step, run: RunContext) -> StepResult:
    """Inline shell/PowerShell written straight into the YAML.

    The body goes to a temp file rather than `-c`: multi-line scripts survive
    quoting intact, and the file is removed once the step finishes.
    """
    body = _value(step.script, run)
    if not body:
        return StepResult(step, False, "empty script")
    shell = (
        step.shell if step.shell != "auto" else ("powershell" if PLATFORM == "windows" else "bash")
    )
    suffix = _SHELL_SUFFIX.get(shell, ".sh")

    import tempfile

    # delete=False on purpose: the interpreter opens the path by name after we
    # close it, so the file has to outlive the handle. The `finally` unlinks it.
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
        "w", suffix=suffix, delete=False, encoding="utf-8", newline="\n"
    )
    try:
        handle.write(body if body.endswith("\n") else body + "\n")
        handle.close()
        if PLATFORM != "windows":
            os.chmod(handle.name, 0o700)
        argv = _shell_argv(shell, handle.name)
        if argv is None:
            return StepResult(step, False, f"no interpreter available for shell '{shell}'")
        # A detached script would race the temp-file cleanup below, so scripts
        # always run to completion; use `type: command` for fire-and-forget.
        if step.detach:
            log.debug("script step %s: detach ignored, running inline", step.label)
        original_detach, step.detach = step.detach, False
        try:
            return await _exec_argv(step, run, argv)
        finally:
            step.detach = original_detach
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


_ALLOWED_URL_SCHEMES = frozenset({"http", "https", "mailto", "ftp", "ftps"})


def _is_safe_url(url: str) -> bool:
    """Reject schemes that webbrowser.open would happily resolve to local files
    or shell-handled URIs (file://, javascript:, data:, custom protocols)."""
    scheme = url.split(":", 1)[0].lower() if ":" in url else ""
    return scheme in _ALLOWED_URL_SCHEMES


async def _open_url(step: Step, run: RunContext) -> StepResult:
    url = _value(step.url, run)
    if not url:
        return StepResult(step, False, "missing url")
    if not _is_safe_url(url):
        return StepResult(step, False, f"refused unsafe url scheme: {url}")
    try:
        webbrowser.open(url, new=2)
        return StepResult(step, True, run.scrub(url))
    except (OSError, webbrowser.Error) as exc:
        return StepResult(step, False, str(exc))


async def _apply_env(step: Step, run: RunContext) -> StepResult:
    for key, value in step.set.items():
        run.env[key] = _value(str(value), run) or ""
    for key in step.unset:
        run.env.pop(key, None)
    run.ctx.env = run.env
    return StepResult(step, True, f"set={list(step.set)} unset={step.unset}")


async def _kill_process(step: Step, run: RunContext) -> StepResult:
    process = _value(step.process, run)
    if not process:
        return StepResult(step, False, "missing process")
    try:
        if PLATFORM == "windows":
            subprocess.run(
                ["taskkill", "/F", "/IM", process],
                capture_output=True,
                check=False,
                creationflags=_NO_WINDOW,
            )
        else:
            # `-x` requires whole-name match; re.escape neuters regex metacharacters
            # so a profile entry like `process: .` cannot accidentally match every
            # 1-char process name. Without these flags, pkill's default substring +
            # regex match against the full command line is alarmingly broad.
            subprocess.run(
                ["pkill", "-x", re.escape(process)],
                capture_output=True,
                check=False,
            )
        return StepResult(step, True, f"kill {process}")
    except OSError as exc:
        return StepResult(step, False, str(exc))


async def _wait(step: Step, run: RunContext) -> StepResult:
    await asyncio.sleep(step.seconds)
    return StepResult(step, True, f"waited {step.seconds}s")


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
            *argv,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
        return await proc.wait() == 0
    except (OSError, FileNotFoundError):
        return False


async def _wait_for(step: Step, run: RunContext) -> StepResult:
    """Poll a tcp/http endpoint or a command until it's ready (or timeout)."""
    target = _value(step.url, run)
    argv = _values(_as_argv(step.run), run)
    if not target and not argv:
        return StepResult(step, False, "wait_for needs url: or run:")
    if target and target.split(":", 1)[0].lower() not in ("tcp", "http", "https"):
        return StepResult(step, False, f"wait_for url must be tcp:// or http(s)://, got {target}")

    async def ready() -> bool:
        if target:
            scheme = target.split(":", 1)[0].lower()
            if scheme == "tcp":
                hostport = target[len("tcp://") :]
                host, _, port = hostport.rpartition(":")
                if not host or not port.isdigit():
                    return False
                return await _probe_tcp(host, int(port))
            return await asyncio.to_thread(_probe_http, target)
        return await _probe_command(argv, run.env)

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


async def _run_nested_profile(step: Step, run: RunContext) -> StepResult:
    from launcher.config import find_profile

    name = _value(step.profile, run)
    if not name:
        return StepResult(step, False, "missing profile name")
    if run.depth >= MAX_PROFILE_DEPTH:
        return StepResult(step, False, f"profile nesting deeper than {MAX_PROFILE_DEPTH}")
    if name.lower() in {n.lower() for n in run.chain}:
        return StepResult(step, False, f"profile cycle: {' -> '.join([*run.chain, name])}")
    nested = find_profile(name)
    if nested is None:
        return StepResult(step, False, f"profile '{name}' not found")

    child = RunContext(
        profile=nested,
        env=run.env,  # shared: an `env` step in the child is visible afterwards
        ctx=interp.Context.for_profile(nested, run.env),
        track=run.track,
        depth=run.depth + 1,
        chain=(*run.chain, run.profile.name),
    )
    child.ctx.secrets_used = run.ctx.secrets_used
    results = await _execute_steps(nested.steps, child)
    failures = [r for r in results if r.counts_as_failure]
    detail = f"{nested.name}: {len(results) - len(failures)} ok, {len(failures)} failed"
    if failures:
        # Surface the first reason: otherwise a nested failure reads as a bare
        # count and you have to go digging through the log to learn why.
        detail += f" — {failures[0].step.label}: {failures[0].detail}"
    return StepResult(step, not failures, detail)


async def _notify_step(step: Step, run: RunContext) -> StepResult:
    from launcher.notify import notify

    title = _value(step.title, run) or run.profile.name
    message = _value(step.message, run) or ""
    await asyncio.to_thread(notify, title, message)
    return StepResult(step, True, run.scrub(f"{title} — {message}"[:200]))


def _http_request(
    step: Step, url: str, headers: dict[str, str], body: bytes | None, timeout: float
):
    request = urllib.request.Request(url, data=body, method=step.method, headers=headers)
    return urllib.request.urlopen(request, timeout=timeout)


async def _http_step(step: Step, run: RunContext) -> StepResult:
    url = _value(step.url, run)
    if not url:
        return StepResult(step, False, "missing url")
    scheme = url.split(":", 1)[0].lower()
    if scheme not in ("http", "https"):
        return StepResult(step, False, f"refused non-http url: {url}")

    headers = {k: _value(v, run) or "" for k, v in step.headers.items()}
    payload = interp.render_any(step.body, run.ctx)
    body: bytes | None = None
    if payload is not None:
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        else:
            body = str(payload).encode("utf-8")
    expected = step.expect_status or []
    timeout = step.timeout or 15.0

    def call() -> tuple[int, str]:
        try:
            with _http_request(step, url, headers, body, timeout) as resp:
                return resp.status, resp.read(400).decode(errors="replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.reason or ""

    try:
        status, snippet = await asyncio.to_thread(call)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return StepResult(step, False, run.scrub(f"{step.method} {url}: {exc}"))
    ok = status in expected if expected else 200 <= status < 400
    detail = run.scrub(f"{step.method} {url} -> {status} {snippet.strip()[:120]}")
    return StepResult(step, ok, detail)


async def _plugin_step(step: Step, run: RunContext) -> StepResult:
    """Run an external executable with a JSON request on stdin.

    Request:  {"profile": …, "step": …, "config": {...}, "env": {...}}
    Response: {"ok": true, "detail": "...", "env": {"KEY": "value"}}

    A plugin that prints nothing but exits 0 counts as success — the JSON
    contract is optional, so a plain script works as a plugin too.
    """
    executable = _value(step.plugin, run)
    if not executable:
        return StepResult(step, False, "missing plugin")
    resolved = shutil.which(executable) or executable
    if not Path(resolved).is_file():
        return StepResult(step, False, f"plugin not found: {executable}")

    request = json.dumps(
        {
            "profile": run.profile.name,
            "step": step.label,
            "config": interp.render_any(step.config, run.ctx),
            "env": dict(run.env),
        }
    ).encode("utf-8")

    try:
        proc = await asyncio.create_subprocess_exec(
            resolved,
            *_values(step.args, run),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=run.env,
            cwd=_value(step.cwd, run),
            creationflags=_NO_WINDOW,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(request), step.timeout or 60.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return StepResult(step, False, f"plugin timed out after {step.timeout or 60.0}s")
    except (OSError, FileNotFoundError) as exc:
        return StepResult(step, False, str(exc))

    text = stdout.decode(errors="replace").strip()
    ok = proc.returncode == 0
    detail = text or stderr.decode(errors="replace").strip() or f"exit {proc.returncode}"
    try:
        response = json.loads(text) if text.startswith("{") else None
    except ValueError:
        response = None
    if isinstance(response, dict):
        ok = bool(response.get("ok", ok))
        detail = str(response.get("detail", detail))
        for key, value in (response.get("env") or {}).items():
            run.env[str(key)] = str(value)
    return StepResult(step, ok, run.scrub(detail[:400]))


async def _file_step(step: Step, run: RunContext) -> StepResult:
    """copy / symlink / mkdir / remove / write / append — config swapping."""
    action = (step.action or "").lower()
    src = _value(step.src, run)
    dest = _value(step.dest, run)
    content = _value(step.content, run)

    def perform() -> str:
        if action == "mkdir":
            if not dest:
                raise ValueError("mkdir needs dest")
            Path(dest).mkdir(parents=True, exist_ok=True)
            return f"mkdir {dest}"
        if action == "remove":
            if not dest:
                raise ValueError("remove needs dest")
            target = Path(dest)
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif target.exists() or target.is_symlink():
                target.unlink()
            else:
                return f"{dest} already absent"
            return f"removed {dest}"
        if action in ("write", "append"):
            if not dest:
                raise ValueError(f"{action} needs dest")
            target = Path(dest)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a" if action == "append" else "w", encoding="utf-8") as fh:
                fh.write(content or "")
            return f"{action} {len(content or '')} chars -> {dest}"
        if not src or not dest:
            raise ValueError(f"{action} needs src and dest")
        source, target = Path(src), Path(dest)
        if not source.exists():
            raise ValueError(f"src does not exist: {src}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if action == "copy":
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                shutil.copy2(source, target)
            return f"copied {src} -> {dest}"
        if action == "symlink":
            # Replace an existing link/file so re-running a profile is idempotent.
            if target.is_symlink() or target.exists():
                if target.is_dir() and not target.is_symlink():
                    raise ValueError(f"refusing to replace directory {dest}")
                target.unlink()
            target.symlink_to(source, target_is_directory=source.is_dir())
            return f"linked {dest} -> {src}"
        raise ValueError(f"unsupported action {action!r}")

    try:
        return StepResult(step, True, await asyncio.to_thread(perform))
    except (OSError, ValueError, shutil.Error) as exc:
        return StepResult(step, False, str(exc))


DISPATCH: dict[str, Callable[[Step, RunContext], Any]] = {
    "app": _launch_app,
    "command": _run_command,
    "script": _run_script,
    "url": _open_url,
    "env": _apply_env,
    "kill": _kill_process,
    "wait": _wait,
    "wait_for": _wait_for,
    "profile": _run_nested_profile,
    "notify": _notify_step,
    "http": _http_step,
    "plugin": _plugin_step,
    "file": _file_step,
}


# ── conditions ───────────────────────────────────────────────────────────


def when_matches(when: dict[str, Any], env: dict[str, str]) -> bool:
    """Backwards-compatible wrapper around :mod:`launcher.conditions`."""
    return conditions.matches(when, env, expand=_expand)


# ── step execution ───────────────────────────────────────────────────────


async def _attempt(step: Step, run: RunContext) -> StepResult:
    handler = DISPATCH.get(step.type)
    if handler is None:
        return StepResult(step, False, f"unknown step type: {step.type}")
    try:
        return await handler(step, run)
    except interp.InterpolationError as exc:
        return StepResult(step, False, str(exc))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # a broken handler must not kill the whole run
        log.exception("step %s crashed", step.label)
        return StepResult(step, False, f"internal error: {exc}")


async def _run_step(
    step: Step, run: RunContext, on_result: Callable[[StepResult], None] | None = None
) -> StepResult:
    if not step.enabled:
        return StepResult(step, True, "disabled", skipped=True)
    if step.when is not None and not conditions.matches(step.when, run.env, expand=_expand):
        return StepResult(step, True, "condition not met", skipped=True)

    started = time.monotonic()
    result = await _attempt(step, run)
    attempt = 0
    delay = step.retry_delay
    while not result.ok and attempt < step.retries:
        attempt += 1
        if delay > 0:
            await asyncio.sleep(delay)
            delay *= 2  # exponential backoff, seeded by retry_delay
        result = await _attempt(step, run)
        result.detail += f" (attempt {attempt + 1}/{step.retries + 1})"
    result.duration = time.monotonic() - started
    result.attempts = attempt + 1

    if not result.ok and step.on_failure:
        log.info(
            "step %s failed — running %d compensation step(s)", step.label, len(step.on_failure)
        )
        recovery = await _execute_steps(step.on_failure, run, on_result)
        healed = sum(1 for r in recovery if r.counts_as_failure)
        result.detail += f" [on_failure: {len(recovery) - healed}/{len(recovery)} ok]"

    level = log.info if result.ok else log.warning
    level("%s | %s: %s", run.profile.name, step.label, run.scrub(result.detail))
    return result


def _build_dependencies(steps: list[Step]) -> list[list[int]]:
    """Derive the edge list. Explicit `depends_on` wins; otherwise a normal
    step waits for every earlier step and a `parallel` step waits only for
    what came before its batch."""
    index_of = {step.id: i for i, step in enumerate(steps) if step.id}
    deps: list[list[int]] = []
    batch_start = 0
    for i, step in enumerate(steps):
        if step.depends_on:
            deps.append([index_of[d] for d in step.depends_on if d in index_of])
        elif step.parallel:
            deps.append(list(range(batch_start)))
        else:
            deps.append(list(range(i)))
        if not step.parallel:
            batch_start = i + 1
    return deps


def _semaphore() -> asyncio.Semaphore | None:
    from launcher import settings

    limit = settings.load().max_parallel
    return asyncio.Semaphore(limit) if limit and limit > 0 else None


async def _execute_steps(
    steps: list[Step],
    run: RunContext,
    on_result: Callable[[StepResult], None] | None = None,
) -> list[StepResult]:
    if not steps:
        return []
    deps = _build_dependencies(steps)
    total = len(steps)
    results: list[StepResult | None] = [None] * total
    done = [False] * total
    pending: dict[asyncio.Task, int] = {}
    ordered: list[StepResult] = []
    gate = _semaphore()

    async def run_indexed(i: int) -> StepResult:
        step = steps[i]
        blocked = [d for d in deps[i] if results[d] is not None and results[d].counts_as_failure]
        # Only explicit edges cascade — implicit ones would turn any single
        # failure into a skipped remainder, which is not what a launcher wants.
        if step.depends_on and blocked:
            return StepResult(
                step,
                True,
                f"skipped — '{steps[blocked[0]].label}' failed",
                skipped=True,
            )
        if gate is None:
            return await _run_step(step, run, on_result)
        async with gate:
            return await _run_step(step, run, on_result)

    while not all(done):
        for i in range(total):
            if done[i] or i in pending.values():
                continue
            if all(done[d] for d in deps[i]):
                pending[asyncio.create_task(run_indexed(i))] = i
        if not pending:
            for i in range(total):
                if not done[i]:
                    result = StepResult(
                        steps[i], False, "dependency cycle — step never became runnable"
                    )
                    results[i] = result
                    done[i] = True
                    ordered.append(result)
                    if on_result:
                        on_result(result)
            break
        finished, _ = await asyncio.wait(set(pending), return_when=asyncio.FIRST_COMPLETED)
        for task in finished:
            index = pending.pop(task)
            result = task.result()
            results[index] = result
            done[index] = True
            ordered.append(result)
            if on_result:
                on_result(result)
    return ordered


# ── dry run ──────────────────────────────────────────────────────────────


def describe_step(step: Step) -> str:
    """Human-readable one-liner of what a step *would* do (dry-run)."""
    if step.type == "app":
        args = " ".join(step.args)
        return f"launch {step.path}" + (f" {args}" if args else "")
    if step.type == "command":
        argv = step.run if isinstance(step.run, str) else " ".join(_as_argv(step.run))
        mode = "spawn" if step.detach else "run and wait for"
        return f"{mode}: {argv}"
    if step.type == "script":
        first = (step.script or "").strip().splitlines()
        head = first[0][:60] if first else ""
        extra = f" (+{len(first) - 1} more lines)" if len(first) > 1 else ""
        return f"run {step.shell} script: {head}{extra}"
    if step.type == "url":
        return f"open {step.url}"
    if step.type == "env":
        return f"set {list(step.set)} / unset {step.unset}"
    if step.type == "kill":
        return f"kill process {step.process}"
    if step.type == "wait":
        return f"sleep {step.seconds}s"
    if step.type == "wait_for":
        target = step.url or (
            step.run if isinstance(step.run, str) else " ".join(_as_argv(step.run))
        )
        return f"poll {target} until ready (≤{step.timeout or 30.0}s)"
    if step.type == "profile":
        return f"run nested profile '{step.profile}'"
    if step.type == "notify":
        return f"notify: {step.title or ''} {step.message or ''}".strip()
    if step.type == "http":
        return f"{step.method} {step.url}"
    if step.type == "plugin":
        return f"invoke plugin {step.plugin}"
    if step.type == "file":
        if step.action in ("write", "append", "mkdir", "remove"):
            return f"{step.action} {step.dest}"
        return f"{step.action} {step.src} -> {step.dest}"
    return f"unknown step type {step.type!r}"


def dry_run_profile(profile: Profile, steps: list[Step] | None = None) -> list[StepResult]:
    """Describe every step without executing anything."""
    results = []
    for step in steps if steps is not None else profile.steps:
        detail = describe_step(step)
        if step.window:
            detail += f" [window: {step.window}]"
        if step.when:
            detail += f" [when: {step.when}]"
        if step.depends_on:
            detail += f" [after: {', '.join(step.depends_on)}]"
        if not step.enabled:
            results.append(StepResult(step, True, f"disabled — would {detail}", skipped=True))
        else:
            results.append(StepResult(step, True, f"would {detail}"))
    return results


# ── entry points ─────────────────────────────────────────────────────────


def _filter_steps(
    steps: list[Step], only: Iterable[str] = (), skip: Iterable[str] = ()
) -> list[Step]:
    """`--only` / `--skip` match a step's name or id, case-insensitively.

    Dependencies are *not* pulled in automatically: when you ask for one step,
    you get that step. Explicit `depends_on` edges pointing outside the
    selection are dropped so the remaining graph still runs.
    """
    only_set = {s.lower() for s in only}
    skip_set = {s.lower() for s in skip}
    if not only_set and not skip_set:
        return steps

    def identifiers(step: Step) -> set[str]:
        return {v.lower() for v in (step.name, step.id, step.type) if v}

    chosen = [
        step
        for step in steps
        if (not only_set or identifiers(step) & only_set) and not (identifiers(step) & skip_set)
    ]
    surviving_ids = {step.id for step in chosen if step.id}
    for step in chosen:
        step.depends_on = [d for d in step.depends_on if d in surviving_ids]
    return chosen


def _new_context(profile: Profile, track: bool = True) -> RunContext:
    env = os.environ.copy()
    run = RunContext(profile=profile, env=env, track=track)
    run.ctx = interp.Context.for_profile(profile, env)
    return run


async def run_profile_async(
    profile: Profile,
    on_result: Callable[[StepResult], None] | None = None,
    steps: list[Step] | None = None,
    track: bool = True,
) -> list[StepResult]:
    run = _new_context(profile, track=track)
    return await _execute_steps(steps if steps is not None else profile.steps, run, on_result)


def run_profile(
    profile: Profile,
    on_result: Callable[[StepResult], None] | None = None,
    only: Iterable[str] = (),
    skip: Iterable[str] = (),
) -> list[StepResult]:
    """Synchronous entry point — runs an asyncio loop internally.

    Must be called from a thread without a running event loop.
    """
    from launcher.logging_setup import setup
    from launcher.state import record_run

    setup()
    steps = _filter_steps(list(profile.steps), only, skip)
    log.info("running profile '%s' (%d steps)", profile.name, len(steps))
    started = time.monotonic()
    results = asyncio.run(run_profile_async(profile, on_result, steps))
    duration = time.monotonic() - started
    record_run(profile.name, results, duration)
    failed = sum(1 for r in results if r.counts_as_failure)
    log.info(
        "profile '%s' finished in %.2fs — %d ok, %d failed",
        profile.name,
        duration,
        len(results) - failed,
        failed,
    )
    return results


def run_teardown(
    profile: Profile, on_result: Callable[[StepResult], None] | None = None
) -> list[StepResult]:
    """Run the profile's `teardown:` steps (nothing is tracked)."""
    from launcher.logging_setup import setup
    from launcher.state import record_run

    setup()
    if not profile.teardown:
        return []
    started = time.monotonic()
    results = asyncio.run(
        run_profile_async(profile, on_result, steps=profile.teardown, track=False)
    )
    record_run(profile.name, results, time.monotonic() - started, kind="teardown")
    return results


def stop_profile(
    profile: Profile, on_result: Callable[[StepResult], None] | None = None
) -> tuple[list[StepResult], int, int]:
    """Teardown steps first, then terminate whatever the profile launched."""
    from launcher import procs

    results = run_teardown(profile, on_result)
    stopped, stubborn = procs.stop_profile(profile.name)
    log.info(
        "stopped profile '%s': %d processes ended, %d survived", profile.name, stopped, stubborn
    )
    return results, stopped, stubborn


def format_result(res: StepResult) -> str:
    if res.skipped:
        tag = "SKP"
    elif res.ok:
        tag = "OK "
    else:
        tag = "err" if res.step.optional else "ERR"
    timing = f" ({res.duration:.1f}s)" if res.duration >= 0.05 else ""
    return f"[{tag}] {res.step.type}: {res.step.label} — {res.detail}{timing}"


def print_results(results: Iterable[StepResult]) -> None:
    for r in results:
        print(format_result(r), file=sys.stderr)
