"""Optional system tray integration (requires `pystray` + `Pillow`).

Graceful no-op if the dependencies aren't installed.
"""
from __future__ import annotations

import threading
from typing import Callable

from launcher.config import Profile


def run_tray(profiles: list[Profile], on_run: Callable[[Profile], None], on_open_hud: Callable[[], None]) -> None:
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        print("[tray] pystray/Pillow not installed — run `pip install .[tray]`")
        return

    img = Image.new("RGB", (64, 64), color=(16, 18, 22))
    d = ImageDraw.Draw(img)
    d.rectangle([14, 14, 50, 50], outline=(106, 169, 255), width=3)
    d.rectangle([22, 22, 42, 42], fill=(106, 169, 255))

    def make_runner(p: Profile) -> Callable[[], None]:
        return lambda _icon=None, _item=None: threading.Thread(target=on_run, args=(p,), daemon=True).start()

    def label(p: Profile) -> str:
        return f"{p.icon} {p.name}" if p.icon else p.name

    items = [pystray.MenuItem(label(p), make_runner(p)) for p in profiles]
    menu = pystray.Menu(
        pystray.MenuItem("Open Launcher…", lambda _i, _it: threading.Thread(target=on_open_hud, daemon=True).start()),
        pystray.Menu.SEPARATOR,
        *items,
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda icon, _it: icon.stop()),
    )
    icon = pystray.Icon("profile-auto-launcher", img, "Profile Auto Launcher", menu)
    icon.run()
