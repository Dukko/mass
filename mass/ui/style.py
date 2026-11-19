"""Material 3 Expressive stylesheet.

Tokens mirror DankMaterialShell (Common/Theme.qml):
  shape scale   xs 4 · s 8 · m 12 · l 16 · l+ 20 · xl 28 · xl+ 32 · xxl 48 · full
  motion        expressive spatial curves (with overshoot) / effects curves
  type          Google Sans Flex, display 57/45/36, headline 32/28, title 22/16
Colors are the matugen palette as --md-* variables (theme.py), with libadwaita
fallbacks so the "Plain Adwaita" mode still looks right.
"""

from __future__ import annotations

FALLBACKS = {
    "primary": "var(--accent-bg-color)",
    "on-primary": "var(--accent-fg-color)",
    "primary-container": "color-mix(in srgb, var(--accent-bg-color) 30%, var(--window-bg-color))",
    "on-primary-container": "var(--window-fg-color)",
    "secondary": "color-mix(in srgb, var(--accent-bg-color) 60%, var(--window-fg-color))",
    "secondary-container": "color-mix(in srgb, var(--accent-bg-color) 18%, var(--window-bg-color))",
    "on-secondary-container": "var(--window-fg-color)",
    "tertiary": "var(--accent-color)",
    "tertiary-container": "color-mix(in srgb, var(--accent-bg-color) 25%, var(--window-bg-color))",
    "on-tertiary-container": "var(--window-fg-color)",
    "surface": "var(--window-bg-color)",
    "on-surface": "var(--window-fg-color)",
    "on-surface-variant": "color-mix(in srgb, var(--window-fg-color) 72%, transparent)",
    "surface-container-lowest": "var(--window-bg-color)",
    "surface-container-low": "color-mix(in srgb, var(--window-fg-color) 4%, var(--window-bg-color))",
    "surface-container": "color-mix(in srgb, var(--window-fg-color) 7%, var(--window-bg-color))",
    "surface-container-high": "color-mix(in srgb, var(--window-fg-color) 10%, var(--window-bg-color))",
    "surface-container-highest": "color-mix(in srgb, var(--window-fg-color) 14%, var(--window-bg-color))",
    "outline": "color-mix(in srgb, var(--window-fg-color) 45%, transparent)",
    "outline-variant": "color-mix(in srgb, var(--window-fg-color) 15%, transparent)",
    "shadow": "black",
}


def v(name: str) -> str:
    return f"var(--md-{name}, {FALLBACKS[name]})"


def mix(name: str, pct: int, other: str = "transparent") -> str:
    return f"color-mix(in srgb, {v(name)} {pct}%, {other})"


# motion (cubic-bezier approximations of the M3E springs; y > 1 = overshoot)
FAST_SPATIAL = "350ms cubic-bezier(0.42, 1.67, 0.21, 0.90)"
SPATIAL = "500ms cubic-bezier(0.38, 1.21, 0.22, 1.00)"
EFFECTS = "200ms cubic-bezier(0.34, 0.80, 0.34, 1.00)"
FAST_EFFECTS = "150ms cubic-bezier(0.31, 0.94, 0.34, 1.00)"

FONT = '"Google Sans Flex", "Google Sans", "Roboto Flex", "Inter", sans-serif'

