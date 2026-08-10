"""Window placement on Wayland.

Wayland deliberately gives no protocol for one client to move another client's
window, so there is no single backend the way `wmctrl` covers X11. What exists
instead is a control channel per compositor, and those do the job:

* **Sway** (and other i3-compatible wlroots compositors) — `swaymsg` IPC
* **Hyprland** — `hyprctl`
* **KWin** (Plasma Wayland) — a KWin script loaded over D-Bus

**GNOME/Mutter** has no equivalent: `org.gnome.Shell.Eval` is disabled on
release builds and window control needs a signed extension. Rather than
pretend, that case raises a `WindowError` explaining the two ways out (run the
app under XWayland, or install a placement extension).

Each backend matches the window by pid, which is what the launcher already
knows from spawning it, and falls back to a title substring via `match:`.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from typing import Any

from launcher.logging_setup import get_logger
from launcher.windows import Rect, WindowError, _pick_monitor, _run, _target_rect

log = get_logger("wayland")

# Compositors we can actually drive, in detection order.
SWAY = "sway"
HYPRLAND = "hyprland"
KWIN = "kwin"
GNOME = "gnome"


def is_wayland() -> bool:
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return True
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def compositor() -> str:
    """Which compositor we are talking to, or '' when we cannot tell."""
    if os.environ.get("SWAYSOCK") or os.environ.get("I3SOCK"):
        return SWAY
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return HYPRLAND
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "sway" in desktop:
        return SWAY
    if "hyprland" in desktop:
        return HYPRLAND
    if "kde" in desktop or "plasma" in desktop:
        return KWIN
    if "gnome" in desktop:
        return GNOME
    return ""


# ── Sway ─────────────────────────────────────────────────────────────────


def _sway_nodes(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the sway tree; containers hide in `nodes` and `floating_nodes`."""
    found: list[dict[str, Any]] = []
    stack = [tree]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        found.append(node)
        for key in ("nodes", "floating_nodes"):
            children = node.get(key)
            if isinstance(children, list):
                stack.extend(children)
    return found


def sway_find(tree: dict[str, Any], pid: int | None, match: str) -> int | None:
    """Container id of the first window owned by `pid` / matching `match`."""
    needle = match.lower()
    for node in _sway_nodes(tree):
        if node.get("type") not in ("con", "floating_con"):
            continue
        if node.get("app_id") is None and node.get("window_properties") is None:
            continue  # a split container, not a real window
        if pid and node.get("pid") != pid:
            continue
        if needle and needle not in str(node.get("name", "")).lower():
            continue
        node_id = node.get("id")
        if isinstance(node_id, int):
            return node_id
    return None


def sway_monitors(outputs: list[dict[str, Any]]) -> list[Rect]:
    monitors: list[Rect] = []
    for output in outputs:
        if not output.get("active", True):
            continue
        rect = output.get("rect") or {}
        try:
            geometry = Rect(int(rect["x"]), int(rect["y"]), int(rect["width"]), int(rect["height"]))
        except (KeyError, TypeError, ValueError):
            continue
        if output.get("primary") or output.get("focused"):
            monitors.insert(0, geometry)
        else:
            monitors.append(geometry)
    return monitors


def _sway_query(what: str) -> Any:
    raw = _run(["swaymsg", "-t", what, "-r"])
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise WindowError(f"swaymsg returned invalid JSON: {exc}") from exc


def _sway_command(selector: str, command: str) -> None:
    _run(["swaymsg", f"[con_id={selector}] {command}"])


def _apply_sway(spec: dict[str, Any], pid: int | None, match: str) -> str:
    if not shutil.which("swaymsg"):
        raise WindowError("swaymsg is not installed")

    con_id = _wait_for(lambda: sway_find(_sway_query("get_tree"), pid, match), spec)
    if con_id is None:
        raise WindowError("no matching window appeared")

    state = str(spec.get("state", spec.get("position", ""))).lower()
    if spec.get("workspace") is not None:
        _sway_command(con_id, f"move container to workspace number {int(spec['workspace'])}")

    if state == "minimized":
        # Sway has no minimise; the scratchpad is the closest equivalent.
        _sway_command(con_id, "move scratchpad")
        return "moved to scratchpad"

    if state == "fullscreen":
        _sway_command(con_id, "fullscreen enable")
        detail = "fullscreen"
    else:
        rect = _target_rect(spec, _pick_monitor(sway_monitors(_sway_query("get_outputs")), spec))
        if rect is None:
            detail = "no position requested"
        else:
            # Tiled windows ignore absolute geometry, so ask for floating first.
            _sway_command(con_id, "floating enable")
            _sway_command(con_id, f"resize set {rect.width} {rect.height}")
            _sway_command(con_id, f"move absolute position {rect.x} {rect.y}")
            detail = f"placed at {rect.x},{rect.y} {rect.width}x{rect.height}"

    if spec.get("focus"):
        _sway_command(con_id, "focus")
    return detail


