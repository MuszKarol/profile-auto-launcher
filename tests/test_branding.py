"""The app mark: it has to be a real PNG, a real ICO, and legible when small."""

from __future__ import annotations

import struct

from launcher import branding


def test_the_png_is_a_png_of_the_size_asked_for():
    data = branding.png_bytes(64)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (64, 64)
    assert data[-12:] == b"\x00\x00\x00\x00IEND\xaeB`\x82"


def test_the_icon_is_drawn_not_stored():
    """Every surface renders from the same shape, so none can drift."""
    assert branding.png_bytes(32) == branding.png_bytes(32)
    assert branding.png_bytes(32) != branding.png_bytes(48)


def test_the_mark_fills_its_square_and_leaves_the_corners_clear():
    size = 32
    pixels = branding.render_rgba(size)

    def alpha(x: int, y: int) -> int:
        return pixels[(y * size + x) * 4 + 3]

    assert alpha(size // 2, size // 2) == 255  # centre is solid
    assert alpha(0, 0) == 0  # corners are rounded away
    assert alpha(size - 1, size - 1) == 0


def test_both_the_badge_and_the_glyph_are_actually_drawn():
    pixels = branding.render_rgba(64)
    opaque = {
        tuple(pixels[offset : offset + 3])
        for offset in range(0, len(pixels), 4)
        if pixels[offset + 3] == 255
    }
    assert tuple(branding.BADGE[:3]) in opaque
    assert tuple(branding.GLYPH[:3]) in opaque


def test_the_ico_holds_one_png_per_size():
    data = branding.ico_bytes((16, 32))
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind, count) == (0, 1, 2)
    width, height = data[6], data[7]
    assert (width, height) == (16, 16)
    (offset,) = struct.unpack("<I", data[6 + 12 : 6 + 16])
    assert data[offset : offset + 8] == b"\x89PNG\r\n\x1a\n"


def test_a_256_pixel_entry_is_recorded_as_zero():
    """0 means 256 in an ICO directory; writing 256 into a byte cannot work."""
    data = branding.ico_bytes((256,))
    assert (data[6], data[7]) == (0, 0)


def test_the_svg_carries_the_same_shape():
    svg = branding.svg_text()
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert svg.count("<path") == len(branding._GLYPH_STROKES)


def test_the_assets_are_written_where_the_installers_look(tmp_path):
    written = branding.write_assets(tmp_path / "assets")
    assert [path.name for path in written] == ["icon.svg", "icon.png", "icon.ico"]
    assert all(path.stat().st_size > 0 for path in written)
