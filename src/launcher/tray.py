"""System tray integration (requires `pystray` + `Pillow`).

The menu is rebuilt whenever the profiles directory changes or a profile
starts/stops, so adding a YAML file no longer means restarting the tray.
Graceful no-op if the dependencies aren't installed.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable

from launcher.config import Profile, profile_files
from launcher.logging_setup import get_logger

log = get_logger("tray")

WATCH_SECONDS = 4.0


def _icon_image():
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (64, 64), color=(16, 18, 22))
    draw = ImageDraw.Draw(img)
    draw.rectangle([14, 14, 50, 50], outline=(106, 169, 255), width=3)
    draw.rectangle([22, 22, 42, 42], fill=(106, 169, 255))
    return img


def _profiles_signature() -> tuple:
    """Cheap fingerprint of the profiles on disk (path + mtime + size)."""
    entries = []
    for path in profile_files():
        try:
            stat = path.stat()
            entries.append((str(path), int(stat.st_mtime), stat.st_size))
        except OSError:
            continue
    return tuple(entries)


def _active_signature() -> tuple:
    from launcher import procs

    try:
        return tuple(sorted(procs.active_profiles()))
    except Exception:
        return ()


def run_tray(
    profiles: Iterable[Profile] | Callable[[], list[Profile]],
    on_run: Callable[[Profile], None],
    on_open_hud: Callable[[], None],
    on_stop: Callable[[Profile], None] | None = None,
    on_quit: Callable[[], None] | None = None,
) -> None:
    """Block on the tray event loop.

    `profiles` may be a list (legacy) or a callable re-read on every rebuild.
    """
    try:
        import pystray
    except ImportError:
        print("[tray] pystray/Pillow not installed — run `pip install .[tray]`")
        return
    try:
        image = _icon_image()
    except ImportError:
        print("[tray] Pillow not installed — run `pip install .[tray]`")
        return

    get_profiles: Callable[[], list[Profile]] = (
        profiles if callable(profiles) else (lambda: list(profiles))
    )

    def in_thread(target: Callable, *args) -> None:
        threading.Thread(target=target, args=args, daemon=True).start()

    def label(profile: Profile) -> str:
        return f"{profile.icon} {profile.name}" if profile.icon else profile.name

    def build_menu(current: list[Profile]) -> pystray.Menu:
        from launcher import procs

        try:
            active = procs.active_profiles()
        except Exception:
            active = {}

        run_items = [
            pystray.MenuItem(
                label(profile),
                (lambda p: lambda _i=None, _it=None: in_thread(on_run, p))(profile),
            )
            for profile in current
        ]

        items = [
            pystray.MenuItem(
                "Open Launcher…", lambda _i, _it: in_thread(on_open_hud), default=True
            ),
            pystray.Menu.SEPARATOR,
            *run_items,
        ]

        if active and on_stop is not None:
            by_name = {p.name: p for p in current}
            stop_items = [
                pystray.MenuItem(
                    f"{name} ({len(running)} proc)",
                    (lambda p: lambda _i=None, _it=None: in_thread(on_stop, p))(by_name[name]),
                )
                for name, running in active.items()
                if name in by_name
            ]
            if stop_items:
                items += [
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Stop", pystray.Menu(*stop_items)),
                ]

        items += [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Reload profiles", lambda _i, _it: rebuild(force=True)),
            pystray.MenuItem("Quit", lambda icon, _it: shutdown(icon)),
        ]
        return pystray.Menu(*items)

    icon = pystray.Icon(
        "profile-auto-launcher", image, "Profile Auto Launcher", build_menu(get_profiles())
    )
    state = {"profiles": _profiles_signature(), "active": _active_signature()}
    stop_event = threading.Event()

    def shutdown(tray_icon) -> None:
        stop_event.set()
        if on_quit:
            try:
                on_quit()
            except Exception:
                log.exception("quit handler failed")
        tray_icon.stop()

    def rebuild(force: bool = False) -> None:
        current = get_profiles()
        icon.menu = build_menu(current)
        icon.update_menu()
        if force:
            log.info("tray menu reloaded (%d profiles)", len(current))

    def watch() -> None:
        while not stop_event.wait(WATCH_SECONDS):
            try:
                profiles_now = _profiles_signature()
                active_now = _active_signature()
                if profiles_now == state["profiles"] and active_now == state["active"]:
                    continue
                state["profiles"], state["active"] = profiles_now, active_now
                rebuild()
            except Exception:
                log.exception("tray watcher failed")

    threading.Thread(target=watch, daemon=True, name="pal-tray-watch").start()
    icon.run()
    stop_event.set()