CSS = f"""
/* ------------------------------------------------------------ base */
window.mass {{
  font-family: {FONT};
  background-color: {v('surface')};
  color: {v('on-surface')};
}}
window.mass label {{ font-feature-settings: "tnum" 0; }}
.mass .numeric {{ font-feature-settings: "tnum"; }}

/* type scale */
.mass .display-l {{ font-size: 57px; font-weight: 800; letter-spacing: -1.5px;
  font-variation-settings: "ROND" 100, "wdth" 100; }}
.mass .display-m {{ font-size: 45px; font-weight: 800; letter-spacing: -1px;
  font-variation-settings: "ROND" 100; }}
.mass .display-s {{ font-size: 36px; font-weight: 750; letter-spacing: -0.5px;
  font-variation-settings: "ROND" 100; }}
.mass .headline-l {{ font-size: 32px; font-weight: 700; letter-spacing: -0.25px; }}
.mass .headline-m {{ font-size: 28px; font-weight: 700; }}
.mass .headline-s {{ font-size: 24px; font-weight: 700; }}
.mass .title-l {{ font-size: 22px; font-weight: 600; }}
.mass .title-m {{ font-size: 16px; font-weight: 650; letter-spacing: 0.1px; }}
.mass .title-s {{ font-size: 14px; font-weight: 650; }}
.mass .label-l {{ font-size: 14px; font-weight: 600; letter-spacing: 0.1px; }}
.mass .label-m {{ font-size: 12px; font-weight: 600; letter-spacing: 0.5px; }}
.mass .overline {{ font-size: 12px; font-weight: 700; letter-spacing: 1.4px; }}
.mass .body-m {{ font-size: 14px; }}
.mass .on-variant {{ color: {v('on-surface-variant')}; }}
.mass .primary-text {{ color: {v('primary')}; }}

/* ------------------------------------------------------------ shapes */
.mass .shape-art {{ color: {v('secondary-container')}; }}
.mass .wavy-seekbar {{ color: {v('primary')}; }}
.mass .mini-player .wavy-seekbar {{ color: {v('primary')}; }}

/* ------------------------------------------------------------ navigation rail */
.mass .rail {{ padding: 10px 6px; background: {v('surface')}; }}
.mass .rail-fab {{
  min-width: 44px; min-height: 44px; border-radius: 14px; padding: 0;
  background: {v('primary-container')}; color: {v('on-primary-container')};
  box-shadow: 0 2px 6px {mix('shadow', 18)};
  transition: border-radius {FAST_SPATIAL}, background {EFFECTS};
}}
.mass .rail-fab:hover {{ border-radius: 18px; }}
.mass .rail-fab:active {{ border-radius: 22px; }}
.mass .rail-fab.playing {{ background: {v('primary')}; color: {v('on-primary')}; border-radius: 22px; }}
.mass .rail-item {{ background: none; box-shadow: none; padding: 0; min-height: 0; border-radius: 0; }}
.mass .rail-item .indicator {{
  min-width: 48px; min-height: 36px; border-radius: 999px;
  transition: background {EFFECTS}, min-width {FAST_SPATIAL}, border-radius {FAST_SPATIAL};
}}
.mass .rail-item:hover .indicator {{ background: {mix('on-surface', 8)}; }}
.mass .rail-item:active .indicator {{ border-radius: 10px; }}
.mass .rail-item.active .indicator {{
  background: {v('secondary-container')}; color: {v('on-secondary-container')};
}}
.mass .rail-item .rail-label {{ font-size: 12px; font-weight: 600; color: {v('on-surface-variant')}; }}
.mass .rail-item.active .rail-label {{ color: {v('on-surface')}; font-weight: 800; }}

/* content sits on a raised container, like DMS popouts */
.mass .content-surface {{
  background: {v('surface-container-low')};
  border-radius: 28px 0 0 0;
}}
.mass .content-surface.narrow {{ border-radius: 0; }}

/* ------------------------------------------------------------ buttons */
.mass button.m3 {{
  min-height: 40px; padding: 0 20px; border-radius: 999px; font-weight: 650;
  transition: border-radius {FAST_SPATIAL}, background {EFFECTS}, min-width {FAST_SPATIAL};
}}
.mass button.m3:active {{ border-radius: 12px; }}
.mass button.m3.filled {{ background: {v('primary')}; color: {v('on-primary')}; }}
.mass button.m3.filled:hover {{ background: {mix('primary', 90, v('on-primary'))}; }}
.mass button.m3.tonal {{ background: {v('secondary-container')}; color: {v('on-secondary-container')}; }}
.mass button.m3.tonal:hover {{ background: {mix('secondary-container', 88, v('on-secondary-container'))}; }}
.mass button.m3.outlined {{ background: none; box-shadow: inset 0 0 0 1px {v('outline-variant')}; }}
.mass button.m3.large {{ min-height: 56px; padding: 0 28px; font-size: 16px; }}
.mass button.m3.icon {{ min-width: 40px; min-height: 40px; padding: 0; }}
.mass button.m3.icon.large {{ min-width: 56px; min-height: 56px; padding: 0; }}
.mass button.m3.flat {{ background: none; }}
.mass button.m3.flat:hover {{ background: {mix('on-surface', 8)}; }}

/* toggle buttons: round when off, morph to square + filled when on */
.mass button.m3.toggle {{ background: {v('surface-container-highest')}; color: {v('on-surface-variant')}; }}
.mass button.m3.toggle.on {{ background: {v('primary')}; color: {v('on-primary')}; border-radius: 16px; }}

/* the hero play button: squircle when paused, pill (and wider) when playing */
.mass button.play-hero {{
  min-width: 96px; min-height: 80px; padding: 0; border-radius: 28px;
  background: {v('primary')}; color: {v('on-primary')};
  box-shadow: 0 4px 14px {mix('primary', 35)};
  transition: border-radius {SPATIAL}, min-width {SPATIAL}, background {EFFECTS};
}}
.mass button.play-hero image {{ -gtk-icon-size: 32px; }}
.mass button.play-hero:hover {{ background: {mix('primary', 90, v('on-primary'))}; }}
.mass button.play-hero.playing {{ min-width: 128px; border-radius: 999px; }}
.mass button.play-hero:active {{ border-radius: 16px; }}

/* connected button group: outer corners full, inner corners small */
.mass .btn-group {{ border-spacing: 4px; }}
.mass .btn-group > button {{
  border-radius: 12px; min-height: 56px; min-width: 56px; padding: 0;
  background: {v('secondary-container')}; color: {v('on-secondary-container')};
  transition: border-radius {FAST_SPATIAL}, background {EFFECTS};
}}
.mass .btn-group > button:hover {{ background: {mix('secondary-container', 88, v('on-secondary-container'))}; }}
.mass .btn-group > button:first-child {{ border-radius: 999px 12px 12px 999px; }}
.mass .btn-group > button:last-child {{ border-radius: 12px 999px 999px 12px; }}
.mass .btn-group > button:active {{ border-radius: 8px; }}
.mass .btn-group > button.on {{ background: {v('primary')}; color: {v('on-primary')}; }}
.mass .btn-group > button.play-hero {{
  background: {v('primary')}; color: {v('on-primary')}; border-radius: 28px; min-width: 96px; min-height: 80px;
}}
.mass .btn-group > button.play-hero:hover {{ background: {mix('primary', 90, v('on-primary'))}; }}
.mass .btn-group > button.play-hero.playing {{ border-radius: 999px; min-width: 128px; }}
.mass .btn-group > button.play-hero:active {{ border-radius: 16px; }}
.mass .btn-group > button image {{ -gtk-icon-size: 22px; }}

/* segmented chips (library tabs) */
.mass .seg {{ border-spacing: 2px; }}
.mass .seg > togglebutton {{
  min-height: 40px; padding: 0 18px; border-radius: 8px; font-weight: 650;
  background: {v('surface-container-high')}; color: {v('on-surface-variant')};
  transition: border-radius {FAST_SPATIAL}, background {EFFECTS};
}}
.mass .seg > togglebutton:first-child {{ border-radius: 999px 8px 8px 999px; }}
.mass .seg > togglebutton:last-child {{ border-radius: 8px 999px 999px 8px; }}
.mass .seg > togglebutton:checked {{
  border-radius: 999px; background: {v('secondary-container')}; color: {v('on-secondary-container')};
}}

.mass .chip {{
  min-height: 32px; padding: 0 14px; border-radius: 8px; font-weight: 600;
  background: none; box-shadow: inset 0 0 0 1px {v('outline-variant')};
  transition: border-radius {FAST_SPATIAL}, background {EFFECTS};
}}
.mass .chip:hover {{ background: {mix('on-surface', 8)}; }}
.mass .chip.on, .mass .chip:checked {{
  background: {v('secondary-container')}; color: {v('on-secondary-container')}; box-shadow: none;
  border-radius: 16px;
}}
.mass .player-chip {{
  min-height: 36px; padding: 0 16px 0 10px; border-radius: 999px;
  background: {v('tertiary-container')}; color: {v('on-tertiary-container')}; font-weight: 700;
  transition: border-radius {FAST_SPATIAL};
}}
.mass .player-chip:active {{ border-radius: 12px; }}
.mass .dj-chip > button, .mass button.dj-chip {{
  min-height: 36px; padding: 0 14px 0 10px; border-radius: 999px; font-weight: 700;
  background: none; box-shadow: inset 0 0 0 1px {v('outline-variant')};
  transition: border-radius {FAST_SPATIAL}, background {EFFECTS};
}}
.mass .dj-chip.on > button {{ background: {v('secondary-container')}; color: {v('on-secondary-container')};
  box-shadow: none; }}
.mass .dj-chip > button:active {{ border-radius: 12px; }}
.mass .dj-caption {{
  background: {v('tertiary-container')}; color: {v('on-tertiary-container')};
  border-radius: 28px 28px 28px 8px; padding: 18px 22px;
}}
.mass .dj-caption-text {{ font-size: 17px; font-weight: 500; line-height: 1.35; }}
.mass .ai-hero {{
  background: {v('tertiary-container')}; color: {v('on-tertiary-container')};
  border-radius: 48px; padding: 24px 28px;
}}
.mass .ai-hero .on-variant {{ color: {mix('on-tertiary-container', 75)}; }}
.mass .ai-hero .shape-art {{ color: {v('tertiary')}; }}
.mass .ai-card {{
  background: {v('surface-container')}; border-radius: 28px; padding: 18px;
  transition: background {EFFECTS};
}}
.mass .ai-card.on {{ background: {v('secondary-container')}; color: {v('on-secondary-container')}; }}
.mass .ai-card .shape-art {{ color: {v('primary-container')}; }}
.mass list.m3-list > row.dj-row .shape-art {{ color: {v('tertiary-container')}; }}

/* ------------------------------------------------------------ surfaces */
.mass .card {{ background: {v('surface-container')}; border-radius: 28px; box-shadow: none; }}
.mass .hero-card {{
  background: {v('primary-container')}; color: {v('on-primary-container')};
  border-radius: 48px; padding: 28px;
}}
.mass .hero-card .on-variant {{ color: {mix('on-primary-container', 75)}; }}
.mass .hero-card .wavy-seekbar {{ color: {v('on-primary-container')}; }}
.mass .tile {{
  border-radius: 28px; padding: 10px;
  transition: background {EFFECTS};
}}
.mass .tile:hover {{ background: {v('surface-container-high')}; }}
.mass gridview, .mass gridview > child {{ background: none; }}
.mass gridview > child {{ border-radius: 28px; padding: 0; }}
.mass gridview > child:hover {{ background: {v('surface-container-high')}; }}
.mass gridview > child:focus-visible {{ outline: 2px solid {v('primary')}; outline-offset: -2px; }}

/* segmented list: tight gaps, small inner radii, large outer radii */
.mass list.m3-list {{ background: none; border-spacing: 2px; }}
.mass list.m3-list > row {{
  background: {v('surface-container')}; border-radius: 6px; margin-bottom: 2px;
  min-height: 64px; transition: background {EFFECTS}, border-radius {FAST_SPATIAL};
}}
.mass list.m3-list > row:first-child {{ border-top-left-radius: 24px; border-top-right-radius: 24px; }}
.mass list.m3-list > row:last-child {{ border-bottom-left-radius: 24px; border-bottom-right-radius: 24px; }}
.mass list.m3-list > row:hover {{ background: {v('surface-container-high')}; }}
.mass list.m3-list > row:active {{ border-radius: 20px; }}
.mass list.m3-list > row.current {{
  background: {v('primary-container')}; color: {v('on-primary-container')}; border-radius: 24px;
}}
.mass list.m3-list > row.current .on-variant {{ color: {mix('on-primary-container', 75)}; }}
.mass list.m3-list > row .title-m {{ font-size: 15px; }}
/* the same segmented look on recycling ListViews: rows are reused, so the look lives on our own
   .qrow box (first/last/current are classes set at bind time) and GTK's row stays bare */
.mass listview.m3-list {{ background: none; }}
.mass listview.m3-list.full {{ padding: 8px 20px 120px; }}
.mass listview.m3-list.compact {{ padding: 8px 12px 16px; }}
.mass listview.m3-list > row {{ background: none; padding: 0; margin-bottom: 2px; }}
.mass listview.m3-list > row:focus-visible > .qrow {{ outline: 2px solid {v('primary')}; outline-offset: -2px; }}
.mass .qrow {{
  background: {v('surface-container')}; border-radius: 6px; min-height: 64px; padding: 8px 8px 8px 12px;
  transition: background {EFFECTS}, border-radius {FAST_SPATIAL};
}}
.mass .qrow.first {{ border-top-left-radius: 24px; border-top-right-radius: 24px; }}
.mass .qrow.last {{ border-bottom-left-radius: 24px; border-bottom-right-radius: 24px; }}
.mass listview.m3-list > row:hover > .qrow {{ background: {v('surface-container-high')}; }}
.mass listview.m3-list > row:active > .qrow {{ border-radius: 20px; }}
.mass .qrow.current {{
  background: {v('primary-container')}; color: {v('on-primary-container')}; border-radius: 24px;
}}
.mass .qrow.current .on-variant {{ color: {mix('on-primary-container', 75)}; }}
.mass .qrow .title-m {{ font-size: 15px; }}
.mass .qrow.dj-row .shape-art {{ color: {v('tertiary-container')}; }}

/* ------------------------------------------------------------ search */
.mass .search-bar {{
  min-height: 56px; border-radius: 999px; padding: 0 20px;
  background: {v('surface-container-high')}; box-shadow: none; outline: none; font-size: 16px;
  transition: background {EFFECTS};
}}
.mass .search-bar:focus-within {{ background: {v('surface-container-highest')}; }}

/* ------------------------------------------------------------ mini player (floating toolbar) */
.mass .mini-player {{
  background: {v('surface-container-highest')}; color: {v('on-surface')};
  border-radius: 999px; padding: 8px 8px 8px 8px;
  box-shadow: 0 6px 24px {mix('shadow', 30)}, 0 1px 3px {mix('shadow', 20)};
}}
.mass .mini-player button.m3.icon {{ min-width: 44px; min-height: 44px; }}
.mass .mini-player button.play-mini {{
  min-width: 56px; min-height: 48px; padding: 0; border-radius: 16px;
  background: {v('primary')}; color: {v('on-primary')};
  transition: border-radius {FAST_SPATIAL}, min-width {FAST_SPATIAL};
}}
.mass .mini-player button.play-mini.playing {{ border-radius: 999px; min-width: 64px; }}
.mass .mini-player button.play-mini:active {{ border-radius: 10px; }}
.mass .mini-player .open-target {{ border-radius: 999px; padding: 0 8px 0 0; }}
.mass .mini-player .open-target:hover {{ background: {mix('on-surface', 6)}; }}

/* ------------------------------------------------------------ now playing */
.mass .np-page {{ background: none; }}
.mass .np-title {{ font-size: 30px; font-weight: 800; letter-spacing: -0.5px;
  font-variation-settings: "ROND" 100; }}
.mass .np-artist {{ font-size: 18px; font-weight: 650; color: {v('primary')}; }}
.mass .np-album {{ font-size: 15px; color: {v('on-surface-variant')}; }}
.mass .np-artist link, .mass .np-album link {{ color: inherit; text-decoration: none; }}
.mass .np-artist link:hover, .mass .np-album link:hover {{ text-decoration: underline; }}

/* expressive slider (volume) */
.mass scale.m3-slider {{ padding: 0; }}
.mass scale.m3-slider trough {{
  min-height: 16px; border-radius: 8px; background: {v('secondary-container')};
}}
.mass scale.m3-slider highlight {{ min-height: 16px; border-radius: 8px 2px 2px 8px; background: {v('primary')}; }}
.mass scale.m3-slider slider {{
  min-width: 4px; min-height: 40px; margin: -12px -2px; border-radius: 2px;
  background: {v('primary')}; box-shadow: 0 0 0 4px {v('surface')}; outline: none;
  transition: min-width {FAST_SPATIAL};
}}
.mass scale.m3-slider:active slider {{ min-width: 2px; }}

/* misc */
.mass .badge {{ background: {v('tertiary-container')}; color: {v('on-tertiary-container')};
  border-radius: 999px; padding: 2px 10px; font-size: 11px; font-weight: 800; letter-spacing: 0.4px; }}
.mass .section-title {{ font-size: 22px; font-weight: 750; letter-spacing: -0.2px; }}
.mass popover > contents {{ border-radius: 24px; }}
.mass .players-list > row.active {{ background: {v('secondary-container')}; color: {v('on-secondary-container')}; }}
.mass headerbar.flat-header {{ background: none; box-shadow: none; }}
"""

