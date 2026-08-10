"""Window placement for apps a profile launches.

A `window:` block on an `app`/`command` step asks the launcher to move the
window the process opens::

    window:
      monitor: 2            # 1-based; defaults to the primary display
      position: left-half   # left/right/top/bottom-half, maximized, fullscreen,
                            # center, full, or explicit x/y/width/height
      workspace: 3          # virtual desktop / space, 1-based
      match: "Visual Studio"  # title substring, when the pid owns several windows
      timeout: 10           # seconds to wait for the window to appear

Backends by session:

===========  =====================================  ==========================
Session      Geometry                               `workspace:`
===========  =====================================  ==========================
Windows      user32 (SetWindowPos/ShowWindow)       IVirtualDesktopManager
X11          wmctrl + xdotool                       wmctrl
Wayland      per compositor, see `launcher.wayland` per compositor
macOS        AppleScript / System Events            yabai, when installed
===========  =====================================  ==========================

Anything missing degrades to a logged "skipped" — a window that ends up in the
wrong place must never fail a profile.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from launcher.config import PLATFORM
from launcher.logging_setup import get_logger

log = get_logger("windows")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

NAMED_POSITIONS = frozenset(
    {
        "left-half",
        "right-half",
        "top-half",
        "bottom-half",
        "maximized",
        "fullscreen",
        "center",
        "full",
        "top-left",
        "top-right",
        "bottom-left",
        "bottom-right",
    }
)


class WindowError(RuntimeError):
    """The window could not be found or the backend is unavailable."""


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int


def _run(argv: list[str], timeout: float = 5.0) -> str:
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        creationflags=_NO_WINDOW,
    )
    if proc.returncode != 0:
        raise WindowError(f"{argv[0]} failed: {proc.stderr.strip() or proc.returncode}")
    return proc.stdout


# ── geometry ─────────────────────────────────────────────────────────────


def _target_rect(spec: dict[str, Any], screen: Rect) -> Rect | None:
    """Resolve `position:`/explicit coordinates against the chosen monitor.

    Percentages ("50%") and fractions (0.5) are relative to the monitor, so a
    profile can be written once and land sanely on any resolution.
    """
    if any(k in spec for k in ("x", "y", "width", "height")):
        return Rect(
            x=screen.x + _dimension(spec.get("x", 0), screen.width),
            y=screen.y + _dimension(spec.get("y", 0), screen.height),
            width=_dimension(spec.get("width", screen.width), screen.width),
            height=_dimension(spec.get("height", screen.height), screen.height),
        )

    position = str(spec.get("position", "")).strip().lower()
    half_w, half_h = screen.width // 2, screen.height // 2
    layouts = {
        "left-half": Rect(screen.x, screen.y, half_w, screen.height),
        "right-half": Rect(screen.x + half_w, screen.y, half_w, screen.height),
        "top-half": Rect(screen.x, screen.y, screen.width, half_h),
        "bottom-half": Rect(screen.x, screen.y + half_h, screen.width, half_h),
        "top-left": Rect(screen.x, screen.y, half_w, half_h),
        "top-right": Rect(screen.x + half_w, screen.y, half_w, half_h),
        "bottom-left": Rect(screen.x, screen.y + half_h, half_w, half_h),
        "bottom-right": Rect(screen.x + half_w, screen.y + half_h, half_w, half_h),
        "full": Rect(screen.x, screen.y, screen.width, screen.height),
        "maximized": Rect(screen.x, screen.y, screen.width, screen.height),
        "fullscreen": Rect(screen.x, screen.y, screen.width, screen.height),
        "center": Rect(
            screen.x + screen.width // 4,
            screen.y + screen.height // 4,
            half_w,
            half_h,
        ),
    }
    return layouts.get(position)


def _dimension(value: Any, extent: int) -> int:
    """Accept 800, "50%", 0.5 — the last two scale with the monitor."""
    if isinstance(value, str) and value.strip().endswith("%"):
        try:
            return int(extent * float(value.strip().rstrip("%")) / 100)
        except ValueError:
            return 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if 0 < number < 1:
        return int(extent * number)
    return int(number)


def _pick_monitor(monitors: list[Rect], spec: dict[str, Any]) -> Rect:
    index = spec.get("monitor")
    if not monitors:
        return Rect(0, 0, 1920, 1080)
    if index is None:
        return monitors[0]
    try:
        position = int(index)
    except (TypeError, ValueError):
        return monitors[0]
    if 1 <= position <= len(monitors):
        return monitors[position - 1]
    log.warning("monitor %s not found (%d available) — using the primary", index, len(monitors))
    return monitors[0]


# ── Windows backend ──────────────────────────────────────────────────────


def _monitors_windows() -> list[Rect]:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    monitors: list[Rect] = []

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    MONITORINFOF_PRIMARY = 0x1
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )
    primary: list[Rect] = []

    def on_monitor(hmonitor, _hdc, _rect, _param):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if ctypes.windll.user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            work = info.rcWork  # excludes the taskbar
            rect = Rect(work.left, work.top, work.right - work.left, work.bottom - work.top)
            if info.dwFlags & MONITORINFOF_PRIMARY:
                primary.append(rect)
            else:
                monitors.append(rect)
        return 1

    ctypes.windll.user32.EnumDisplayMonitors(None, None, callback_type(on_monitor), 0)
    return primary + monitors  # primary first, matching `monitor: 1`


def _find_window_windows(pid: int | None, match: str) -> int | None:  # pragma: no cover
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list[int] = []
    needle = match.lower()

    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def on_window(hwnd, _param):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0 and not pid:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        if needle and needle not in title.lower():
            return True
        if pid:
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value != pid:
                return True
        elif not title:
            return True
        found.append(hwnd)
        return False

    user32.EnumWindows(callback_type(on_window), 0)
    return found[0] if found else None


def _apply_windows(spec: dict[str, Any], pid: int | None, match: str) -> str:  # pragma: no cover
    import ctypes

    user32 = ctypes.windll.user32
    SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9
    SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010

    deadline = time.time() + float(spec.get("timeout", 10))
    hwnd = None
    while time.time() < deadline:
        hwnd = _find_window_windows(pid, match)
        if hwnd:
            break
        time.sleep(0.3)
    if not hwnd:
        raise WindowError("no matching window appeared")

    state = str(spec.get("state", spec.get("position", ""))).lower()
    if state == "minimized":
        user32.ShowWindow(hwnd, SW_MINIMIZE)
        return "minimized"

    user32.ShowWindow(hwnd, SW_RESTORE)
    screen = _pick_monitor(_monitors_windows(), spec)
    rect = _target_rect(spec, screen)
    if rect:
        user32.SetWindowPos(
            hwnd,
            None,
            rect.x,
            rect.y,
            rect.width,
            rect.height,
            SWP_NOZORDER | SWP_NOACTIVATE,
        )
    if state in ("maximized", "fullscreen"):
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
    if spec.get("focus"):
        user32.SetForegroundWindow(hwnd)
    detail = f"placed at {rect.x},{rect.y} {rect.width}x{rect.height}" if rect else "adjusted"
    if spec.get("workspace") is not None:
        detail += f"; {_move_to_desktop_windows(hwnd, int(spec['workspace']))}"
    return detail


# Windows exposes MoveWindowToDesktop publicly but gives no public way to
# enumerate desktops. Explorer keeps them, in display order, as concatenated
# 16-byte GUIDs under this key — undocumented, but stable since Windows 10 and
# the only route to a working `workspace: N`.
_VIRTUAL_DESKTOP_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\VirtualDesktops"
_VIRTUAL_DESKTOP_VALUE = "VirtualDesktopIDs"


def parse_desktop_guids(blob: bytes) -> list[bytes]:
    """Split the registry blob into 16-byte GUIDs, ignoring a ragged tail."""
    return [bytes(blob[i : i + 16]) for i in range(0, len(blob) - 15, 16)]


def _desktop_guids() -> list[bytes]:  # pragma: no cover - Windows only
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _VIRTUAL_DESKTOP_KEY) as key:
            blob, _kind = winreg.QueryValueEx(key, _VIRTUAL_DESKTOP_VALUE)
    except OSError as exc:
        raise WindowError(f"could not read the virtual desktop list: {exc}") from exc
    guids = parse_desktop_guids(bytes(blob))
    if not guids:
        raise WindowError("the virtual desktop list is empty")
    return guids


def _move_to_desktop_windows(hwnd: int, index: int) -> str:  # pragma: no cover - Windows only
    """Move a window to virtual desktop `index` (1-based)."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    def guid_from_string(text: str) -> GUID:
        value = GUID()
        if ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(value)):
            raise WindowError(f"bad CLSID {text}")
        return value

    guids = _desktop_guids()
    if not 1 <= index <= len(guids):
        return f"workspace {index} does not exist ({len(guids)} desktops)"

    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    try:
        clsid = guid_from_string("{AA509086-5CA9-4C25-8F95-589D3C07B48A}")
        iid = guid_from_string("{A5CD92FF-29BE-454C-8D04-D82879FB3F1B}")
        manager = ctypes.c_void_p()
        CLSCTX_LOCAL_SERVER = 0x4
        result = ole32.CoCreateInstance(
            ctypes.byref(clsid),
            None,
            CLSCTX_LOCAL_SERVER,
            ctypes.byref(iid),
            ctypes.byref(manager),
        )
        if result or not manager:
            raise WindowError(
                f"IVirtualDesktopManager unavailable (hresult 0x{result & 0xFFFFFFFF:08x})"
            )

        # IUnknown occupies slots 0-2; MoveWindowToDesktop is the third method
        # of IVirtualDesktopManager.
        vtable = ctypes.cast(manager, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        prototype = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, wintypes.HWND, ctypes.POINTER(GUID)
        )
        move = prototype(vtable[5])
        target = GUID.from_buffer_copy(guids[index - 1])
        result = move(manager, hwnd, ctypes.byref(target))
        if result:
            # E_ACCESSDENIED shows up for windows whose owning process refuses
            # cross-process moves; say so instead of reporting a silent success.
            raise WindowError(
                f"moving to desktop {index} was refused (hresult 0x{result & 0xFFFFFFFF:08x})"
            )
        return f"moved to desktop {index}"
    finally:
        ole32.CoUninitialize()


