"""The application mark, drawn in pure Python.

One shape — a `>_` prompt inside a rounded square — rendered at any size for
the tray, the window icons, the installer and the repository assets. It is
white on black, like the rest of the interface: a solid mark reads better than
an outline at the 16 pixels a tray gives it, and `launcher.icons` draws the
same prompt as a stroke for use inside the windows.

Drawing it here rather than shipping a binary keeps every surface consistent
and lets the ICO/PNG/SVG files be regenerated with `python -m launcher.branding`.

Pillow is optional: it is only needed to hand pystray an `Image`, and the
raster path below does not use it.
"""

from __future__ import annotations

import base64
import struct
import zlib
from functools import lru_cache
from pathlib import Path

APP_NAME = "Profile Auto Launcher"
APP_ID = "profile-auto-launcher"

# The product's own surface in miniature: the window ground, with the one
# accent drawn on it. A white badge would vanish into a light taskbar.
BADGE = (0x09, 0x09, 0x0B, 0xFF)
GLYPH = (0xFF, 0xFF, 0xFF, 0xFF)


def _rounded_rect(x: float, y: float, half: float, radius: float) -> float:
    """Signed distance to a rounded square centred on (0.5, 0.5).

    Distances rather than hit tests: one evaluation per pixel gives smoother
    edges than supersampling and renders a 256px icon in a blink, which is
    what makes drawing the icon on demand practical.
    """
    dx = abs(x - 0.5) - (half - radius)
    dy = abs(y - 0.5) - (half - radius)
    outside = (max(dx, 0.0) ** 2 + max(dy, 0.0) ** 2) ** 0.5
    return outside + min(max(dx, dy), 0.0) - radius


def _capsule(
    x: float, y: float, ax: float, ay: float, bx: float, by: float, radius: float
) -> float:
    """Signed distance to a thick segment — the glyph's stroke primitive."""
    vx, vy = bx - ax, by - ay
    length_sq = vx * vx + vy * vy
    t = 0.0 if length_sq == 0 else ((x - ax) * vx + (y - ay) * vy) / length_sq
    t = min(1.0, max(0.0, t))
    dx, dy = x - (ax + t * vx), y - (ay + t * vy)
    return (dx * dx + dy * dy) ** 0.5 - radius


# The `>_` prompt: a chevron of two strokes, then the underscore beside it.
_STROKE = 0.052
_GLYPH_STROKES = (
    (0.30, 0.29, 0.48, 0.50),
    (0.48, 0.50, 0.30, 0.71),
    (0.56, 0.71, 0.74, 0.71),
)


def _glyph(x: float, y: float) -> float:
    return min(_capsule(x, y, *stroke, _STROKE) for stroke in _GLYPH_STROKES)


def _coverage(distance: float, scale: float) -> float:
    """Analytic anti-aliasing: one pixel of feather across the edge."""
    return min(1.0, max(0.0, 0.5 - distance * scale))


@lru_cache(maxsize=16)
def render_rgba(size: int) -> bytes:
    """The mark as raw RGBA bytes, anti-aliased and cached per size."""
    size = max(8, int(size))
    pixels = bytearray(size * size * 4)
    step = 1.0 / size
    for py in range(size):
        y = (py + 0.5) * step
        for px in range(size):
            x = (px + 0.5) * step
            alpha = _coverage(_rounded_rect(x, y, 0.48, 0.23), size)
            if alpha <= 0.0:
                continue
            ink = _coverage(_glyph(x, y), size)
            offset = (py * size + px) * 4
            for channel in range(3):
                pixels[offset + channel] = int(BADGE[channel] * (1 - ink) + GLYPH[channel] * ink)
            pixels[offset + 3] = int(255 * alpha)
    return bytes(pixels)


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


@lru_cache(maxsize=16)
def png_bytes(size: int = 256) -> bytes:
    """A PNG of the mark. Tk 8.6 reads these directly, as does every OS."""
    pixels = render_rgba(size)
    stride = size * 4
    raw = b"".join(b"\x00" + pixels[y * stride : (y + 1) * stride] for y in range(size))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def ico_bytes(sizes: tuple[int, ...] = (16, 32, 48, 64, 128, 256)) -> bytes:
    """A Windows .ico holding PNG entries — what the installer needs."""
    images = [(size, png_bytes(size)) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, payloads = b"", b""
    for size, data in images:
        dimension = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset + len(payloads)
        )
        payloads += data
    return header + entries + payloads


def svg_text() -> str:
    """The same mark as vector art, for READMEs and desktop entries."""
    badge = "#{:02x}{:02x}{:02x}".format(*BADGE[:3])
    glyph = "#{:02x}{:02x}{:02x}".format(*GLYPH[:3])
    strokes = "".join(
        f'<path d="M{ax * 256:.0f} {ay * 256:.0f} L{bx * 256:.0f} {by * 256:.0f}"/>'
        for ax, ay, bx, by in _GLYPH_STROKES
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">'
        f'<rect x="5" y="5" width="246" height="246" rx="59" fill="{badge}"/>'
        f'<g stroke="{glyph}" stroke-width="{_STROKE * 2 * 256:.0f}" stroke-linecap="round" '
        f'stroke-linejoin="round" fill="none">{strokes}</g>'
        "</svg>\n"
    )


def tray_image(size: int = 64):
    """A Pillow image for pystray. Raises ImportError without Pillow."""
    from PIL import Image

    return Image.frombytes("RGBA", (size, size), render_rgba(size))


def photo_image(size: int = 32, master=None):
    """A `tk.PhotoImage` of the mark, belonging to `master`'s interpreter.

    Deliberately not cached: a PhotoImage is tied to the Tk interpreter that
    made it, so a module-level cache outlives its window and then explodes at
    shutdown. The bytes behind it are cached instead, which is the slow part.
    Whoever displays the image must keep a reference to it, or Tk frees it.
    """
    import tkinter as tk

    return tk.PhotoImage(master=master, data=base64.b64encode(png_bytes(size)))


def apply_window_icon(window) -> None:
    """Best-effort window/taskbar icon. Never raises: it is decoration."""
    try:
        image = photo_image(64, master=window)
        window.iconphoto(False, image)
        hold(window, image)
    except Exception:
        pass


def hold(widget, image) -> None:
    """Keep `image` alive for as long as `widget` is, and no longer.

    Tk frees an image the moment nothing references it, so a widget has to
    hold its own — but a reference that outlives the interpreter complains
    when it is finally collected. Releasing it on `<Destroy>` does both.
    """
    widget._palaunch_image = image
    widget.bind("<Destroy>", lambda _event: setattr(widget, "_palaunch_image", None), add="+")


def write_assets(directory: Path) -> list[Path]:
    """Regenerate `assets/` — the icon files the installers and docs use."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in (
        ("icon.svg", svg_text().encode("utf-8")),
        ("icon.png", png_bytes(256)),
        ("icon.ico", ico_bytes()),
    ):
        path = directory / name
        path.write_bytes(data)
        written.append(path)
    return written


if __name__ == "__main__":  # python -m launcher.branding [dir]
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("assets")
    for written in write_assets(target):
        print(f"wrote {written}")