CSS += """
.mass .msym {
  font-family: "Material Symbols Rounded";
  font-feature-settings: "liga";
  font-variation-settings: "FILL" 0, "wght" 500, "GRAD" 0, "opsz" 24;
  font-size: 24px; min-width: 24px; line-height: 1;
}
.mass .msym.fill { font-variation-settings: "FILL" 1, "wght" 500, "GRAD" 0, "opsz" 24; }
.mass .msym-16 { font-size: 16px; min-width: 16px; }
.mass .msym-18 { font-size: 18px; min-width: 18px; }
.mass .msym-20 { font-size: 20px; min-width: 20px; }
.mass .msym-22 { font-size: 22px; min-width: 22px; }
.mass .msym-28 { font-size: 28px; min-width: 28px; }
.mass .msym-32 { font-size: 32px; min-width: 32px; font-variation-settings: "FILL" 1, "wght" 600, "opsz" 40; }
.mass .msym-40 { font-size: 40px; min-width: 40px; font-variation-settings: "FILL" 1, "wght" 600, "opsz" 48; }
.mass .rail-item.active .msym { font-variation-settings: "FILL" 1, "wght" 600, "GRAD" 0, "opsz" 24; }
.mass button.toggle.on .msym, .mass .btn-group > button.on .msym {
  font-variation-settings: "FILL" 1, "wght" 600, "GRAD" 0, "opsz" 24; }
"""

