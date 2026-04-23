"""Asynchronous profile executor.

Steps run sequentially by default. Consecutive steps marked `parallel: true`
are batched and launched concurrently; the batch is awaited before the next
non-parallel step (or `wait`) runs. Apps and URLs are launch-and-forget
(detached) so the launcher doesn't block on long-lived processes.
"""
from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from typing import Iterable

from launcher.config import PLATFORM, Profile, Step


@dataclass
class StepResult:
    step: Step
    ok: bool
    detail: str = ""


def _expand(value: str | None, env: dict[str, str]) -> str | None:
    if value is None:
        return None
    return os.path.expandvars(os.path.expanduser(value))


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
    except Exception as exc:  # noqa: BLE001
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
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode == 0:
            return StepResult(step, True, f"exit 0")
        return StepResult(step, False, f"exit {proc.returncode}: {stderr.decode(errors='replace').strip()}")
    except FileNotFoundError:
        return StepResult(step, False, f"not found: {argv[0]}")
    except Exception as exc:  # noqa: BLE001
        return StepResult(step, False, str(exc))


async def _open_url(step: Step, env: dict[str, str]) -> StepResult:
    if not step.url:
        return StepResult(step, False, "missing url")
    try:
        webbrowser.open(step.url, new=2)
        return StepResult(step, True, step.url)
    except Exception as exc:  # noqa: BLE001
        return StepResult(step, False, str(exc))


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
            subprocess.run(["taskkill", "/F", "/IM", step.process], capture_output=True, check=False)
        else:
            subprocess.run(["pkill", "-f", step.process], capture_output=True, check=False)
        return StepResult(step, True, f"kill {step.process}")
    except Exception as exc:  # noqa: BLE001
        return StepResult(step, False, str(exc))


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
}


async def _run_step(step: Step, env: dict[str, str]) -> StepResult:
    handler = DISPATCH.get(step.type)
    if handler is None:
        return StepResult(step, False, f"unknown step type: {step.type}")
    return await handler(step, env)


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
        if step.type == "wait":
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
    """Synchronous entry point — runs an asyncio loop internally."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run_profile_async(profile, on_result))
    # Already inside a loop (e.g. tkinter callback) — schedule and wait.
    return asyncio.get_event_loop().run_until_complete(run_profile_async(profile, on_result))


def format_result(res: StepResult) -> str:
    tag = "OK " if res.ok else "ERR"
    label = res.step.name or res.step.type
    return f"[{tag}] {res.step.type}: {label} — {res.detail}"


def print_results(results: Iterable[StepResult]) -> None:
    for r in results:
        print(format_result(r), file=sys.stderr)
