"""Matugen theming.

Palette sources, in order of preference for "system" mode:
  1. $XDG_CONFIG_HOME/mass/colors.json  - written by the bundled matugen template
                                          (data/matugen/mass-colors.json), so any
                                          matugen setup can drive the app
  2. $XDG_CACHE_HOME/dms/dms-colors.json - DankMaterialShell's matugen output
Both are watched; the app re-themes live whenever the wallpaper changes.

"artwork" mode runs `matugen image <cover> --dry-run -j hex` on the now-playing cover.

The resulting Material 3 palette is mapped onto libadwaita's CSS variables and
a set of app-specific classes, and installed above the user's gtk.css.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from .config import CACHE_DIR, CONFIG_DIR
from .host import HOST_CACHE, host_cmd
from .ui.style import CSS as STYLE_CSS

log = logging.getLogger(__name__)

APP_COLORS = CONFIG_DIR / "colors.json"
DMS_COLORS = HOST_CACHE / "dms" / "dms-colors.json"
ART_DIR = CACHE_DIR / "art"
_EMPTY_MATUGEN_CFG = CACHE_DIR / "matugen-empty.toml"

Palette = dict[str, str]  # token -> "#rrggbb"


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


def build_css(p: Palette) -> str:
    """Variables only: libadwaita's own + every Material token as --md-*."""
    g = lambda k, fb="surface": p.get(k) or p.get(fb, "#808080")  # noqa: E731
    adw = {
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
    lines = [":root {"]
    lines += [f"  --{k}: {v};" for k, v in adw.items()]
    lines += [f"  --md-{k.replace('_', '-')}: {v};" for k, v in p.items()]
    lines.append("}")
    # legacy named colors, for anything still using @accent_bg_color etc.
    lines += [f"@define-color {k.replace('-', '_')} {v};" for k, v in adw.items()]
    return "\n".join(lines)


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
        Gtk.StyleContext.add_provider_for_display(display, static, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        # above USER (gtk.css from DMS), so live changes win over the stale startup copy
        Gtk.StyleContext.add_provider_for_display(
            display, self._provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 10
        )
        for path in (APP_COLORS, DMS_COLORS):
            self._watch(path)
        self.reload()

    # ------------------------------------------------------------------ file watching

    def _watch(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
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
        for path in (APP_COLORS, DMS_COLORS):
            if parsed := _parse_palette_file(path):
                pref_mode, pals = parsed
                # follow the generator's mode when we're set to follow the system
                if pref_mode and self._style.get_color_scheme() == Adw.ColorScheme.DEFAULT:
                    self._sync_scheme(pref_mode)
                self._apply(pals.get(self.mode) or next(iter(pals.values())), path.name)
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

    def _sync_scheme(self, mode: str) -> None:
        # Only nudge libadwaita if the portal didn't already tell it the right thing.
        want_dark = mode == "dark"
        if self._style.get_dark() != want_dark and not self._style.get_system_supports_color_schemes():
            self._style.set_color_scheme(Adw.ColorScheme.FORCE_DARK if want_dark else Adw.ColorScheme.FORCE_LIGHT)

    def _apply(self, palette: Palette, source: str) -> None:
        self.palette, self.source = palette, source
        css = build_css(palette) if palette else ""
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
            if parsed := _parse_palette_file(cached):
                self._art_palettes, self._art_palette_key = parsed[1], k
                self.reload()

        proc.communicate_utf8_async(None, self._art_cancel, done)
