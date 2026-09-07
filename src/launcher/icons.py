"""Minimal outline icons, drawn rather than shipped.

The interface uses icons sparingly — navigation entries and the header of each
tool, nothing else — so a whole icon font would be four glyphs of value and a
dependency of cost. These are drawn on a Tk canvas from line segments in a
24×24 grid, in the Lucide manner: one stroke weight, round caps, no fills.

Because they are strokes rather than glyphs they take the palette's colour,
scale to any size, and look the same on Windows, macOS and Linux.
"""

from __future__ import annotations

import tkinter as tk

GRID = 24.0
STROKE = 1.6

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
    # play in a circle — Launch
    "play": [
        ("oval", 3, 3, 21, 21),
        ("line", 10, 8.5, 16, 12, 10, 15.5, 10, 8.5),
    ],
    # stacked cards — Profiles
    "layers": [
        ("line", 12, 3, 21, 7.5, 12, 12, 3, 7.5, 12, 3),
        ("line", 3, 12.5, 12, 17, 21, 12.5),
        ("line", 3, 16.5, 12, 21, 21, 16.5),
    ],
    # two arrows round a loop — Sync
    "sync": [
        ("line", 20, 4, 20, 10, 14, 10),
        ("line", 4, 20, 4, 14, 10, 14),
        ("line", 6.5, 9.5, 8.5, 7, 11.5, 5.5, 15, 5.8, 18, 7.6, 20, 10),
        ("line", 4, 14, 6, 16.4, 9, 18.2, 12.5, 18.5, 15.5, 17, 17.5, 14.5),
    ],
    # a pulse line — Activity
    "activity": [
        ("line", 3, 12, 8, 12, 10.5, 5, 14, 19, 16.5, 12, 21, 12),
    ],
    # sliders — Settings
    "sliders": [
        ("line", 5, 4, 5, 10),
        ("line", 5, 14, 5, 20),
        ("oval", 2.6, 10, 7.4, 14.4),
        ("line", 12, 4, 12, 6),
        ("line", 12, 10.4, 12, 20),
        ("oval", 9.6, 5.8, 14.4, 10.4),
        ("line", 19, 4, 19, 14),
        ("line", 19, 18.4, 19, 20),
        ("oval", 16.6, 13.8, 21.4, 18.4),
    ],
    # a box being opened — the setup wizard
    "package": [
        ("line", 12, 3, 20.5, 7.5, 20.5, 16.5, 12, 21, 3.5, 16.5, 3.5, 7.5, 12, 3),
        ("line", 3.5, 7.5, 12, 12, 20.5, 7.5),
        ("line", 12, 12, 12, 21),
    ],
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
