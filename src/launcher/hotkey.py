"""Optional global hotkey listener (requires `pynput`).

Default binding: `<alt>+<space>` opens the HUD. Override via PAL_HOTKEY.
"""
from __future__ import annotations

import os
import threading
from typing import Callable


def run_hotkey(on_trigger: Callable[[], None]) -> threading.Thread | None:
    try:
        from pynput import keyboard
    except ImportError:
        print("[hotkey] pynput not installed — run `pip install .[hotkey]`")
        return None

    combo = os.environ.get("PAL_HOTKEY", "<alt>+<space>")

    def fire() -> None:
        threading.Thread(target=on_trigger, daemon=True).start()

    def listen() -> None:
        with keyboard.GlobalHotKeys({combo: fire}) as listener:
            listener.join()

    thread = threading.Thread(target=listen, daemon=True, name="pal-hotkey")
    thread.start()
    return thread
