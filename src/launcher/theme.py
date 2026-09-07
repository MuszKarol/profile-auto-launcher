"""Design tokens for the Tk surfaces: colours, fonts, spacing and radii.

Dark is the default and the palette the UI was designed against; `light` is a
tuned counterpart, and `auto` follows the desktop preference. Everything the
windows draw comes from here, so a colour is changed in one place.

The font family matters more than it looks: a hard-coded "Segoe UI" does not
exist outside Windows, so Tk silently substitutes something else. `ui_font()`
picks the first family the running Tk actually has.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from launcher.config import PLATFORM

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class Palette:
    """Every colour the windows use. Named by role, never by hue."""

    name: str
    bg: str  # window ground
    panel: str  # cards, rows, side nav
    panel_hover: str
    panel_selected: str
    elevated: str  # dialogs and popovers, one step above panel
    fg: str
    muted: str  # secondary text
    faint: str  # tertiary text, separators' labels
    accent: str
    accent_hover: str
    on_accent: str  # text drawn on top of `accent`
    border: str
    ok: str
    err: str
    warn: str
    field_bg: str
    field_border: str
    shadow: str


DARK = Palette(
    name="dark",
    bg="#0b0e14",
    panel="#141926",
    panel_hover="#1a2130",
    panel_selected="#212a3d",
    elevated="#171d2b",
    fg="#e9edf6",
    muted="#94a0b8",
    faint="#5f6b83",
    accent="#6d8cff",
    accent_hover="#8aa2ff",
    on_accent="#08101f",
    border="#232b3d",
    ok="#5ee9a4",
    err="#ff7b8a",
    warn="#f7c65c",
    field_bg="#0e131d",
    field_border="#2a3348",
    shadow="#05070c",
)

LIGHT = Palette(
    name="light",
    bg="#f7f8fb",
    panel="#ffffff",
    panel_hover="#eef1f8",
    panel_selected="#e3e9fb",
    elevated="#ffffff",
    fg="#141926",
    muted="#5a6478",
    faint="#8792a8",
    accent="#3f5bd9",
    accent_hover="#3049c4",
    on_accent="#ffffff",
    border="#dde2ee",
    ok="#0f8a4f",
    err="#c22f45",
    warn="#8a6112",
    field_bg="#ffffff",
    field_border="#ccd4e6",
    shadow="#c9cfdd",
)


@dataclass(frozen=True)
class Metrics:
    """Spacing and sizing scale — multiples of 4, like the rest of the world."""

    gap_xs: int = 4
    gap_sm: int = 8
    gap: int = 12
    gap_lg: int = 18
    gap_xl: int = 26
    pad: int = 14
    row_pad_x: int = 14
    row_pad_y: int = 10
    radius: int = 10  # only reachable where Tk draws its own shapes (canvas)


METRICS = Metrics()

# Type scale, in points. Tk sizes are per-family, so these stay conservative.
SIZE_DISPLAY = 17
SIZE_TITLE = 13
SIZE_BODY = 11
SIZE_SMALL = 9
SIZE_TINY = 8

_UI_FAMILIES = {
    "windows": ("Segoe UI Variable Text", "Segoe UI", "Tahoma"),
    "darwin": ("SF Pro Text", "Helvetica Neue", "Lucida Grande"),
    "linux": ("Inter", "Cantarell", "Ubuntu", "Noto Sans", "DejaVu Sans"),
}

_MONO_FAMILIES = {
    "windows": ("Cascadia Mono", "Consolas", "Courier New"),
    "darwin": ("SF Mono", "Menlo", "Monaco"),
    "linux": ("JetBrains Mono", "Fira Code", "Noto Sans Mono", "DejaVu Sans Mono"),
}

_family_cache: dict[str, str] = {}


def _first_installed(candidates: tuple[str, ...], fallback: str) -> str:
    """The first candidate Tk reports as installed, else `fallback`.

    Asking Tk needs a root window, and this is called while one is being built,
    so a missing root (or a Tk that cannot list families) just means the
    fallback — never an exception in the middle of opening a window.
    """
    try:
        from tkinter import font as tkfont

        installed = {name.lower() for name in tkfont.families()}
    except Exception:
        return fallback
    for candidate in candidates:
        if candidate.lower() in installed:
            return candidate
    return fallback


def font_family() -> str:
    """UI font family for this platform, resolved against what Tk has."""
    if "ui" not in _family_cache:
        table = _UI_FAMILIES.get(PLATFORM, _UI_FAMILIES["linux"])
        _family_cache["ui"] = _first_installed(table, table[-1])
    return _family_cache["ui"]


def mono_family() -> str:
    """Monospace family for paths, logs and step descriptions."""
    if "mono" not in _family_cache:
        table = _MONO_FAMILIES.get(PLATFORM, _MONO_FAMILIES["linux"])
        _family_cache["mono"] = _first_installed(table, table[-1])
    return _family_cache["mono"]


def _probe(argv: list[str]) -> str:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            creationflags=_NO_WINDOW,
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
    return "light" if "light" in gtk_theme.lower() else "dark"


def palette(choice: str | None = None) -> Palette:
    """The palette to draw with. `choice` overrides the stored setting."""
    if choice is None:
        from launcher import settings

        choice = settings.load().theme
    choice = (choice or "dark").lower()
    if choice == "light":
        return LIGHT
    if choice == "auto":
        return LIGHT if detect() == "light" else DARK
    return DARK
