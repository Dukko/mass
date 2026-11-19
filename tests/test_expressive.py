"""Placeholder foreground colours follow the matugen palette."""

from gi.repository import Gdk

import mass.theme as theme
from mass.ui.expressive import _fg_for


def rgba(value: str) -> Gdk.RGBA:
    colour = Gdk.RGBA()
    assert colour.parse(value)
    return colour


def test_fg_uses_matching_on_colour(monkeypatch):
    monkeypatch.setattr(theme, "_active_palette", {
        "secondary_container": "#4a3c10",
        "on_secondary_container": "#f7e6b0",
        "primary_container": "#1a3a5c",
        "on_primary_container": "#d7e3ff",
    })
    assert abs(_fg_for(rgba("#4a3c10")).red - 247 / 255) < 0.01
    assert abs(_fg_for(rgba("#1a3a5c")).blue - 1.0) < 0.01


def test_preferred_token_wins_when_colours_are_shared(monkeypatch):
    # several tokens can resolve to the same hex; the container token keeps its own on-colour
    monkeypatch.setattr(theme, "_active_palette", {
        "surface": "#4a3c10",
        "on_surface": "#ffffff",
        "secondary_container": "#4a3c10",
        "on_secondary_container": "#000000",
    })
    assert _fg_for(rgba("#4a3c10")).red < 0.01


def test_falls_back_to_contrast_without_a_palette(monkeypatch):
    monkeypatch.setattr(theme, "_active_palette", {})
    light = _fg_for(rgba("#ffffff"))
    dark = _fg_for(rgba("#000000"))
    assert light.red < 0.01 and light.alpha < 1
    assert dark.red > 0.99 and dark.alpha < 1