CSS += f"""
/* ---------- now playing option chips (MASS parity) */
.mass button.np-tool {{
  min-height: 32px; padding: 0 12px 0 10px; border-radius: 999px; font-size: 13px; font-weight: 700;
  background: none; color: {v('on-surface-variant')}; box-shadow: inset 0 0 0 1px {v('outline-variant')};
  transition: border-radius {FAST_SPATIAL}, background {EFFECTS}, color {EFFECTS};
}}
.mass button.np-tool:hover {{ background: {mix('on-surface', 8)}; }}
.mass button.np-tool:active {{ border-radius: 10px; }}
.mass button.np-tool.on, .mass button.np-tool:checked {{
  background: {v('secondary-container')}; color: {v('on-secondary-container')}; box-shadow: none;
}}
.mass .np-badge {{
  min-height: 32px; padding: 0 12px; border-radius: 999px; font-size: 12px; font-weight: 800;
  letter-spacing: 0.06em; color: {v('on-surface-variant')}; box-shadow: inset 0 0 0 1px {v('outline-variant')};
}}
.mass .np-badge .dot {{ min-width: 8px; min-height: 8px; border-radius: 999px; background: {v('outline')}; }}
.mass .np-badge.hires {{ color: {v('on-tertiary-container')}; background: {v('tertiary-container')}; box-shadow: none; }}
.mass .np-badge.hires .dot {{ background: {v('tertiary')}; }}

/* ---------- lyrics */
.mass .lyrics, .mass .lyrics > viewport {{ background: none; }}
.mass .lyrics label.lyric {{
  font-size: 26px; font-weight: 800; line-height: 1.25; color: {mix('on-surface', 38)};
  transition: color 300ms cubic-bezier(0.34, 0.8, 0.34, 1);
}}
.mass .lyrics button.lyric-line {{
  background: none; box-shadow: none; padding: 4px 10px; border-radius: 16px; min-height: 0;
  transition: background {EFFECTS};
}}
.mass .lyrics button.lyric-line:hover {{ background: {mix('on-surface', 6)}; }}
.mass .lyrics button.lyric-line.past label.lyric {{ color: {mix('on-surface', 24)}; }}
.mass .lyrics button.lyric-line.current label.lyric {{ color: {v('on-surface')}; }}
.mass .lyrics label.lyric.plain {{ font-size: 20px; font-weight: 600; color: {v('on-surface')}; }}
.mass .lyrics label.lyric.dim {{ font-size: 20px; color: {v('on-surface-variant')}; }}
"""