# ── X11 backend ──────────────────────────────────────────────────────────


def _monitors_linux() -> list[Rect]:
    if not shutil.which("xrandr"):
        return []
    try:
        out = _run(["xrandr", "--listmonitors"])
    except (WindowError, OSError, subprocess.SubprocessError):
        return []
    monitors: list[Rect] = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        # " 0: +*eDP-1 1920/344x1080/193+0+0  eDP-1"
        geometry = next((p for p in parts if "/" in p and "x" in p and "+" in p), None)
        if not geometry:
            continue
        try:
            size, x_off, y_off = geometry.split("+")
            width, height = (chunk.split("/")[0] for chunk in size.split("x"))
            rect = Rect(int(x_off), int(y_off), int(width), int(height))
        except (ValueError, IndexError):
            continue
        # a leading '*' marks the primary output, which must be monitor 1
        if any(p.startswith("+*") or p.startswith("*") for p in parts):
            monitors.insert(0, rect)
        else:
            monitors.append(rect)
    return monitors


def _find_window_linux(pid: int | None, match: str) -> str | None:
    try:
        out = _run(["wmctrl", "-lp"])
    except (WindowError, OSError, subprocess.SubprocessError):
        return None
    needle = match.lower()
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        win_id, _desktop, win_pid, _host, title = parts
        if pid and win_pid.isdigit() and int(win_pid) != pid:
            continue
        if needle and needle not in title.lower():
            continue
        return win_id
    return None


