"""Hidden "classic Winamp" skin: a fixed Material palette plus a CSS overlay that
squares, bevels and LCD-greens the regular stylesheet. Toggled with the Konami code."""

from __future__ import annotations

from gi.repository import Gdk

_UP, _DOWN, _LEFT, _RIGHT = Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right
KONAMI = (
    (_UP,), (_UP,), (_DOWN,), (_DOWN,), (_LEFT,), (_RIGHT,), (_LEFT,), (_RIGHT,),
    (Gdk.KEY_b, Gdk.KEY_B), (Gdk.KEY_a, Gdk.KEY_A),
)
KONAMI_LEN = len(KONAMI)
_MODIFIERS = frozenset({
    Gdk.KEY_Shift_L, Gdk.KEY_Shift_R, Gdk.KEY_Control_L, Gdk.KEY_Control_R, Gdk.KEY_Alt_L,
    Gdk.KEY_Alt_R, Gdk.KEY_Meta_L, Gdk.KEY_Meta_R, Gdk.KEY_Super_L, Gdk.KEY_Super_R,
    Gdk.KEY_Caps_Lock, Gdk.KEY_ISO_Level3_Shift,
})


def konami_step(state: int, keyval: int) -> int:
    """Next Konami-code progress after a key press; `KONAMI_LEN` means it just completed."""
    if keyval in _MODIFIERS:
        return state
    if keyval in KONAMI[state]:
        return state + 1
    # the code's only self-overlap is its leading Up, Up
    if keyval == _UP:
        return 2 if state == 2 else 1
    return 0


PALETTE: dict[str, str] = {
    "primary": "#00e000",
    "on_primary": "#000000",
    "primary_container": "#000000",
    "on_primary_container": "#00ff00",
    "inverse_primary": "#007000",
    "secondary": "#ffb000",
    "on_secondary": "#000000",
    "secondary_container": "#000000",
    "on_secondary_container": "#ffb000",
    "tertiary": "#ff7a00",
    "on_tertiary": "#000000",
    "tertiary_container": "#000000",
    "on_tertiary_container": "#ffb000",
    "error": "#ff4040",
    "on_error": "#000000",
    "error_container": "#5a1010",
    "on_error_container": "#ffb0b0",
    "background": "#29293a",
    "on_background": "#c8d0e0",
    "surface": "#29293a",
    "on_surface": "#c8d0e0",
    "surface_variant": "#3a3a4f",
    "on_surface_variant": "#9aa0b8",
    "surface_dim": "#1c1c28",
    "surface_bright": "#4a4a5e",
    "surface_tint": "#00e000",
    "surface_container_lowest": "#10101a",
    "surface_container_low": "#2f2f42",
    "surface_container": "#3a3a4f",
    "surface_container_high": "#4a4a5e",
    "surface_container_highest": "#55556b",
    "outline": "#7a7a96",
    "outline_variant": "#10101a",
    "inverse_surface": "#c8d0e0",
    "inverse_on_surface": "#29293a",
    "shadow": "#000000",
    "scrim": "#000000",
}

FONT = '"Terminus", "Fixedsys Excelsior", "Px437 IBM VGA8", "Tamzen", monospace'
FACE, HOVER = "#4a4a5e", "#5a5a72"
LCD, LCD_DIM, AMBER = "#00ff00", "#00a800", "#ffb000"
RAISED = "inset 1px 1px 0 #7a7a96, inset -1px -1px 0 #10101a"
SUNKEN = "inset 1px 1px 0 #10101a, inset -1px -1px 0 #7a7a96"
GLOW = "0 0 3px rgba(0, 255, 0, 0.55)"
W = "window.mass.winamp"
# enough repeated classes to outrank every .mass rule, so the squaring wins
ALL = "window.mass" + ".winamp" * 4