CSS += f"""
/* ---------- now playing, option C: player card + tabbed panel */
.mass .np-card {{
  padding: 20px 24px 22px; border-radius: 32px;
  background: {mix('primary-container', 45, v('surface-container'))};
}}
.mass .np-card .player-chip {{ background: {mix('primary-container', 80, v('surface-container-high'))};
  color: {v('on-primary-container')}; }}
.mass .np-card .np-badge {{ min-height: 28px; }}
.mass .np-panel {{ border-radius: 32px; background: {v('surface-container-low')}; }}
.mass button.np-tab {{
  min-height: 40px; padding: 0 18px; border-radius: 999px; font-size: 15px; font-weight: 650;
  background: none; box-shadow: none; color: {v('on-surface-variant')};
  transition: background {EFFECTS}, color {EFFECTS}, border-radius {FAST_SPATIAL};
}}
.mass button.np-tab:hover {{ background: {mix('on-surface', 8)}; }}
.mass button.np-tab:active {{ border-radius: 12px; }}
.mass button.np-tab:checked {{ background: {v('secondary-container')}; color: {v('on-secondary-container')}; }}
.mass button.np-fav {{ min-width: 52px; min-height: 52px; }}
.mass button.np-fav.on {{ border-radius: 999px; }}
.mass scale.m3-slider.thin trough {{ min-height: 10px; border-radius: 5px; }}
.mass scale.m3-slider.thin highlight {{ min-height: 10px; border-radius: 5px 2px 2px 5px; }}
.mass scale.m3-slider.thin slider {{ min-height: 28px; margin: -9px -2px; }}
.mass menubutton.np-tool > button {{
  min-height: 32px; padding: 0 12px 0 10px; border-radius: 999px; font-size: 13px; font-weight: 700;
  background: none; color: {v('on-surface-variant')}; box-shadow: inset 0 0 0 1px {v('outline-variant')};
  transition: border-radius {FAST_SPATIAL}, background {EFFECTS}, color {EFFECTS};
}}
.mass menubutton.np-tool > button:hover {{ background: {mix('on-surface', 8)}; }}
.mass menubutton.np-tool.on > button, .mass menubutton.np-tool > button:checked {{
  background: {v('secondary-container')}; color: {v('on-secondary-container')}; box-shadow: none;
}}
.mass .np-panel .lyrics label.lyric {{ font-size: 30px; }}
.mass .np-panel list.m3-list {{ background: none; }}
"""