def _apply_linux(spec: dict[str, Any], pid: int | None, match: str) -> str:
    if not shutil.which("wmctrl"):
        raise WindowError("wmctrl is not installed (apt install wmctrl)")

    deadline = time.time() + float(spec.get("timeout", 10))
    win_id = None
    while time.time() < deadline:
        win_id = _find_window_linux(pid, match)
        if win_id:
            break
        time.sleep(0.3)
    if not win_id:
        raise WindowError("no matching window appeared")

    state = str(spec.get("state", spec.get("position", ""))).lower()
    if spec.get("workspace") is not None:
        _run(["wmctrl", "-i", "-r", win_id, "-t", str(int(spec["workspace"]) - 1)])

    # Clear existing maximization first: a maximized window ignores -e moves.
    _run(["wmctrl", "-i", "-r", win_id, "-b", "remove,maximized_vert,maximized_horz"])

    if state == "minimized":
        if shutil.which("xdotool"):
            _run(["xdotool", "windowminimize", str(int(win_id, 16))])
        return "minimized"

    detail = "adjusted"
    rect = _target_rect(spec, _pick_monitor(_monitors_linux(), spec))
    if rect and state not in ("maximized", "fullscreen"):
        _run(
            [
                "wmctrl",
                "-i",
                "-r",
                win_id,
                "-e",
                f"0,{rect.x},{rect.y},{rect.width},{rect.height}",
            ]
        )
        detail = f"placed at {rect.x},{rect.y} {rect.width}x{rect.height}"
    if state == "maximized":
        _run(["wmctrl", "-i", "-r", win_id, "-b", "add,maximized_vert,maximized_horz"])
        detail = "maximized"
    elif state == "fullscreen":
        _run(["wmctrl", "-i", "-r", win_id, "-b", "add,fullscreen"])
        detail = "fullscreen"
    if spec.get("focus"):
        _run(["wmctrl", "-i", "-a", win_id])
    return detail


# ── macOS backend ────────────────────────────────────────────────────────


def _osascript(script: str) -> str:
    return _run(["osascript", "-e", script], timeout=15)


def _monitors_darwin() -> list[Rect]:
    try:
        out = _osascript('tell application "Finder" to get bounds of window of desktop')
    except (WindowError, OSError, subprocess.SubprocessError):
        return []
    try:
        left, top, right, bottom = (int(v.strip()) for v in out.strip().split(","))
    except ValueError:
        return []
    return [Rect(left, top, right - left, bottom - top)]


