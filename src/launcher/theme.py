"""Colour palette and fonts for the Tk surfaces (HUD, profile editor).

`theme: auto` in settings follows the desktop's light/dark preference. The
font family matters more than it looks: the previous hard-coded "Segoe UI"
does not exist outside Windows, so Tk silently substituted a fallback.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

from launcher.config import PLATFORM

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    panel: str
    panel_hover: str
    panel_selected: str
    fg: str
    muted: str
    accent: str
    border: str
    ok: str
    err: str
    warn: str
    field_bg: str


DARK = Palette(
    name="dark",
    bg="#0e1117",
    panel="#161b26",
    panel_hover="#1b2231",
    panel_selected="#20293d",
    fg="#e6e9ef",
    muted="#7b8496",
    accent="#7aa2f7",
    border="#2a3247",
    ok="#7ee2a8",
    err="#f38ba8",
    warn="#e9c46a",
    field_bg="#0e1117",
)

LIGHT = Palette(
    name="light",
    bg="#fbfcfe",
    panel="#f0f2f7",
    panel_hover="#e6eaf3",
    panel_selected="#dde4f4",
    fg="#161b26",
    muted="#5d6577",
    accent="#3060c9",
    border="#ccd3e2",
    ok="#1f8a52",
    err="#c0334d",
    warn="#8a6112",
    field_bg="#ffffff",
)


def _probe(argv: list[str]) -> str:
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=3,
            check=False, creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip()


def detect() -> str:
    """'dark' or 'light' — falls back to dark when the desktop won't say."""
    if PLATFORM == "windows":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if value else "dark"
        except (ImportError, OSError):
            return "dark"
    if PLATFORM == "darwin":
        # The key is absent entirely in light mode, so an empty read means light.
        return "dark" if _probe(["defaults", "read", "-g", "AppleInterfaceStyle"]) else "light"
    scheme = _probe(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"])
    if scheme:
        return "dark" if "dark" in scheme.lower() else "light"
    gtk_theme = _probe(["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"])
    return "dark" if "dark" in gtk_theme.lower() else "dark"


def palette() -> Palette:
    from launcher import settings

    choice = (settings.load().theme or "auto").lower()
    if choice == "light":
        return LIGHT
    if choice == "dark":
        return DARK
    return LIGHT if detect() == "light" else DARK


def font_family() -> str:
    if PLATFORM == "windows":
        return "Segoe UI"
    if PLATFORM == "darwin":
        return "SF Pro Text"
    return "DejaVu Sans"


def mono_family() -> str:
    if PLATFORM == "windows":
        return "Consolas"
    if PLATFORM == "darwin":
        return "SF Mono"
    return "DejaVu Sans Mono"