CSS += f"""
/* ---------- quality badge → signal path popover */
.mass menubutton.np-badge {{ padding: 0; }}
.mass menubutton.np-badge > button {{
  min-height: 32px; padding: 0 12px; border-radius: 999px; background: none; box-shadow: none; color: inherit;
  transition: background {EFFECTS}, border-radius {FAST_SPATIAL};
}}
.mass menubutton.np-badge > button:hover {{ background: {mix('on-surface', 8)}; }}
.mass menubutton.np-badge > button:checked {{ background: {mix('on-surface', 12)}; }}
.mass menubutton.np-badge > button:active {{ border-radius: 10px; }}
.mass popover.signal-path-popover > contents {{
  padding: 16px 18px 12px; border-radius: 24px; min-width: 300px;
  background: {v('surface-container-high')}; color: {v('on-surface')};
}}
.mass .signal-path .sp-stage {{ margin-top: 10px; }}
.mass .signal-path .sp-stage:first-child {{ margin-top: 0; }}
.mass .signal-path .sp-dot {{ min-width: 10px; min-height: 10px; border-radius: 999px; background: {v('outline')}; }}
.mass .signal-path .sp-dot.lit {{ background: {v('primary')}; }}
.mass .signal-path .sp-title {{ font-size: 12px; font-weight: 800; letter-spacing: 0.08em; }}
.mass .signal-path .sp-quality {{ font-size: 12px; color: {v('on-surface-variant')}; }}
.mass .signal-path .sp-row {{
  margin-left: 4px; padding: 7px 0 7px 16px; border-left: 2px solid {v('outline-variant')};
}}
.mass .signal-path .sp-row-title {{ font-weight: 700; }}
.mass .signal-path .sp-row-sub {{ font-size: 12px; color: {v('on-surface-variant')}; }}
.mass .signal-path .sp-info {{ color: {v('on-surface-variant')}; min-width: 32px; min-height: 32px; padding: 0; }}
.mass .signal-path .sp-info:checked {{ color: {v('primary')}; }}
.mass .signal-path .sp-tip {{
  /* continues the rail, text lined up under the row title */
  font-size: 13px; color: {v('on-surface-variant')};
  margin-left: 4px; padding: 0 0 10px 48px; border-left: 2px solid {v('outline-variant')};
}}
.mass .signal-path .sp-chip {{
  font-size: 11px; font-weight: 700; padding: 1px 8px; border-radius: 999px;
  background: {v('tertiary-container')}; color: {v('on-tertiary-container')};
}}
"""