# ── Hyprland ─────────────────────────────────────────────────────────────


def hypr_find(clients: list[dict[str, Any]], pid: int | None, match: str) -> str | None:
    """Window address (`0x…`) of the first client owned by `pid`."""
    needle = match.lower()
    for client in clients:
        if pid and client.get("pid") != pid:
            continue
        if needle and needle not in str(client.get("title", "")).lower():
            continue
        address = client.get("address")
        if isinstance(address, str) and address:
            return address
    return None


def hypr_monitors(monitors: list[dict[str, Any]]) -> list[Rect]:
    found: list[Rect] = []
    for monitor in monitors:
        try:
            geometry = Rect(
                int(monitor["x"]),
                int(monitor["y"]),
                int(monitor["width"]),
                int(monitor["height"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if monitor.get("focused"):
            found.insert(0, geometry)
        else:
            found.append(geometry)
    return found


def _hypr_query(what: str) -> Any:
    raw = _run(["hyprctl", "-j", what])
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise WindowError(f"hyprctl returned invalid JSON: {exc}") from exc


def _hypr_dispatch(*commands: str) -> None:
    # --batch keeps the placement atomic; separate calls let the compositor
    # re-tile in between and the window lands in the wrong spot.
    _run(["hyprctl", "--batch", ";".join(f"dispatch {c}" for c in commands)])


def _apply_hyprland(spec: dict[str, Any], pid: int | None, match: str) -> str:
    if not shutil.which("hyprctl"):
        raise WindowError("hyprctl is not installed")

    address = _wait_for(lambda: hypr_find(_hypr_query("clients"), pid, match), spec)
    if address is None:
        raise WindowError("no matching window appeared")

    state = str(spec.get("state", spec.get("position", ""))).lower()
    if spec.get("workspace") is not None:
        _hypr_dispatch(f"movetoworkspacesilent {int(spec['workspace'])},address:{address}")

    if state == "minimized":
        _hypr_dispatch(f"movetoworkspacesilent special:minimized,address:{address}")
        return "moved to the special workspace"

    if state == "fullscreen":
        _hypr_dispatch(f"focuswindow address:{address}", "fullscreen 0")
        return "fullscreen"

    rect = _target_rect(spec, _pick_monitor(hypr_monitors(_hypr_query("monitors")), spec))
    if rect is None:
        detail = "no position requested"
    else:
        _hypr_dispatch(
            f"setfloating address:{address}",
            f"resizewindowpixel exact {rect.width} {rect.height},address:{address}",
            f"movewindowpixel exact {rect.x} {rect.y},address:{address}",
        )
        detail = f"placed at {rect.x},{rect.y} {rect.width}x{rect.height}"

    if spec.get("focus"):
        _hypr_dispatch(f"focuswindow address:{address}")
    return detail


# ── KWin (Plasma Wayland) ────────────────────────────────────────────────

# KWin 6 renamed clientList->windowList and the per-window `desktop` int became
# a `desktops` array, so the script probes for both rather than assuming one.
_KWIN_SCRIPT = """
(function () {
    var list = (typeof workspace.windowList === "function")
        ? workspace.windowList()
        : workspace.clientList();
    for (var i = 0; i < list.length; i++) {
        var w = list[i];
        if (%(pid)s > 0 && w.pid !== %(pid)s) continue;
        var title = String(w.caption || "").toLowerCase();
        if ("%(match)s" !== "" && title.indexOf("%(match)s") === -1) continue;
        if (%(minimize)s) { w.minimized = true; break; }
        if (%(workspace)s > 0) {
            if (typeof workspace.desktops !== "undefined" && workspace.desktops.length) {
                var target = workspace.desktops[%(workspace)s - 1];
                if (target) w.desktops = [target];
            } else {
                w.desktop = %(workspace)s;
            }
        }
        if (%(fullscreen)s) { w.fullScreen = true; break; }
        if (%(place)s) {
            w.frameGeometry = {
                x: %(x)s, y: %(y)s, width: %(width)s, height: %(height)s
            };
        }
        if (%(focus)s) workspace.activeWindow = w;
        break;
    }
})();
"""


def kwin_monitors(payload: Any) -> list[Rect]:
    """Parse `kscreen-doctor -j` output into monitor rectangles."""
    outputs = payload.get("outputs") if isinstance(payload, dict) else payload
    monitors: list[Rect] = []
    for output in outputs or []:
        if not isinstance(output, dict) or not output.get("enabled", True):
            continue
        pos = output.get("pos") or {}
        size = output.get("size") or {}
        try:
            geometry = Rect(
                int(pos.get("x", 0)),
                int(pos.get("y", 0)),
                int(size["width"]),
                int(size["height"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if output.get("primary"):
            monitors.insert(0, geometry)
        else:
            monitors.append(geometry)
    return monitors


def _kwin_screens() -> list[Rect]:
    if not shutil.which("kscreen-doctor"):
        return []
    try:
        return kwin_monitors(json.loads(_run(["kscreen-doctor", "-j"])))
    except (WindowError, ValueError, OSError):
        return []


def _gdbus(*args: str) -> str:
    if not shutil.which("gdbus"):
        raise WindowError("gdbus is not installed (glib2 tools)")
    return _run(["gdbus", "call", "--session", "--dest", "org.kde.KWin", *args], timeout=15)


def _apply_kwin(spec: dict[str, Any], pid: int | None, match: str) -> str:
    import tempfile

    state = str(spec.get("state", spec.get("position", ""))).lower()
    rect = _target_rect(spec, _pick_monitor(_kwin_screens(), spec))
    script = _KWIN_SCRIPT % {
        "pid": int(pid or 0),
        "match": match.lower().replace('"', ""),
        "minimize": "true" if state == "minimized" else "false",
        "fullscreen": "true" if state in ("fullscreen", "maximized") else "false",
        "workspace": int(spec.get("workspace") or 0),
        "place": "true" if rect else "false",
        "focus": "true" if spec.get("focus") else "false",
        "x": rect.x if rect else 0,
        "y": rect.y if rect else 0,
        "width": rect.width if rect else 0,
        "height": rect.height if rect else 0,
    }

    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 - KWin opens it by path
        "w", suffix=".js", delete=False, encoding="utf-8"
    )
    name = f"palaunch-{int(time.time() * 1000)}"
    try:
        handle.write(script)
        handle.close()
        _gdbus(
            "--object-path",
            "/Scripting",
            "--method",
            "org.kde.kwin.Scripting.loadScript",
            handle.name,
            name,
        )
        _gdbus("--object-path", "/Scripting", "--method", "org.kde.kwin.Scripting.start")
    finally:
        try:
            _gdbus(
                "--object-path",
                "/Scripting",
                "--method",
                "org.kde.kwin.Scripting.unloadScript",
                name,
            )
        except WindowError:
            pass  # leaving a spent script loaded is harmless
        try:
            os.unlink(handle.name)
        except OSError:
            pass

    if state == "minimized":
        return "minimized"
    if rect:
        return f"placed at {rect.x},{rect.y} {rect.width}x{rect.height}"
    return "adjusted"


# ── entry point ──────────────────────────────────────────────────────────


def _wait_for(lookup, spec: dict[str, Any]):
    """Poll `lookup` until it returns something, or the timeout expires."""
    deadline = time.time() + float(spec.get("timeout", 10))
    while True:
        try:
            found = lookup()
        except WindowError:
            found = None
        if found is not None:
            return found
        if time.time() >= deadline:
            return None
        time.sleep(0.3)


def monitors() -> list[Rect]:
    """Monitor rectangles from whichever compositor is running."""
    which = compositor()
    try:
        if which == SWAY:
            return sway_monitors(_sway_query("get_outputs"))
        if which == HYPRLAND:
            return hypr_monitors(_hypr_query("monitors"))
        if which == KWIN:
            return _kwin_screens()
    except (WindowError, OSError):
        pass
    return []


def apply(spec: dict[str, Any], pid: int | None, match: str) -> str:
    which = compositor()
    if which == SWAY:
        return _apply_sway(spec, pid, match)
    if which == HYPRLAND:
        return _apply_hyprland(spec, pid, match)
    if which == KWIN:
        return _apply_kwin(spec, pid, match)
    if which == GNOME:
        raise WindowError(
            "GNOME on Wayland exposes no window-placement API to other "
            "programs (Shell.Eval is disabled on release builds). Either run "
            "the app under XWayland, or place windows with a GNOME extension."
        )
    raise WindowError(
        f"unsupported Wayland compositor "
        f"({os.environ.get('XDG_CURRENT_DESKTOP', 'unknown')}) — "
        "sway, Hyprland and KWin are supported"
    )
