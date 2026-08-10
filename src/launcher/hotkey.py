"""Global hotkey listener (requires `pynput`).

Two kinds of binding are registered together: the launcher hotkey that opens
the HUD (settings `hotkey`, default `<alt>+<space>`) and any per-profile
`hotkey:` that runs a profile straight away.

A malformed or duplicate combination is logged and skipped — one bad profile
must not cost you every other shortcut.
"""
from __future__ import annotations

import threading
from typing import Callable

from launcher.logging_setup import get_logger

log = get_logger("hotkey")


def default_combo() -> str:
    from launcher import settings

    return settings.load().hotkey or "<alt>+<space>"


def run_hotkey(
    on_trigger: Callable[[], None],
    profile_bindings: dict[str, Callable[[], None]] | None = None,
) -> threading.Thread | None:
    """Start the listener thread. Returns None when pynput is unavailable."""
    try:
        from pynput import keyboard
    except ImportError:
        print("[hotkey] pynput not installed — run `pip install .[hotkey]`")
        return None

    def fire(action: Callable[[], None]) -> Callable[[], None]:
        # The pynput listener thread must not block: a profile run can take
        # minutes, and further hotkeys would queue up behind it.
        return lambda: threading.Thread(target=action, daemon=True).start()

    bindings: dict[str, Callable[[], None]] = {}
    combo = default_combo()
    bindings[combo] = fire(on_trigger)

    for binding, action in (profile_bindings or {}).items():
        if not binding:
            continue
        if binding in bindings:
            log.warning("hotkey %s is already bound — ignoring the duplicate", binding)
            continue
        bindings[binding] = fire(action)

    def listen() -> None:
        try:
            with keyboard.GlobalHotKeys(bindings) as listener:
                listener.join()
        except ValueError as exc:
            # GlobalHotKeys parses every combination up front, so one bad
            # string takes the whole map down; retry with just the launcher key.
            log.error("invalid hotkey combination (%s) — falling back to %s", exc, combo)
            try:
                with keyboard.GlobalHotKeys({combo: fire(on_trigger)}) as listener:
                    listener.join()
            except Exception:
                log.exception("hotkey listener failed to start")
        except Exception:
            log.exception("hotkey listener stopped")

    thread = threading.Thread(target=listen, daemon=True, name="pal-hotkey")
    thread.start()
    log.info("hotkeys active: %s", ", ".join(bindings))
    return thread


def describe(profiles) -> list[str]:
    """Lines for `palaunch where` / docs: which key runs what."""
    lines = [f"{default_combo():<24} open the HUD"]
    for profile in profiles:
        if profile.hotkey:
            lines.append(f"{profile.hotkey:<24} run {profile.name}")
    return lines