CSS += f"""
/* ---------- M3E press feedback: buttons squish on press and spring back with overshoot */
.mass button.m3, .mass button.np-tool, .mass menubutton.np-badge > button, .mass .tile {{
  transition: transform {FAST_SPATIAL}, border-radius {FAST_SPATIAL}, min-width {FAST_SPATIAL},
              background {EFFECTS}, color {EFFECTS};
}}
.mass button.m3:active, .mass button.np-tool:active, .mass menubutton.np-badge > button:active {{
  transform: scale(0.92);
}}
.mass button.m3.large:active {{ transform: scale(0.94); }}
.mass flowboxchild:active .tile {{ transform: scale(0.96); }}
"""

CSS += f"""
/* ---------- DJ editor text areas */
.mass .dj-text-card {{ border-radius: 16px; }}
.mass textview.dj-text, .mass textview.dj-text > text {{ background: none; font-size: 14px; }}
"""

CSS += f"""
/* ---------- startup splash */
.mass .splash {{ color: {v('surface')}; }}  /* backdrop, drawn by Splash.do_snapshot */
.mass .splash-shape {{ color: {v('primary-container')}; }}
.mass .splash-title {{ font-size: 32px; font-weight: 800; letter-spacing: -0.5px; color: {v('on-surface')}; }}
.mass .splash-status {{ font-size: 14px; color: {v('on-primary-container')}; }}
"""