def _apply_darwin(spec: dict[str, Any], pid: int | None, match: str) -> str:
    if pid is None and not match:
        raise WindowError("macOS placement needs a tracked pid or a `match:` title")

    selector = (
        f"first process whose unix id is {pid}"
        if pid
        else f'first process whose name contains "{match}"'
    )
    deadline = time.time() + float(spec.get("timeout", 10))
    while time.time() < deadline:
        try:
            count = _osascript(
                f'tell application "System Events" to count windows of ({selector})'
            ).strip()
            if count.isdigit() and int(count) > 0:
                break
        except (WindowError, OSError, subprocess.SubprocessError):
            pass
        time.sleep(0.4)
    else:
        raise WindowError("no matching window appeared")

    state = str(spec.get("state", spec.get("position", ""))).lower()
    if state == "minimized":
        _osascript(
            f'tell application "System Events" to set value of attribute "AXMinimized" '
            f"of front window of ({selector}) to true"
        )
        return "minimized"

    rect = _target_rect(spec, _pick_monitor(_monitors_darwin(), spec))
    detail = "no position requested"
    if rect is not None:
        _osascript(
            f'tell application "System Events" to tell front window of ({selector}) to '
            f"set {{position, size}} to {{{{{rect.x}, {rect.y}}}, {{{rect.width}, {rect.height}}}}}"
        )
        detail = f"placed at {rect.x},{rect.y} {rect.width}x{rect.height}"
    if spec.get("workspace") is not None:
        detail += f"; {_move_to_space_darwin(pid, match, int(spec['workspace']))}"
    return detail


def yabai_window_id(windows_json: Any, pid: int | None, match: str) -> int | None:
    """Window id from `yabai -m query --windows` output."""
    needle = match.lower()
    for window in windows_json or []:
        if not isinstance(window, dict):
            continue
        if pid and window.get("pid") != pid:
            continue
        if needle and needle not in str(window.get("title", "")).lower():
            continue
        window_id = window.get("id")
        if isinstance(window_id, int):
            return window_id
    return None


def _move_to_space_darwin(pid: int | None, match: str, index: int) -> str:
    """Move a window to a Space.

    macOS has no public API for this — Mission Control is not scriptable and
    even Apple's own shortcuts only switch Spaces, they don't move other
    applications' windows. `yabai` is the established way to do it, so we drive
    that when it is installed and say plainly what is missing when it is not.
    """
    if not shutil.which("yabai"):
        raise WindowError(
            "moving windows between Spaces needs yabai (brew install koekeishiya/formulae/yabai) "
            "— macOS exposes no public Spaces API"
        )
    try:
        payload = json.loads(_run(["yabai", "-m", "query", "--windows"], timeout=10))
    except (ValueError, WindowError, OSError) as exc:
        raise WindowError(f"yabai query failed: {exc}") from exc
    window_id = yabai_window_id(payload, pid, match)
    if window_id is None:
        raise WindowError("yabai does not know about that window")
    _run(["yabai", "-m", "window", str(window_id), "--space", str(index)], timeout=10)
    return f"moved to space {index}"


# ── entry point ──────────────────────────────────────────────────────────


def apply(spec: dict[str, Any], pid: int | None = None, match: str = "") -> str:
    """Place a window. Raises WindowError when it cannot be done."""
    from launcher import settings

    if not settings.load().window_management:
        return "window management disabled in settings"
    match = str(spec.get("match", match) or "")
    try:
        if PLATFORM == "windows":
            return _apply_windows(spec, pid, match)
        if PLATFORM == "darwin":
            return _apply_darwin(spec, pid, match)
        from launcher import wayland

        # X11 apps running under XWayland are still reachable through wmctrl,
        # but we cannot tell which those are before looking, and the compositor
        # backend is the one that works for native Wayland clients.
        if wayland.is_wayland():
            return wayland.apply(spec, pid, match)
        return _apply_linux(spec, pid, match)
    except WindowError:
        raise
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise WindowError(str(exc)) from exc


def backend_name() -> str:
    """Which placement backend this session will use — surfaced by `palaunch where`."""
    if PLATFORM == "windows":
        return "win32 (user32 + IVirtualDesktopManager)"
    if PLATFORM == "darwin":
        return "AppleScript" + (" + yabai" if shutil.which("yabai") else " (no yabai: no Spaces)")
    from launcher import wayland

    if wayland.is_wayland():
        which = wayland.compositor()
        return f"wayland/{which}" if which else "wayland (unsupported compositor)"
    return "x11 (wmctrl)" if shutil.which("wmctrl") else "x11 (wmctrl missing)"
