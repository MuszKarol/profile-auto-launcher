"""Minimal outline icons, drawn rather than shipped.

The interface uses icons sparingly — navigation entries and the buttons whose
verb has a well-known shape (run, add, remove, move) — so a whole icon font
would be a dependency for a couple of dozen glyphs. These are drawn on a Tk canvas from line segments in a
24×24 grid, in the Lucide manner: one stroke weight, round caps, no fills.

Because they are strokes rather than glyphs they take the palette's colour,
scale to any size, and look the same on Windows, macOS and Linux.
"""

from __future__ import annotations

import math
import tkinter as tk

GRID = 24.0
STROKE = 1.6


def _arc(cx: float, cy: float, r: float, start: float, end: float, steps: int = 12) -> tuple:
    """A polyline tracing a circular arc, angles in degrees, clockwise from 3 o'clock.

    Tk's own arcs cannot carry round caps, so curves are short polylines —
    at icon sizes the segments are below a pixel and read as a curve.
    """
    points: list[float] = []
    for i in range(steps + 1):
        angle = math.radians(start + (end - start) * i / steps)
        points += [round(cx + r * math.cos(angle), 2), round(cy + r * math.sin(angle), 2)]
    return ("line", *points)


# Each icon is a list of shapes on the 24×24 grid:
#   ("line", x1, y1, x2, y2, …)  polyline through the points
#   ("oval", x1, y1, x2, y2)     circle or ellipse in that box
#   ("rect", x1, y1, x2, y2)     rectangle
ICONS: dict[str, list[tuple]] = {
    # a terminal prompt — the application mark
    "terminal": [
        ("rect", 3, 4, 21, 20),
        ("line", 7.5, 9, 10.5, 12, 7.5, 15),
        ("line", 13, 15.5, 17, 15.5),
    ],
    # ── navigation ──
    # play in a circle — Launch
    "play": [
        ("oval", 3, 3, 21, 21),
        ("line", 10, 8.5, 15.5, 12, 10, 15.5, 10, 8.5),
    ],
    # stacked sheets — Profiles
    "layers": [
        ("line", 12, 3, 21, 7.5, 12, 12, 3, 7.5, 12, 3),
        ("line", 3, 12.5, 12, 17, 21, 12.5),
        ("line", 3, 16.5, 12, 21, 21, 16.5),
    ],
    # two arrows chasing round a circle — Sync
    "sync": [
        _arc(12, 12, 8, 200, 340),
        ("line", 20.5, 5.5, 19.5, 9.3, 15.8, 8.4),
        _arc(12, 12, 8, 20, 160),
        ("line", 3.5, 18.5, 4.5, 14.7, 8.2, 15.6),
    ],
    # a pulse line — Activity
    "activity": [
        ("line", 3, 12, 7, 12, 10, 4, 14, 20, 17, 12, 21, 12),
    ],
    # sliders — Settings
    "sliders": [
        ("line", 4, 7, 11, 7),
        ("line", 17, 7, 20, 7),
        ("oval", 11, 4.5, 16, 9.5),
        ("line", 4, 17, 7, 17),
        ("line", 13, 17, 20, 17),
        ("oval", 7, 14.5, 12, 19.5),
    ],
    # a box being opened — the setup wizard
    "package": [
        ("line", 12, 3, 20.5, 7.5, 20.5, 16.5, 12, 21, 3.5, 16.5, 3.5, 7.5, 12, 3),
        ("line", 3.5, 7.5, 12, 12, 20.5, 7.5),
        ("line", 12, 12, 12, 21),
    ],
    # ── actions ──
    "run": [("line", 7, 4.5, 19, 12, 7, 19.5, 7, 4.5)],
    "stop": [("rect", 6, 6, 18, 18)],
    "plus": [("line", 12, 5, 12, 19), ("line", 5, 12, 19, 12)],
    "minus": [("line", 5, 12, 19, 12)],
    "check": [("line", 5, 12.5, 10, 17.5, 19.5, 7)],
    "close": [("line", 6, 6, 18, 18), ("line", 18, 6, 6, 18)],
    "search": [("oval", 4, 4, 17, 17), ("line", 15.5, 15.5, 20, 20)],
    "edit": [
        ("line", 4, 20, 4.5, 16, 15.5, 5, 19, 8.5, 8, 19.5, 4, 20),
        ("line", 13, 7.5, 16.5, 11),
    ],
    "trash": [
        ("line", 4, 6.5, 20, 6.5),
        ("line", 9, 6.5, 9, 4, 15, 4, 15, 6.5),
        ("line", 6, 6.5, 7, 20, 17, 20, 18, 6.5),
        ("line", 10, 10.5, 10, 16),
        ("line", 14, 10.5, 14, 16),
    ],
    "folder": [("line", 3, 19, 3, 5, 9.5, 5, 11.5, 7.5, 21, 7.5, 21, 19, 3, 19)],
    "refresh": [
        _arc(12, 12, 8, 0, 300, 20),
        ("line", 20, 3.5, 20, 7.8, 15.8, 7.8),
    ],
    "record": [("oval", 3, 3, 21, 21), ("oval", 9, 9, 15, 15)],
    "arrow-up": [("line", 12, 19, 12, 5), ("line", 6, 11, 12, 5, 18, 11)],
    "arrow-down": [("line", 12, 5, 12, 19), ("line", 6, 13, 12, 19, 18, 13)],
    "upload": [
        ("line", 12, 15, 12, 4),
        ("line", 7.5, 8.5, 12, 4, 16.5, 8.5),
        ("line", 4, 15, 4, 20, 20, 20, 20, 15),
    ],
    "download": [
        ("line", 12, 4, 12, 15),
        ("line", 7.5, 10.5, 12, 15, 16.5, 10.5),
        ("line", 4, 15, 4, 20, 20, 20, 20, 15),
    ],
    "chevron-down": [("line", 7, 10, 12, 15, 17, 10)],
    "chevron-right": [("line", 10, 7, 15, 12, 10, 17)],
}


def draw(
    parent: tk.Misc,
    name: str,
    size: int = 18,
    colour: str = "#ffffff",
    bg: str = "",
) -> tk.Canvas:
    """A canvas holding one icon. Unknown names give an empty canvas."""
    canvas = tk.Canvas(
        parent,
        width=size,
        height=size,
        bg=bg or parent.cget("bg"),
        highlightthickness=0,
        borderwidth=0,
        takefocus=0,
    )
    scale = size / GRID
    width = max(1.0, STROKE * scale)
    for shape in ICONS.get(name, []):
        kind, *points = shape
        scaled = [value * scale for value in points]
        if kind == "line":
            canvas.create_line(
                *scaled, fill=colour, width=width, capstyle="round", joinstyle="round"
            )
        elif kind == "oval":
            canvas.create_oval(*scaled, outline=colour, width=width)
        elif kind == "rect":
            canvas.create_rectangle(*scaled, outline=colour, width=width)
    canvas.icon_name = name  # type: ignore[attr-defined]
    return canvas


def recolour(canvas: tk.Canvas, colour: str, bg: str | None = None) -> None:
    """Restyle an icon in place — what a hover or a selected tab needs."""
    for item in canvas.find_all():
        kind = canvas.type(item)
        if kind == "line":
            canvas.itemconfigure(item, fill=colour)
        else:
            canvas.itemconfigure(item, outline=colour)
    if bg is not None:
        canvas.configure(bg=bg)
