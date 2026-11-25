"""System & artwork theming (matugen, DankMaterialShell, Omarchy).

Palette sources, in order of preference for "system" mode:
  1. $XDG_CONFIG_HOME/mass/colors.json         - bundled matugen template
  2. $XDG_CONFIG_HOME/omarchy/current/theme/colors.toml - active Omarchy theme
  3. $XDG_CACHE_HOME/dms/dms-colors.json       - DankMaterialShell's matugen output
All are watched; the app re-themes live whenever the theme or wallpaper changes.

"artwork" mode runs `matugen image <cover> --dry-run -j hex` on the now-playing cover.

The resulting Material 3 palette is mapped onto libadwaita's CSS variables and
a set of app-specific classes, and installed above the user's gtk.css.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import tomllib
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from .config import CACHE_DIR, CONFIG_DIR
from .host import HOST_CACHE, HOST_CONFIG, host_cmd
from .ui.style import CSS as STYLE_CSS
from .ui.style import FALLBACKS, SCROLLBAR_CSS
from .ui.winamp import CSS as WINAMP_CSS
from .ui.winamp import PALETTE as WINAMP_PALETTE

log = logging.getLogger(__name__)

APP_COLORS = CONFIG_DIR / "colors.json"
OMARCHY_CURRENT = HOST_CONFIG / "omarchy" / "current"
OMARCHY_COLORS = OMARCHY_CURRENT / "theme" / "colors.toml"
DMS_COLORS = HOST_CACHE / "dms" / "dms-colors.json"
ART_DIR = CACHE_DIR / "art"
ART_CACHE_MAX = 200  # palette files kept; older ones are cheap to regenerate
_EMPTY_MATUGEN_CFG = CACHE_DIR / "matugen-empty.toml"

Palette = dict[str, str]  # token -> "#rrggbb"

_active_palette: Palette = {}


def active_palette() -> Palette:
    """The palette currently applied (empty in Plain Adwaita mode)."""
    return _active_palette


def _prune_art_cache() -> None:
    """Keep only the most recent artwork palettes, so the cache doesn't grow forever."""
    try:
        files = sorted(ART_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[ART_CACHE_MAX:]:
            old.unlink(missing_ok=True)
    except OSError as err:
        log.debug("could not prune %s: %s", ART_DIR, err)


def _parse_palette_file(path: Path) -> tuple[str | None, dict[str, Palette]] | None:
    """Return (preferred_mode, {"dark": {...}, "light": {...}}) from any known format."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    colors = data.get("colors", data)
    out: dict[str, Palette] = {}
    if isinstance(colors, dict) and "dark" in colors and isinstance(colors["dark"], dict):
        # {"dark": {token: hex}, "light": {...}}  (DMS + our template)
        out = {m: colors[m] for m in ("dark", "light") if m in colors}
    elif isinstance(colors, dict):
        # raw matugen -j hex: {token: {"dark": {"color": hex}, "light": {...}}}
        for mode in ("dark", "light"):
            out[mode] = {
                tok: v[mode]["color"] for tok, v in colors.items() if isinstance(v, dict) and mode in v
            }
    if not out.get("dark") and not out.get("light"):
        return None
    mode = data.get("mode") if data.get("mode") in ("dark", "light") else None
    return mode, out


_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


def _norm_hex(val: object) -> str | None:
    if isinstance(val, str) and _HEX_RE.match(val.strip()):
        s = val.strip()
        return s if s.startswith("#") else f"#{s}"
    return None


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _mix_hex(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    r = round(r1 + (r2 - r1) * t)
    g = round(g1 + (g2 - g1) * t)
    b = round(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _luminance(h: str) -> float:
    def ch(c: int) -> float:
        v = c / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = _hex_to_rgb(h)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast_fg(bg: str) -> str:
    return "#111111" if _luminance(bg) > 0.36 else "#ffffff"


def _parse_omarchy_file(path: Path) -> tuple[str, dict[str, Palette]] | None:
    """Parse an Omarchy `colors.toml` into (mode, {mode: Material 3 Palette})."""
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, ValueError):
        return None
    bg = _norm_hex(data.get("background"))
    fg = _norm_hex(data.get("foreground"))
    accent = _norm_hex(data.get("accent")) or _norm_hex(data.get("color4")) or _norm_hex(data.get("color6"))
    if not (bg and fg and accent):
        return None

    is_light = data.get("mode") == "light" or (path.parent / "light.mode").exists() or _luminance(bg) > 0.45
    mode = "light" if is_light else "dark"
    secondary = _norm_hex(data.get("color6")) or _norm_hex(data.get("color5")) or _mix_hex(accent, fg, 0.35)
    tertiary = _norm_hex(data.get("color5")) or _norm_hex(data.get("color3")) or secondary
    error = _norm_hex(data.get("color1")) or "#f25e86"
    surface_lowest = "#ffffff" if is_light else _mix_hex(bg, "#000000", 0.22)
    palette: Palette = {
        "primary": accent,
        "on_primary": _contrast_fg(accent),
        "primary_container": _mix_hex(accent, bg, 0.70),
        "on_primary_container": fg,
        "inverse_primary": _mix_hex(accent, bg, 0.45),
        "secondary": secondary,
        "on_secondary": _contrast_fg(secondary),
        "secondary_container": _mix_hex(secondary, bg, 0.80),
        "on_secondary_container": fg,
        "tertiary": tertiary,
        "on_tertiary": _contrast_fg(tertiary),
        "tertiary_container": _mix_hex(tertiary, bg, 0.75),
        "on_tertiary_container": fg,
        "error": error,
        "on_error": _contrast_fg(error),
        "error_container": _mix_hex(error, bg, 0.72),
        "on_error_container": fg,
        "background": bg,
        "on_background": fg,
        "surface": bg,
        "on_surface": fg,
        "surface_variant": _mix_hex(bg, fg, 0.12),
        "on_surface_variant": _mix_hex(fg, bg, 0.28),
        "surface_dim": _mix_hex(bg, fg if is_light else "#000000", 0.12),
        "surface_bright": _mix_hex(bg, fg, 0.14),
        "surface_tint": accent,
        "surface_container_lowest": surface_lowest,
        "surface_container_low": _mix_hex(bg, fg, 0.04),
        "surface_container": _mix_hex(bg, fg, 0.08),
        "surface_container_high": _mix_hex(bg, fg, 0.12),
        "surface_container_highest": _mix_hex(bg, fg, 0.17),
        "outline": _mix_hex(fg, bg, 0.55),
        "outline_variant": _mix_hex(fg, bg, 0.84),
        "shadow": "#000000",
    }
    return mode, {mode: palette}


def _adw_vars(p: Palette) -> dict[str, str]:
    g = lambda k, fb="surface": p.get(k) or p.get(fb, "#808080")  # noqa: E731
    return {
        "accent-bg-color": g("primary"),
        "accent-fg-color": g("on_primary"),
        "accent-color": g("primary"),
        "destructive-bg-color": g("error"),
        "destructive-fg-color": g("on_error"),
        "destructive-color": g("error"),
        "error-bg-color": g("error"),
        "error-fg-color": g("on_error"),
        "error-color": g("error"),
        "window-bg-color": g("surface"),
        "window-fg-color": g("on_surface"),
        "view-bg-color": g("surface"),
        "view-fg-color": g("on_surface"),
        "headerbar-bg-color": g("surface"),
        "headerbar-fg-color": g("on_surface"),
        "headerbar-backdrop-color": g("surface"),
        "sidebar-bg-color": g("surface_container_low"),
        "sidebar-fg-color": g("on_surface"),
        "sidebar-backdrop-color": g("surface_container_low"),
        "secondary-sidebar-bg-color": g("surface_container"),
        "secondary-sidebar-fg-color": g("on_surface"),
        "card-bg-color": g("surface_container"),
        "card-fg-color": g("on_surface"),
        "dialog-bg-color": g("surface_container_high"),
        "dialog-fg-color": g("on_surface"),
        "popover-bg-color": g("surface_container_high"),
        "popover-fg-color": g("on_surface"),
        "thumbnail-bg-color": g("surface_container_high"),
        "thumbnail-fg-color": g("on_surface"),
        "overview-bg-color": g("surface_container_low"),
        "overview-fg-color": g("on_surface"),
    }


def build_css(p: Palette) -> str:
    """Variables only: libadwaita's own + every Material token as --md-*."""
    adw = _adw_vars(p)
    lines = [":root {"]
    lines += [f"  --{k}: {v};" for k, v in adw.items()]
    lines += [f"  --md-{k.replace('_', '-')}: {v};" for k, v in p.items()]
    lines.append("}")
    # legacy named colors, for anything still using @accent_bg_color etc.
    lines += [f"@define-color {k.replace('-', '_')} {v};" for k, v in adw.items()]
    return "\n".join(lines)


_MD_SURFACES = ("background", "surface", "surface_dim", "surface_bright", "surface_container_lowest",
                "surface_container_low", "surface_container", "surface_container_high",
                "surface_container_highest")
_ADW_SURFACES = ("window-bg-color", "view-bg-color", "headerbar-bg-color", "headerbar-backdrop-color",
                 "sidebar-bg-color", "sidebar-backdrop-color", "secondary-sidebar-bg-color",
                 "card-bg-color", "overview-bg-color")


def translucency_css(p: Palette, transparency: int, blur: bool) -> str:
    """Fade the main window's surfaces; popovers, dialogs and the mini player stay opaque.

    Popovers are their own Wayland surfaces with no blur, and dialogs and the mini
    player float over content, so all get the opaque values back. Stacked panels
    compound their alpha, which keeps higher elevations more solid.
    """
    if transparency <= 0:
        return ""
    opaque: dict[str, str] = {}
    for tok in _MD_SURFACES:
        if val := p.get(tok) or FALLBACKS.get(tok.replace("_", "-")):
            opaque[f"--md-{tok.replace('_', '-')}"] = val
    if p:  # Plain Adwaita mode has no hex values to fade without a var() cycle
        adw = _adw_vars(p)
        opaque |= {f"--{k}": adw[k] for k in _ADW_SURFACES}
    keep = 100 - min(transparency, 90)
    faded = [f"  {k}: color-mix(in srgb, {v} {keep}%, transparent);" for k, v in opaque.items()]
    if blur:
        # GTK turns this on a toplevel into an ext-background-effect-v1 blur region;
        # the compositor picks the strength (niri: the `blur {}` config section)
        faded.append("  backdrop-filter: blur(24px);")
    restored = [f"  {k}: {v};" for k, v in opaque.items()]
    floating = "window.mass popover, window.mass dialog, window.mass .mini-player"
    return "\n".join(["window.mass {", *faded, "}", f"{floating} {{", *restored, "}"])


class ThemeManager(GObject.Object):
    """Owns the app-wide CSS provider and the palette lifecycle."""

    __gsignals__ = {"palette-changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, config) -> None:
        super().__init__()
        self.config = config
        self.palette: Palette = {}
        self.source = "none"
        self._provider = Gtk.CssProvider()
        self._monitors: list[Gio.FileMonitor] = []
        self._art_key: str | None = None
        self._art_cancel: Gio.Cancellable | None = None
        self._style = Adw.StyleManager.get_default()
        self._style.connect("notify::dark", lambda *_: self.reload())

        display = Gdk.Display.get_default()
        static = Gtk.CssProvider()
        static.load_from_string(STYLE_CSS)
        Gtk.StyleContext.add_provider_for_display(display, static, Gtk.STYLE_PROVIDER_PRIORITY_USER + 10)
        # above USER (gtk.css from DMS), so live changes win over the stale startup copy
        Gtk.StyleContext.add_provider_for_display(
            display, self._provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 10
        )
        for path in (APP_COLORS, DMS_COLORS):
            self._watch(path)
        if OMARCHY_CURRENT.exists():
            self._watch(OMARCHY_CURRENT / "theme", create=False)
            if OMARCHY_COLORS.parent.exists():
                self._watch(OMARCHY_COLORS, create=False)
        self.reload()

    # ------------------------------------------------------------------ file watching

    def _watch(self, path: Path, create: bool = True) -> None:
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
        elif not path.parent.exists():
            return
        mon = Gio.File.new_for_path(str(path.parent)).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        mon.set_rate_limit(250)

        def changed(_m, f, other, _event):
            names = {f.get_basename() if f else None, other.get_basename() if other else None}
            if path.name in names and self.config.theme_mode == "system":
                GLib.timeout_add(150, lambda: (self.reload(), False)[1])

        mon.connect("changed", changed)
        self._monitors.append(mon)

    # ------------------------------------------------------------------ palette resolution

    @property
    def mode(self) -> str:
        return "dark" if self._style.get_dark() else "light"

    def reload(self) -> None:
        tm = self.config.theme_mode
        if tm == "adwaita":
            self._apply({}, "adwaita")
            return
        if tm == "artwork" and self._art_palette_key and self._art_palettes:
            self._apply(self._art_palettes.get(self.mode, {}), "artwork")
            return
        sources = (
            (APP_COLORS, _parse_palette_file, APP_COLORS.name),
            (OMARCHY_COLORS, _parse_omarchy_file, "omarchy"),
            (DMS_COLORS, _parse_palette_file, DMS_COLORS.name),
        )
        for path, parser, label in sources:
            if parsed := parser(path):
                pref_mode, pals = parsed
                # follow the generator's mode when we're set to follow the system
                if pref_mode and self._style.get_color_scheme() == Adw.ColorScheme.DEFAULT:
                    self._sync_scheme(pref_mode)
                self._apply(pals.get(self.mode) or next(iter(pals.values())), label)
                return
        self._apply({}, "none")

    _art_palettes: dict[str, Palette] = {}
    _art_palette_key: str | None = None
    _art_bytes: bytes | None = None

    def set_mode(self, mode: str) -> None:
        self.config.theme_mode = mode
        self.config.save()
        self._art_palette_key = None  # scheme may have changed too
        if mode == "artwork" and self._art_bytes:
            self.set_artwork(self._art_bytes)
        self.reload()

    def set_translucency(self, transparency: int, blur: bool) -> None:
        self.config.transparency, self.config.blur = transparency, blur
        self._apply(*self._resolved)

    def set_winamp(self, on: bool) -> None:
        self.config.winamp = on
        self.config.save()
        self._apply(*self._resolved)

    def _sync_scheme(self, mode: str) -> None:
        # Only nudge libadwaita if the portal didn't already tell it the right thing.
        want_dark = mode == "dark"
        if self._style.get_dark() != want_dark and not self._style.get_system_supports_color_schemes():
            self._style.set_color_scheme(Adw.ColorScheme.FORCE_DARK if want_dark else Adw.ColorScheme.FORCE_LIGHT)

    def _apply(self, palette: Palette, source: str) -> None:
        global _active_palette
        self._resolved = (palette, source)  # what the skin hides, restored when it's switched off
        winamp = self.config.winamp
        if winamp:
            palette, source = WINAMP_PALETTE, "winamp"
        self.palette, self.source = palette, source
        _active_palette = palette or {}
        css = (build_css(palette) if palette else "") + "\n" + translucency_css(
            palette, self.config.transparency, self.config.blur) + "\n" + SCROLLBAR_CSS
        if winamp:
            css += "\n" + WINAMP_CSS
        if css == getattr(self, "_css", None):
            return  # reloading a provider restyles every widget; skip no-ops
        self._css = css
        self._provider.load_from_string(css)
        log.debug("theme applied from %s (%d tokens)", source, len(palette))
        self.emit("palette-changed")

    # ------------------------------------------------------------------ artwork mode

    def set_artwork(self, image_bytes: bytes | None) -> None:
        """Feed the now-playing cover; regenerates the palette in artwork mode."""
        if not image_bytes:
            return
        self._art_bytes = image_bytes
        key = hashlib.sha1(image_bytes).hexdigest()[:16]
        self._art_key = key
        if self.config.theme_mode != "artwork" or key == self._art_palette_key:
            return
        ART_DIR.mkdir(parents=True, exist_ok=True)
        cached = ART_DIR / f"{key}.{self.config.matugen_scheme}.json"
        if cached.exists() and (parsed := _parse_palette_file(cached)):
            self._art_palettes, self._art_palette_key = parsed[1], key
            self.reload()
            return
        img = ART_DIR / f"{key}.img"
        img.write_bytes(image_bytes)
        if not _EMPTY_MATUGEN_CFG.exists():
            # an empty config so matugen never renders the user's own templates
            _EMPTY_MATUGEN_CFG.write_text("[config]\n[templates]\n")
        if self._art_cancel:
            self._art_cancel.cancel()
        self._art_cancel = Gio.Cancellable()
        try:
            proc = Gio.Subprocess.new(
                host_cmd(["matugen", "image", str(img), "-c", str(_EMPTY_MATUGEN_CFG), "--dry-run", "-q",
                          "-j", "hex", "-t", self.config.matugen_scheme, "--source-color-index", "0"]),
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
        except GLib.Error as err:
            log.warning("matugen not available: %s", err.message)
            return

        def done(p, res, k=key):
            try:
                ok, out, _ = p.communicate_utf8_finish(res)
            except GLib.Error:
                return
            finally:
                img.unlink(missing_ok=True)
            if not ok or k != self._art_key or not out:
                return
            cached.write_text(out)
            _prune_art_cache()
            if parsed := _parse_palette_file(cached):
                self._art_palettes, self._art_palette_key = parsed[1], k
                self.reload()

        proc.communicate_utf8_async(None, self._art_cancel, done)
