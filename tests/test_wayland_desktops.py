"""Backends that cannot be exercised on CI, tested through their pure parts.

Each backend is a thin shell around one JSON/registry payload and a handful of
CLI calls. The parsing and selection logic is what actually goes wrong, so
that is what is pinned here — with recorded payloads from the real tools.
"""

from __future__ import annotations

import json

import pytest

from launcher import wayland, windows
from launcher.windows import Rect, WindowError

# ── compositor detection ─────────────────────────────────────────────────


def test_wayland_is_detected_from_the_session_type(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert wayland.is_wayland()


def test_wayland_is_detected_from_the_display_socket(monkeypatch):
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert wayland.is_wayland()


def test_x11_session_is_not_wayland(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert not wayland.is_wayland()


@pytest.mark.parametrize(
    "env, expected",
    [
        ({"SWAYSOCK": "/run/sway.sock"}, wayland.SWAY),
        ({"HYPRLAND_INSTANCE_SIGNATURE": "abc"}, wayland.HYPRLAND),
        ({"XDG_CURRENT_DESKTOP": "KDE"}, wayland.KWIN),
        ({"XDG_CURRENT_DESKTOP": "GNOME"}, wayland.GNOME),
        ({"XDG_CURRENT_DESKTOP": "Hyprland"}, wayland.HYPRLAND),
        ({"XDG_CURRENT_DESKTOP": "weston"}, ""),
    ],
)
def test_compositor_detection(monkeypatch, env, expected):
    for name in ("SWAYSOCK", "I3SOCK", "HYPRLAND_INSTANCE_SIGNATURE", "XDG_CURRENT_DESKTOP"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert wayland.compositor() == expected


def _only_compositor(monkeypatch, name: str) -> None:
    monkeypatch.setattr(wayland, "compositor", lambda: name)


def test_gnome_explains_why_it_cannot_place_windows(monkeypatch):
    _only_compositor(monkeypatch, wayland.GNOME)
    with pytest.raises(WindowError, match="XWayland"):
        wayland.apply({"position": "left-half"}, pid=1, match="")


def test_unknown_compositor_names_the_supported_ones(monkeypatch):
    _only_compositor(monkeypatch, "")
    with pytest.raises(WindowError, match="sway, Hyprland and KWin"):
        wayland.apply({"position": "left-half"}, pid=1, match="")


# ── sway ─────────────────────────────────────────────────────────────────

SWAY_TREE = {
    "id": 1,
    "type": "root",
    "nodes": [
        {
            "id": 2,
            "type": "output",
            "nodes": [
                {
                    "id": 3,
                    "type": "workspace",
                    "nodes": [
                        {
                            "id": 7,
                            "type": "con",
                            "pid": 4242,
                            "name": "main.py — Code",
                            "app_id": "code",
                        },
                        {"id": 8, "type": "con", "nodes": []},  # split container
                    ],
                    "floating_nodes": [
                        {
                            "id": 9,
                            "type": "floating_con",
                            "pid": 5555,
                            "name": "Calculator",
                            "app_id": "calc",
                        }
                    ],
                }
            ],
        }
    ],
}


def test_sway_finds_a_window_by_pid():
    assert wayland.sway_find(SWAY_TREE, 4242, "") == 7


def test_sway_finds_floating_windows_too():
    assert wayland.sway_find(SWAY_TREE, 5555, "") == 9


def test_sway_filters_by_title():
    assert wayland.sway_find(SWAY_TREE, None, "calculator") == 9
    assert wayland.sway_find(SWAY_TREE, 4242, "calculator") is None


def test_sway_ignores_split_containers():
    # id 8 has no app_id and no window_properties: it is layout, not a window.
    assert wayland.sway_find(SWAY_TREE, None, "") != 8


def test_sway_returns_none_when_nothing_matches():
    assert wayland.sway_find(SWAY_TREE, 9999, "") is None


def test_sway_monitors_put_the_focused_output_first():
    outputs = [
        {
            "active": True,
            "focused": False,
            "rect": {"x": 1920, "y": 0, "width": 1280, "height": 720},
        },
        {"active": True, "focused": True, "rect": {"x": 0, "y": 0, "width": 1920, "height": 1080}},
        {"active": False, "rect": {"x": 0, "y": 0, "width": 800, "height": 600}},
    ]
    monitors = wayland.sway_monitors(outputs)
    assert monitors == [Rect(0, 0, 1920, 1080), Rect(1920, 0, 1280, 720)]


def test_sway_places_a_window(monkeypatch):
    calls: list[list[str]] = []
    _only_compositor(monkeypatch, wayland.SWAY)
    monkeypatch.setattr(wayland.shutil, "which", lambda _n: "/usr/bin/swaymsg")
    monkeypatch.setattr(
        wayland,
        "_sway_query",
        lambda what: (
            SWAY_TREE
            if what == "get_tree"
            else [
                {
                    "active": True,
                    "focused": True,
                    "rect": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                }
            ]
        ),
    )
    monkeypatch.setattr(wayland, "_run", lambda argv, **kw: calls.append(argv) or "")

    detail = wayland.apply({"position": "left-half", "workspace": 3}, pid=4242, match="")
    joined = " | ".join(argv[-1] for argv in calls)
    assert "move container to workspace number 3" in joined
    # A tiled window ignores absolute geometry, so floating has to come first.
    assert joined.index("floating enable") < joined.index("resize set")
    assert "resize set 960 1080" in joined
    assert "move absolute position 0 0" in joined
    assert detail == "placed at 0,0 960x1080"


def test_sway_uses_the_scratchpad_to_minimise(monkeypatch):
    calls: list[list[str]] = []
    _only_compositor(monkeypatch, wayland.SWAY)
    monkeypatch.setattr(wayland.shutil, "which", lambda _n: "/usr/bin/swaymsg")
    monkeypatch.setattr(wayland, "_sway_query", lambda _what: SWAY_TREE)
    monkeypatch.setattr(wayland, "_run", lambda argv, **kw: calls.append(argv) or "")

    assert "scratchpad" in wayland.apply({"state": "minimized"}, pid=4242, match="")


# ── hyprland ─────────────────────────────────────────────────────────────

HYPR_CLIENTS = [
    {"address": "0x55a1", "pid": 4242, "title": "main.py — Code"},
    {"address": "0x55b2", "pid": 5555, "title": "Discord"},
]


def test_hyprland_finds_a_window_by_pid():
    assert wayland.hypr_find(HYPR_CLIENTS, 5555, "") == "0x55b2"


def test_hyprland_filters_by_title():
    assert wayland.hypr_find(HYPR_CLIENTS, None, "discord") == "0x55b2"
    assert wayland.hypr_find(HYPR_CLIENTS, None, "firefox") is None


def test_hyprland_monitors_put_the_focused_one_first():
    payload = [
        {"x": 1920, "y": 0, "width": 1280, "height": 720, "focused": False},
        {"x": 0, "y": 0, "width": 1920, "height": 1080, "focused": True},
    ]
    assert wayland.hypr_monitors(payload)[0] == Rect(0, 0, 1920, 1080)


def test_hyprland_places_a_window_in_one_batch(monkeypatch):
    calls: list[list[str]] = []
    _only_compositor(monkeypatch, wayland.HYPRLAND)
    monkeypatch.setattr(wayland.shutil, "which", lambda _n: "/usr/bin/hyprctl")
    monkeypatch.setattr(
        wayland,
        "_hypr_query",
        lambda what: (
            HYPR_CLIENTS
            if what == "clients"
            else [{"x": 0, "y": 0, "width": 1920, "height": 1080, "focused": True}]
        ),
    )
    monkeypatch.setattr(wayland, "_run", lambda argv, **kw: calls.append(argv) or "")

    detail = wayland.apply({"position": "right-half"}, pid=4242, match="")
    batch = next(argv[-1] for argv in calls if "resizewindowpixel" in argv[-1])
    assert "setfloating address:0x55a1" in batch
    assert "resizewindowpixel exact 960 1080,address:0x55a1" in batch
    assert "movewindowpixel exact 960 0,address:0x55a1" in batch
    assert detail == "placed at 960,0 960x1080"


def test_hyprland_moves_to_a_workspace(monkeypatch):
    calls: list[list[str]] = []
    _only_compositor(monkeypatch, wayland.HYPRLAND)
    monkeypatch.setattr(wayland.shutil, "which", lambda _n: "/usr/bin/hyprctl")
    monkeypatch.setattr(wayland, "_hypr_query", lambda _what: HYPR_CLIENTS)
    monkeypatch.setattr(wayland, "_run", lambda argv, **kw: calls.append(argv) or "")

    wayland.apply({"workspace": 4}, pid=4242, match="")
    assert any("movetoworkspacesilent 4,address:0x55a1" in argv[-1] for argv in calls)


def test_missing_compositor_cli_is_reported(monkeypatch):
    _only_compositor(monkeypatch, wayland.SWAY)
    monkeypatch.setattr(wayland.shutil, "which", lambda _n: None)
    with pytest.raises(WindowError, match="swaymsg is not installed"):
        wayland.apply({"position": "left-half"}, pid=1, match="")


# ── kwin ─────────────────────────────────────────────────────────────────


def test_kwin_monitor_parsing():
    payload = {
        "outputs": [
            {
                "enabled": True,
                "primary": False,
                "pos": {"x": 1920, "y": 0},
                "size": {"width": 1280, "height": 720},
            },
            {
                "enabled": True,
                "primary": True,
                "pos": {"x": 0, "y": 0},
                "size": {"width": 2560, "height": 1440},
            },
            {"enabled": False, "pos": {"x": 0, "y": 0}, "size": {"width": 800, "height": 600}},
        ]
    }
    assert wayland.kwin_monitors(payload) == [Rect(0, 0, 2560, 1440), Rect(1920, 0, 1280, 720)]


def test_kwin_script_covers_both_api_generations(monkeypatch):
    scripts: list[str] = []
    _only_compositor(monkeypatch, wayland.KWIN)
    monkeypatch.setattr(wayland, "_kwin_screens", lambda: [Rect(0, 0, 1920, 1080)])

    def fake_gdbus(*args):
        for index, value in enumerate(args):
            if value == "org.kde.kwin.Scripting.loadScript":
                with open(args[index + 1], encoding="utf-8") as handle:
                    scripts.append(handle.read())
        return "(int32 1,)"

    monkeypatch.setattr(wayland, "_gdbus", fake_gdbus)
    detail = wayland.apply({"position": "left-half", "workspace": 2}, pid=4242, match="")

    script = scripts[0]
    assert "workspace.windowList" in script and "workspace.clientList" in script  # KWin 6 and 5
    assert "w.desktops = [target]" in script and "w.desktop = 2" in script
    assert "w.pid !== 4242" in script
    assert detail == "placed at 0,0 960x1080"


def test_kwin_unloads_its_script_even_when_start_fails(monkeypatch):
    seen: list[str] = []
    _only_compositor(monkeypatch, wayland.KWIN)
    monkeypatch.setattr(wayland, "_kwin_screens", lambda: [])

    def fake_gdbus(*args):
        method = next((a for a in args if a.startswith("org.kde.kwin")), "")
        seen.append(method)
        if method.endswith("start"):
            raise WindowError("kwin refused")
        return "()"

    monkeypatch.setattr(wayland, "_gdbus", fake_gdbus)
    with pytest.raises(WindowError, match="kwin refused"):
        wayland.apply({"position": "left-half"}, pid=1, match="")
    assert any(m.endswith("unloadScript") for m in seen)


# ── Windows virtual desktops ─────────────────────────────────────────────


def test_desktop_guids_are_split_into_sixteen_byte_chunks():
    blob = bytes(range(48))
    guids = windows.parse_desktop_guids(blob)
    assert len(guids) == 3
    assert guids[1] == blob[16:32]


def test_a_ragged_registry_tail_is_ignored():
    # Explorer has been seen writing a short trailing chunk; a partial GUID
    # must not become a desktop we then try to move a window to.
    assert len(windows.parse_desktop_guids(bytes(40))) == 2


def test_empty_desktop_blob_yields_nothing():
    assert windows.parse_desktop_guids(b"") == []


# ── macOS spaces ─────────────────────────────────────────────────────────

YABAI_WINDOWS = [
    {"id": 11, "pid": 4242, "title": "main.py — Code"},
    {"id": 12, "pid": 5555, "title": "Safari"},
]


def test_yabai_window_lookup_by_pid_and_title():
    assert windows.yabai_window_id(YABAI_WINDOWS, 5555, "") == 12
    assert windows.yabai_window_id(YABAI_WINDOWS, None, "safari") == 12
    assert windows.yabai_window_id(YABAI_WINDOWS, 4242, "safari") is None
    assert windows.yabai_window_id([], 1, "") is None


def test_missing_yabai_explains_the_macos_limitation(monkeypatch):
    monkeypatch.setattr(windows.shutil, "which", lambda _n: None)
    with pytest.raises(WindowError, match="no public Spaces API"):
        windows._move_to_space_darwin(4242, "", 2)


def test_space_move_uses_the_window_id(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(windows.shutil, "which", lambda _n: "/opt/homebrew/bin/yabai")

    def fake_run(argv, **_kw):
        calls.append(argv)
        return json.dumps(YABAI_WINDOWS) if "query" in argv else ""

    monkeypatch.setattr(windows, "_run", fake_run)
    assert windows._move_to_space_darwin(4242, "", 3) == "moved to space 3"
    assert calls[-1] == ["yabai", "-m", "window", "11", "--space", "3"]


# ── backend reporting ────────────────────────────────────────────────────


def test_backend_name_reports_the_wayland_compositor(monkeypatch):
    monkeypatch.setattr(windows, "PLATFORM", "linux")
    monkeypatch.setattr(wayland, "is_wayland", lambda: True)
    monkeypatch.setattr(wayland, "compositor", lambda: wayland.SWAY)
    assert windows.backend_name() == "wayland/sway"


def test_backend_name_reports_missing_x11_tooling(monkeypatch):
    monkeypatch.setattr(windows, "PLATFORM", "linux")
    monkeypatch.setattr(wayland, "is_wayland", lambda: False)
    monkeypatch.setattr(windows.shutil, "which", lambda _n: None)
    assert "wmctrl missing" in windows.backend_name()


def test_apply_routes_wayland_sessions_to_the_compositor(monkeypatch):
    from launcher import settings

    settings._cache = settings.Settings(window_management=True)
    monkeypatch.setattr(windows, "PLATFORM", "linux")
    monkeypatch.setattr(wayland, "is_wayland", lambda: True)
    monkeypatch.setattr(wayland, "apply", lambda *_a: "wayland handled it")
    assert windows.apply({"position": "left-half"}, pid=1) == "wayland handled it"
