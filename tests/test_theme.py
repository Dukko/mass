"""Palette file parsing for the three formats Mass accepts."""

import json

from mass.theme import _parse_palette_file
from mass.ui.style import CSS as STYLE_CSS
from mass.ui.style import FALLBACKS, SCROLLBAR_CSS


def _write(tmp_path, data):
    path = tmp_path / "colors.json"
    path.write_text(json.dumps(data))
    return path


def test_dms_and_template_format(tmp_path):
    mode, palettes = _parse_palette_file(_write(tmp_path, {
        "mode": "dark",
        "dark": {"surface": "#101010"},
        "light": {"surface": "#f0f0f0"},
    }))
    assert mode == "dark"
    assert palettes["dark"]["surface"] == "#101010"
    assert palettes["light"]["surface"] == "#f0f0f0"


def test_nested_colors_key(tmp_path):
    _mode, palettes = _parse_palette_file(_write(tmp_path, {
        "colors": {"dark": {"primary": "#ffffff"}, "light": {"primary": "#000000"}},
    }))
    assert palettes["dark"]["primary"] == "#ffffff"
    assert palettes["light"]["primary"] == "#000000"


def test_raw_matugen_format(tmp_path):
    _mode, palettes = _parse_palette_file(_write(tmp_path, {
        "primary": {"dark": {"color": "#aabbcc"}, "light": {"color": "#112233"}},
        "surface": {"dark": {"color": "#010203"}},
    }))
    assert palettes["dark"] == {"primary": "#aabbcc", "surface": "#010203"}
    assert palettes["light"] == {"primary": "#112233"}


def test_invalid_or_empty_files(tmp_path):
    assert _parse_palette_file(tmp_path / "missing.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert _parse_palette_file(bad) is None
    assert _parse_palette_file(_write(tmp_path, {"mode": "dark"})) is None


def test_scrollbar_suppression_css():
    from gi.repository import Gtk

    provider = Gtk.CssProvider()
    provider.load_from_string(SCROLLBAR_CSS)
    assert "scrollbar" in SCROLLBAR_CSS
    assert "min-width: 0;" in SCROLLBAR_CSS
    assert "min-height: 0;" in SCROLLBAR_CSS
    assert "opacity: 0;" in SCROLLBAR_CSS
    assert SCROLLBAR_CSS in STYLE_CSS


def test_style_css_loads_cleanly():
    from gi.repository import Gtk

    provider = Gtk.CssProvider()
    provider.load_from_string(STYLE_CSS)


def test_winamp_palette_covers_every_token_the_stylesheet_uses():
    from mass.ui.winamp import PALETTE

    assert {k.replace("-", "_") for k in FALLBACKS} <= PALETTE.keys()


def test_winamp_css_loads_cleanly():
    from gi.repository import Gtk

    from mass.theme import build_css
    from mass.ui.winamp import CSS, PALETTE

    errors: list[str] = []
    provider = Gtk.CssProvider()
    provider.connect("parsing-error", lambda _p, _s, err: errors.append(err.message))
    provider.load_from_string(build_css(PALETTE) + CSS)
    assert errors == []


def _press(keys):
    from mass.ui.winamp import konami_step

    state = 0
    for key in keys:
        state = konami_step(state, key)
    return state


def test_konami_plain_sequence_completes():
    from gi.repository import Gdk

    k = Gdk
    keys = [k.KEY_Up, k.KEY_Up, k.KEY_Down, k.KEY_Down, k.KEY_Left, k.KEY_Right,
            k.KEY_Left, k.KEY_Right, k.KEY_b, k.KEY_a]
    assert _press(keys) == 10


def test_konami_extra_up_keeps_the_prefix():
    from gi.repository import Gdk

    k = Gdk
    assert _press([k.KEY_Up, k.KEY_Up, k.KEY_Up]) == 2


def test_konami_uppercase_with_shift_completes():
    from gi.repository import Gdk

    k = Gdk
    keys = [k.KEY_Up, k.KEY_Up, k.KEY_Down, k.KEY_Down, k.KEY_Left, k.KEY_Right,
            k.KEY_Left, k.KEY_Right, k.KEY_Shift_L, k.KEY_B, k.KEY_Shift_L, k.KEY_A]
    assert _press(keys) == 10


def test_konami_wrong_key_mid_sequence_resets():
    from gi.repository import Gdk

    k = Gdk
    assert _press([k.KEY_Up, k.KEY_Up, k.KEY_Down, k.KEY_x]) == 0


def test_nowplaying_sizing_constants():
    from mass.ui.nowplaying import ART_MIN, CARD_MIN, PANEL_MIN

    assert ART_MIN <= 100
    assert CARD_MIN <= 400
    assert PANEL_MIN <= 450