CSS = f"""
{W} {{ font-family: {FONT}; font-size: 12px; border-radius: 0; }}
{ALL} * {{ border-radius: 0; transition: none; }}
{W} label {{ font-weight: 400; }}
{W} .display-l, {W} .display-m, {W} .display-s, {W} .headline-l, {W} .headline-m,
{W} .headline-s, {W} .title-l, {W} .section-title {{ font-size: 16px; letter-spacing: 1px; }}
{W} .title-m, {W} .title-s, {W} .label-l {{ font-size: 12px; letter-spacing: 0; }}

/* panels */
{W} .content-surface {{ background: {PALETTE['surface_container_low']}; box-shadow: {SUNKEN}; }}
{W} .rail {{ background: {PALETTE['surface']}; box-shadow: inset -1px 0 0 #10101a, inset -2px 0 0 #7a7a96; }}
{W} headerbar.flat-header {{
  background: linear-gradient(to bottom, #5a5a72, #29293a); box-shadow: inset 0 -2px 0 #c8a000;
}}
{W} .card, {W} .tile, {W} .np-card, {W} .np-panel, {W} .ai-card, {W} .dj-text-card,
{W} popover > contents, {W} .player-chip, {W} .chip {{
  background: {PALETTE['surface_container']}; box-shadow: {RAISED};
}}
{W} .hero-card, {W} .ai-hero, {W} .search-bar, {W} .np-badge, {W} entry,
{W} .mini-player {{ background: #000000; color: {LCD}; box-shadow: {SUNKEN}; }}
{W} .mini-player {{ box-shadow: {SUNKEN}, 0 2px 0 #000000; }}
{W} .mini-player label, {W} .hero-card label {{ color: {LCD}; text-shadow: {GLOW}; }}
{W} .mini-player .on-variant, {W} .hero-card .on-variant {{ color: {LCD_DIM}; }}
{W} entry, {W} entry text {{ background: #000000; color: {LCD}; }}

/* buttons */
{W} button.m3, {W} button.np-tool, {W} button.np-tab, {W} .chip, {W} .player-chip,
{W} button.dj-chip, {W} menubutton > button, {W} .rail-fab {{
  background: {FACE}; color: {PALETTE['on_surface']}; box-shadow: {RAISED};
}}
{W} button.m3:hover, {W} button.np-tool:hover, {W} button.np-tab:hover, {W} .chip:hover,
{W} menubutton > button:hover, {W} .rail-fab:hover {{ background: {HOVER}; }}
{W} button.m3:active, {W} button.np-tool:active, {W} button.np-tab:active, {W} .chip:active,
{W} menubutton > button:active, {W} .rail-fab:active {{ box-shadow: {SUNKEN}; transform: none; }}
{W} button.m3.filled, {W} .mini-player button.play-mini, {W} .rail-fab.playing {{
  background: #000000; color: {LCD}; text-shadow: {GLOW};
}}
{W} button.m3.toggle.on, {W} button.np-tool.on, {W} button.np-tool:checked, {W} button.np-tab:checked,
{W} .chip.on, {W} .chip:checked, {W} button.dj-chip.on, {W} menubutton.np-tool.on > button {{
  background: #000000; color: {AMBER}; box-shadow: {SUNKEN};
}}
{W} button.m3.flat, {W} button.m3.icon.flat, {W} .rail-item, {W} menubutton.flat > button,
{W} .mini-player button.m3.icon, {W} .open-target {{ background: none; box-shadow: none; }}
{W} .rail-item.active .indicator {{ background: #000000; color: {LCD}; box-shadow: {SUNKEN}; }}
{W} .rail-item:hover .indicator {{ background: {FACE}; box-shadow: {RAISED}; }}

/* the LCD readout */
{W} .np-title {{
  font-size: 16px; letter-spacing: 2px; color: {LCD}; text-shadow: {GLOW};
  background: #000000; padding: 2px 6px;
}}
{W} .np-artist {{ font-size: 12px; color: {PALETTE['primary']}; text-shadow: {GLOW}; background: #000000;
  padding: 2px 6px; }}
{W} .np-album {{ font-size: 12px; color: {AMBER}; }}
{W} .numeric, {W} .elapsed {{ color: {LCD}; text-shadow: {GLOW}; }}
{W} .primary-text {{ color: {LCD}; }}
{W} .wavy-seekbar {{ color: {LCD}; }}

/* lists: playlist-editor black with green text */
{W} list.m3-list {{ border-spacing: 0; }}
{W} list.m3-list > row, {W} .qrow {{ background: #000000; color: #00e000; box-shadow: none; margin-bottom: 0; }}
{W} list.m3-list > row:hover, {W} listview.m3-list > row:hover > .qrow {{ background: #0a2a0a; }}
{W} list.m3-list > row.current, {W} .qrow.current, {W} .players-list > row.active {{
  background: #0a3a0a; color: {LCD};
}}
{W} list.m3-list > row .on-variant, {W} .qrow .on-variant {{ color: {LCD_DIM}; }}
{W} list.m3-list > row.current .on-variant, {W} .qrow.current .on-variant {{ color: #00c800; }}

/* sliders: sunken groove, bevelled thumb */
{W} scale.m3-slider trough, {W} scale.m3-slider.thin trough {{
  min-height: 8px; background: #000000; box-shadow: {SUNKEN};
}}
{W} scale.m3-slider highlight, {W} scale.m3-slider.thin highlight {{ min-height: 8px; background: #00b000; }}
{W} scale.m3-slider slider, {W} scale.m3-slider.thin slider {{
  min-width: 10px; min-height: 16px; margin: -4px -2px; background: {FACE}; box-shadow: {RAISED};
}}
{W} scale.m3-slider:active slider {{ min-width: 10px; box-shadow: {SUNKEN}; }}

{W} .lyrics label.lyric, {W} .np-panel .lyrics label.lyric, {W} .lyrics label.lyric.plain,
{W} .lyrics label.lyric.dim {{ font-size: 16px; }}
{W} .badge {{ background: #000000; color: {AMBER}; box-shadow: {SUNKEN}; font-size: 11px; }}
"""
