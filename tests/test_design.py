"""The rules the interface is held to, checked rather than remembered.

The palette is monochrome, the copy carries no emoji, and icons appear only
where the design allows them. All three are easy to break by accident in a
window nobody opened during review, so they are pinned here.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from launcher import theme

SOURCE = Path(theme.__file__).parent

# The palette is built from Zinc-style neutrals, whose greys carry a few points
# of blue (#a1a1aa and friends). Anything past that is a hue, and hues are what
# this design has none of — the old accent spread 146.
MAX_CHANNEL_SPREAD = 12

# Pictographs, symbols, dingbats, geometric shapes and the variation selector
# that turns a glyph into an emoji. Box drawing is deliberately not here: the
# source comments rule off their sections with it, and nobody sees that.
DECORATION = re.compile(
    "[\U0001f000-\U0001faff"  # emoji proper
    "\u2190-\u21ff"  # arrows (the keycaps below are the exception)
    "\u25a0-\u25ff"  # geometric shapes: the old row bullets and markers
    "\u2600-\u27bf"  # misc symbols and dingbats
    "\u2b00-\u2bff"  # stars and more arrows
    "\ufe0f]"  # variation selector
)

# Arrow keys and the return key are named by their glyphs in the keyboard hint
# line, because "up arrow" is worse. They are keycaps, not decoration.
KEYCAPS = set("↑↓←→⏎")


def _channels(colour: str) -> tuple[int, int, int]:
    value = colour.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


@pytest.mark.parametrize("palette", [theme.DARK, theme.LIGHT], ids=lambda p: p.name)
def test_the_palette_is_monochrome(palette):
    for field, colour in vars(palette).items():
        if field == "name":
            continue
        red, green, blue = _channels(colour)
        spread = max(red, green, blue) - min(red, green, blue)
        assert spread <= MAX_CHANNEL_SPREAD, f"{palette.name}.{field} = {colour} carries a hue"


def test_the_accent_is_white_on_black_in_the_dark_palette():
    assert theme.DARK.accent == "#ffffff"
    assert theme.DARK.on_accent == "#000000"
    assert theme.DARK.bg == "#09090b"


def test_the_light_palette_inverts_the_same_system():
    assert theme.LIGHT.accent == theme.LIGHT.fg
    assert theme.LIGHT.on_accent == "#ffffff"


def test_status_tones_are_greys_not_signals():
    """Nothing is red or green; a failure is simply the brightest thing."""
    for palette in (theme.DARK, theme.LIGHT):
        assert palette.err == ("#ffffff" if palette.name == "dark" else "#000000")
        assert palette.ok == palette.fg
        assert palette.warn == palette.muted


@pytest.mark.parametrize(
    "module", sorted(path.name for path in SOURCE.glob("*.py")), ids=lambda name: name
)
def test_no_module_prints_a_decorative_glyph(module):
    """Only what the program can print is checked — comments may draw rules."""
    tree = ast.parse((SOURCE / module).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        found = set(DECORATION.findall(node.value)) - KEYCAPS
        assert not found, f"{module}:{node.lineno} contains {found}"


def test_the_shipped_profiles_carry_no_decorative_glyphs():
    for path in sorted((SOURCE.parents[1] / "profiles").glob("*.yaml")):
        assert not DECORATION.findall(path.read_text(encoding="utf-8")), path.name


def test_icons_are_outlines_on_a_single_grid():
    """No fills, no gradients: strokes on a 24-unit grid, like Lucide."""
    from launcher import icons

    for name, shapes in icons.ICONS.items():
        assert shapes, name
        for kind, *points in shapes:
            assert kind in ("line", "oval", "rect"), f"{name}: {kind}"
            assert all(0 <= value <= icons.GRID for value in points), name
