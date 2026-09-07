"""Design tokens for the Tk surfaces: colours, fonts, spacing and radii.

The palette is monochrome by design. There is one accent — pure white — and
everything else is a step of grey between the window ground and the text on
it, so hierarchy comes from contrast, weight and space rather than from hue.
Dark is the default; `light` is the same system inverted, and `auto` follows
the desktop preference.

Because there is no colour to spend on meaning, status is carried by weight
and tone: a failure is pure white and bold, ordinary success is body text, and
anything skipped or inactive drops to the muted grey.

The font family matters more than it looks: a hard-coded "Segoe UI" does not
exist outside Windows, so Tk silently substitutes something else.
`font_family()` picks the first family the running Tk actually has.
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
    bg="#09090b",  # window ground
    panel="#161618",  # cards and the side nav
    panel_hover="#1c1c1e",  # inputs, buttons, anything interactive
    panel_selected="#27272a",  # the row or tab you are on
    elevated="#121212",  # dialogs and popovers
    fg="#f4f4f5",
    muted="#a1a1aa",
    faint="#71717a",  # captions and section labels
    accent="#ffffff",
    accent_hover="#e4e4e7",
    on_accent="#000000",
    border="#27272a",
    # No semantic hues: a failure is the brightest thing on screen, ordinary
    # success is body text, and anything skipped fades into the secondary tone.
    ok="#f4f4f5",
    err="#ffffff",
    warn="#a1a1aa",
    field_bg="#1c1c1e",
    field_border="#27272a",
    shadow="#000000",
)

LIGHT = Palette(
    name="light",
    bg="#ffffff",
    panel="#fafafa",
    panel_hover="#f4f4f5",
    panel_selected="#e4e4e7",
    elevated="#ffffff",
    fg="#09090b",
    muted="#52525b",
    faint="#71717a",
    accent="#09090b",
    accent_hover="#27272a",
    on_accent="#ffffff",
    border="#e4e4e7",
    ok="#09090b",
    err="#000000",
    warn="#52525b",
    field_bg="#ffffff",
    field_border="#d4d4d8",
    shadow="#d4d4d8",
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
    "windows": ("Segoe UI Variable Text", "Segoe UI", "Inter", "Tahoma"),
    "darwin": ("SF Pro Text", "Inter", "Helvetica Neue", "Lucida Grande"),
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
