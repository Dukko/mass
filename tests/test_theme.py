"""Palette file parsing for the three formats Mass accepts."""

import json

from mass.theme import _parse_palette_file
from mass.ui.style import CSS as STYLE_CSS
from mass.ui.style import SCROLLBAR_CSS


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

