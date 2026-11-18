"""Material 3 Expressive primitives for GTK4.

- Spring: critically/under-damped spring (stiffness/damping presets from DMS)
- ShapeArt: cover art clipped to an M3E shape (cookie, sunny, clover, squircle, circle)
  that morphs between shapes with spring motion and can slowly rotate while playing
- WavySeekbar: the "squiggly" progress/seek bar whose wave calms down when paused

Everything is drawn with Gsk paths and contributes nothing to layout beyond its
fixed size, so loading art never reflows anything.
"""

from __future__ import annotations

import math

from gi.repository import Gdk, GLib, GObject, Graphene, Gsk, Gtk

# [stiffness, damping] for unit mass, from DankMaterialShell's Theme.springSpecs
SPRINGS = {"expressive": (560.0, 37.0), "fast": (220.0, 23.0), "default": (300.0, 24.0),
           "bouncy": (300.0, 15.0)}


class Spring:
    def __init__(self, value: float = 0.0, preset: str = "default") -> None:
        self.value = self.target = value
        self.velocity = 0.0
        self.k, self.c = SPRINGS[preset]

    def set_target(self, target: float) -> None:
        self.target = target

    def jump(self, value: float) -> None:
        self.value = self.target = value
        self.velocity = 0.0

    @property
    def settled(self) -> bool:
        return abs(self.value - self.target) < 0.0015 and abs(self.velocity) < 0.01

    def step(self, dt: float) -> float:
        # semi-implicit Euler in small sub-steps for stability
        steps = max(1, int(dt / 0.004))
        h = dt / steps
        for _ in range(steps):
            a = -self.k * (self.value - self.target) - self.c * self.velocity
            self.velocity += a * h
            self.value += self.velocity * h
        if self.settled:
            self.jump(self.target)
        return self.value


# ---------------------------------------------------------------------- shapes

def _superellipse(n: float):
    return lambda t: (abs(math.cos(t)) ** n + abs(math.sin(t)) ** n) ** (-1 / n)


def _cookie(k: int, depth: float):
    return lambda t: 1 - depth * (1 - math.cos(k * t)) / 2


SHAPES = {
    "circle": lambda t: 1.0,
    "square": _superellipse(4.2),  # M3E soft square / squircle
    "squircle": _superellipse(3.2),
    "cookie4": _cookie(4, 0.16),
    "cookie6": _cookie(6, 0.13),
    "cookie9": _cookie(9, 0.085),
    "cookie12": _cookie(12, 0.06),
    "sunny": _cookie(8, 0.12),
    "clover": _cookie(4, 0.32),
    "flower": _cookie(8, 0.22),
    "burst": _cookie(12, 0.14),
}


def shape_radii(name: str, n: int) -> list[float]:
    fn = SHAPES.get(name, SHAPES["square"])
    return [fn(2 * math.pi * i / n) for i in range(n)]


def build_shape_path(radii: list[float], w: float, h: float, rotation: float = 0.0) -> Gsk.Path:
    n = len(radii)
    cx, cy = w / 2, h / 2
    rx, ry = w / 2, h / 2
    b = Gsk.PathBuilder.new()
    for i, r in enumerate(radii):
        t = 2 * math.pi * i / n
        # shape is rotated, the sampled radius follows the rotated angle
        x = cx + rx * r * math.cos(t + rotation)
        y = cy + ry * r * math.sin(t + rotation)
        (b.move_to if i == 0 else b.line_to)(x, y)
    b.close()
    return b.to_path()


def _rgba(r, g, b, a=1.0) -> Gdk.RGBA:
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = r, g, b, a
    return c


class ShapeArt(Gtk.Widget):
    """Fixed-size art in an M3E shape. CSS `color` = placeholder background."""

    __gtype_name__ = "MassShapeArt"

    def __init__(self, size: int, shape: str = "square", icon: str = "audio-x-generic-symbolic") -> None:
        super().__init__()
        self.size = size
        self.samples = 240 if size >= 200 else (120 if size >= 80 else 72)
        self._from = shape_radii(shape, self.samples)
        self._to = self._from
        self.shape = shape
        self._morph = Spring(1.0, "expressive")
        self._rotation = 0.0
        self.spin_speed = 0.0  # radians / second
        self.texture: Gdk.Texture | None = None
        self._fade = Spring(1.0, "fast")
        self._icon_name = icon
        self._tick_id = 0
        self._last_frame = 0
        self._path_cache: tuple | None = None
        self.add_css_class("shape-art")
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.set_overflow(Gtk.Overflow.HIDDEN)

    # -------------------------------------------------------------- API

    def set_texture(self, tex: Gdk.Texture | None, animate: bool = True) -> None:
        if tex is self.texture:
            return
        self.texture = tex
        if animate and tex is not None:
            self._fade.jump(0.0)
            self._fade.set_target(1.0)
            self._ensure_tick()
        else:
            self._fade.jump(1.0)
        self.queue_draw()

    def set_icon(self, icon: str) -> None:
        if icon != self._icon_name:
            self._icon_name = icon
            self.queue_draw()

    def set_shape(self, shape: str, animate: bool = True) -> None:
        if shape == self.shape:
            return
        cur = self._current_radii()
        self.shape = shape
        self._from, self._to = cur, shape_radii(shape, self.samples)
        if animate and self.get_mapped():
            self._morph.jump(0.0)
            self._morph.set_target(1.0)
            self._ensure_tick()
        else:
            self._morph.jump(1.0)
        self._path_cache = None
        self.queue_draw()

    def set_spinning(self, speed: float) -> None:
        self.spin_speed = speed
        if speed:
            self._ensure_tick()

    # -------------------------------------------------------------- animation

    def _current_radii(self) -> list[float]:
        t = self._morph.value
        if t >= 1.0 and self._morph.settled:
            return self._to
        return [a + (b - a) * t for a, b in zip(self._from, self._to)]

    def _ensure_tick(self) -> None:
        if not self._tick_id:
            self._last_frame = 0
            self._tick_id = self.add_tick_callback(self._on_tick)

    def _on_tick(self, _w, clock) -> bool:
        now = clock.get_frame_time()
        dt = min(0.05, (now - self._last_frame) / 1e6) if self._last_frame else 1 / 60
        self._last_frame = now
        if not self._morph.settled:
            self._morph.step(dt)
            self._path_cache = None
        if not self._fade.settled:
            self._fade.step(dt)
        if self.spin_speed:
            self._rotation = (self._rotation + self.spin_speed * dt) % (2 * math.pi)
            self._path_cache = None
        self.queue_draw()
        if self._morph.settled and self._fade.settled and not self.spin_speed:
            self._tick_id = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    # -------------------------------------------------------------- GTK

    def do_measure(self, _orientation, _for_size):
        return self.size, self.size, -1, -1

    def do_unmap(self) -> None:
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        Gtk.Widget.do_unmap(self)

    def do_map(self) -> None:
        Gtk.Widget.do_map(self)
        if self.spin_speed or not self._morph.settled:
            self._ensure_tick()

    def _path(self, w: float, h: float) -> Gsk.Path:
        key = (w, h)
        if self._path_cache and self._path_cache[0] == key:
            return self._path_cache[1]
        path = build_shape_path(self._current_radii(), w, h, self._rotation)
        self._path_cache = (key, path)
        return path

    def do_snapshot(self, snapshot) -> None:
        w, h = self.get_width(), self.get_height()
        if w <= 0 or h <= 0:
            return
        path = self._path(w, h)
        bg = self.get_color()
        snapshot.push_fill(path, Gsk.FillRule.WINDING)
        snapshot.append_color(bg, Graphene.Rect().init(0, 0, w, h))
        tex = self.texture
        if tex is None or self._fade.value < 1:
            self._draw_icon(snapshot, w, h, bg)
        if tex is not None:
            tw, th = tex.get_width(), tex.get_height()
            scale = max(w / tw, h / th)
            dw, dh = tw * scale, th * scale
            if self._fade.value < 1:
                snapshot.push_opacity(max(0.0, min(1.0, self._fade.value)))
            snapshot.append_scaled_texture(
                tex, Gsk.ScalingFilter.TRILINEAR, Graphene.Rect().init((w - dw) / 2, (h - dh) / 2, dw, dh))
            if self._fade.value < 1:
                snapshot.pop()
        snapshot.pop()

    def _draw_icon(self, snapshot, w, h, bg: Gdk.RGBA) -> None:
        theme = Gtk.IconTheme.get_for_display(self.get_display())
        px = max(16, int(min(w, h) / 3))
        icon = theme.lookup_icon(self._icon_name, None, px, self.get_scale_factor(),
                                 Gtk.TextDirection.NONE, Gtk.IconLookupFlags.FORCE_SYMBOLIC)
        lum = 0.2126 * bg.red + 0.7152 * bg.green + 0.0722 * bg.blue
        fg = _rgba(0, 0, 0, 0.55) if lum > 0.55 else _rgba(1, 1, 1, 0.6)
        snapshot.save()
        snapshot.translate(Graphene.Point().init((w - px) / 2, (h - px) / 2))
        icon.snapshot_symbolic(snapshot, px, px, [fg])
        snapshot.restore()


class WavySeekbar(Gtk.Widget):
    """M3 Expressive wavy progress / seek bar. CSS `color` = active color."""

    __gtype_name__ = "MassWavySeekbar"
    __gsignals__ = {"seek": (GObject.SignalFlags.RUN_FIRST, None, (float,))}

    def __init__(self, height: int = 40, amplitude: float = 4.0, wavelength: float = 36.0,
                 line_width: float = 4.0, interactive: bool = True, pad: float = 10.0) -> None:
        super().__init__(hexpand=True)
        self.h = height
        self.pad = pad  # inset of the line from the edges (room for the thumb / round caps)
        self.A, self.wl, self.lw = amplitude, wavelength, line_width
        self.fraction = 0.0
        self.playing = False
        self._amp = Spring(0.0, "default")
        self.wavy = True
        self._thumb = Spring(1.0, "fast")
        self._phase = 0.0
        self._drag_frac: float | None = None
        self._tick_id = 0
        self._last = 0
        self.add_css_class("wavy-seekbar")
        self.interactive = interactive
        if interactive:
            self.set_cursor_from_name("pointer")
            drag = Gtk.GestureDrag()
            drag.connect("drag-begin", self._on_begin)
            drag.connect("drag-update", self._on_update)
            drag.connect("drag-end", self._on_end)
            self.add_controller(drag)
            self._drag_x0 = 0.0

    # -------------------------------------------------------------- API

    def set_wavy(self, wavy: bool) -> None:
        if wavy != self.wavy:
            self.wavy = wavy
            self._amp.set_target(1.0 if self.playing and wavy else 0.0)
            self._ensure_tick()

    def set_state(self, fraction: float, playing: bool) -> None:
        fraction = max(0.0, min(1.0, fraction))
        changed = abs(fraction - self.fraction) > 1e-4
        self.fraction = fraction
        if playing != self.playing:
            self.playing = playing
            self._amp.set_target(1.0 if playing and self.wavy else 0.0)
            self._ensure_tick()
        if changed:
            self.queue_draw()

    @property
    def display_fraction(self) -> float:
        return self._drag_frac if self._drag_frac is not None else self.fraction

    # -------------------------------------------------------------- input

    def _pad(self) -> float:
        return self.pad

    def _frac_at(self, x: float) -> float:
        w = self.get_width()
        p = self._pad()
        return max(0.0, min(1.0, (x - p) / max(1.0, w - 2 * p)))

    def _on_begin(self, _g, x, _y) -> None:
        self._drag_x0 = x
        self._drag_frac = self._frac_at(x)
        self._thumb.set_target(1.6)
        self._ensure_tick()

    def _on_update(self, _g, dx, _dy) -> None:
        self._drag_frac = self._frac_at(self._drag_x0 + dx)
        self.queue_draw()

    def _on_end(self, _g, dx, _dy) -> None:
        frac = self._frac_at(self._drag_x0 + dx)
        self.fraction = frac
        self._drag_frac = None
        self._thumb.set_target(1.0)
        self._ensure_tick()
        self.emit("seek", frac)

    # -------------------------------------------------------------- animation

    def _ensure_tick(self) -> None:
        if not self._tick_id and self.get_mapped():
            self._last = 0
            self._tick_id = self.add_tick_callback(self._on_tick)

    def _on_tick(self, _w, clock) -> bool:
        now = clock.get_frame_time()
        dt = min(0.05, (now - self._last) / 1e6) if self._last else 1 / 60
        self._last = now
        self._amp.step(dt)
        self._thumb.step(dt)
        if self._amp.value > 0.001:
            self._phase = (self._phase + dt * 2 * math.pi / 1.4) % (2 * math.pi)
        self.queue_draw()
        if not self.playing and self._amp.settled and self._thumb.settled:
            self._tick_id = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def do_map(self) -> None:
        Gtk.Widget.do_map(self)
        if self.playing or not self._amp.settled:
            self._ensure_tick()

    def do_unmap(self) -> None:
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        Gtk.Widget.do_unmap(self)

    # -------------------------------------------------------------- drawing

    def do_measure(self, orientation, _for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 80, 240, -1, -1
        return self.h, self.h, -1, -1

    def do_snapshot(self, snapshot) -> None:
        w, h = self.get_width(), self.get_height()
        if w <= 0:
            return
        col = self.get_color()
        dim = _rgba(col.red, col.green, col.blue, col.alpha * 0.28)
        p = self._pad()
        cy = h / 2
        span = w - 2 * p
        tx = p + span * self.display_fraction
        thumb_w = 4.0 * self._thumb.value if self.interactive else 0.0
        gap = 6.0 if self.interactive else 3.0
        amp = self.A * self._amp.value

        stroke = Gsk.Stroke.new(self.lw)
        stroke.set_line_cap(Gsk.LineCap.ROUND)
        stroke.set_line_join(Gsk.LineJoin.ROUND)

        # active wave
        end_active = tx - gap - thumb_w / 2
        if end_active > p:
            b = Gsk.PathBuilder.new()
            x = p
            # taper the wave in near the start so it begins on the center line
            first = True
            while True:
                xx = min(x, end_active)
                taper = min(1.0, (xx - p) / (self.wl / 2)) if amp else 0
                y = cy + amp * taper * math.sin(2 * math.pi * (xx - p) / self.wl - self._phase)
                (b.move_to if first else b.line_to)(xx, y)
                first = False
                if xx >= end_active:
                    break
                x += 2.0
            snapshot.append_stroke(b.to_path(), stroke, col)

        # remaining track
        start_rest = tx + gap + thumb_w / 2
        if start_rest < w - p:
            b = Gsk.PathBuilder.new()
            b.move_to(start_rest, cy)
            b.line_to(w - p, cy)
            snapshot.append_stroke(b.to_path(), stroke, dim)
            # stop indicator
            dot = Gsk.PathBuilder.new()
            dot.add_circle(Graphene.Point().init(w - p, cy), self.lw / 2)
            snapshot.append_fill(dot.to_path(), Gsk.FillRule.WINDING, col)

        # thumb: vertical pill
        if self.interactive:
            th = 22.0 + 4 * (self._thumb.value - 1)
            rr = Gsk.RoundedRect()
            rr.init_from_rect(Graphene.Rect().init(tx - thumb_w / 2, cy - th / 2, thumb_w, th), thumb_w / 2)
            snapshot.push_rounded_clip(rr)
            snapshot.append_color(col, rr.bounds)
            snapshot.pop()


# ---------------------------------------------------------------------- Material Symbols

_HAS_SYMBOLS: bool | None = None

# Material Symbols ligature -> fallback freedesktop symbolic icon
SYMBOL_FALLBACKS = {
    "play_arrow": "media-playback-start-symbolic",
    "pause": "media-playback-pause-symbolic",
    "skip_next": "media-skip-forward-symbolic",
    "skip_previous": "media-skip-backward-symbolic",
    "shuffle": "media-playlist-shuffle-symbolic",
    "repeat": "media-playlist-repeat-symbolic",
    "repeat_one": "media-playlist-repeat-song-symbolic",
    "volume_up": "audio-volume-high-symbolic",
    "volume_down": "audio-volume-medium-symbolic",
    "volume_mute": "audio-volume-low-symbolic",
    "volume_off": "audio-volume-muted-symbolic",
    "favorite": "emblem-favorite-symbolic",
    "home": "go-home-symbolic",
    "library_music": "library-music-symbolic",
    "search": "system-search-symbolic",
    "queue_music": "view-list-symbolic",
    "speaker": "audio-speakers-symbolic",
    "speaker_group": "audio-speakers-symbolic",
    "settings": "emblem-system-symbolic",
    "album": "media-optical-symbolic",
    "person": "avatar-default-symbolic",
    "artist": "avatar-default-symbolic",
    "playlist_play": "view-list-symbolic",
    "radio": "network-wireless-symbolic",
    "more_vert": "view-more-symbolic",
    "close": "window-close-symbolic",
    "delete": "user-trash-symbolic",
    "low_priority": "go-top-symbolic",
    "clear_all": "edit-clear-all-symbolic",
    "cast": "send-to-symbolic",
    "power_settings_new": "system-shutdown-symbolic",
    "check": "object-select-symbolic",
    "music_note": "audio-x-generic-symbolic",
    "arrow_back": "go-previous-symbolic",
    "expand_more": "pan-down-symbolic",
    "lyrics": "format-justify-left-symbolic",
    "computer": "computer-symbolic",
    "tv": "video-display-symbolic",
    "smartphone": "phone-symbolic",
    "menu": "open-menu-symbolic",
    "graphic_eq": "audio-x-generic-symbolic",
    "podcasts": "microphone-sensitivity-high-symbolic",
    "mic": "microphone-sensitivity-high-symbolic",
    "auto_awesome": "starred-symbolic",
    "stop": "media-playback-stop-symbolic",
    "visibility": "view-reveal-symbolic",
    "lyrics": "format-justify-left-symbolic",
    "autoplay": "media-playlist-repeat-symbolic",
    "join": "media-playlist-consecutive-symbolic",
    "bedtime": "weather-clear-night-symbolic",
    "info": "dialog-information-symbolic",
}


def has_material_symbols() -> bool:
    global _HAS_SYMBOLS
    if _HAS_SYMBOLS is None:
        from gi.repository import PangoCairo

        fams = PangoCairo.FontMap.get_default().list_families()
        _HAS_SYMBOLS = any(f.get_name().startswith("Material Symbols Rounded") for f in fams)
    return _HAS_SYMBOLS


class Sym(Gtk.Widget):
    """A Material Symbols Rounded glyph (via ligature), or a symbolic icon fallback."""

    __gtype_name__ = "MassSym"

    def __init__(self, name: str, size: int = 24, fill: bool = False) -> None:
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.set_layout_manager(Gtk.BinLayout())
        self.size = size
        self._name = None
        if has_material_symbols():
            self._child = Gtk.Label()
            self._child.add_css_class("msym")
            self._child.add_css_class(f"msym-{size}")
        else:
            self._child = Gtk.Image(pixel_size=size)
        self._child.set_parent(self)
        self.set_fill(fill)
        self.set_symbol(name)

    def set_symbol(self, name: str) -> None:
        if name == self._name:
            return
        self._name = name
        if isinstance(self._child, Gtk.Label):
            self._child.set_label(name)
        else:
            self._child.set_from_icon_name(SYMBOL_FALLBACKS.get(name, "image-missing-symbolic"))

    def set_fill(self, fill: bool) -> None:
        (self._child.add_css_class if fill else self._child.remove_css_class)("fill")

    def do_dispose(self) -> None:
        if self._child is not None:
            self._child.unparent()
            self._child = None


def sym_button(name: str, tooltip: str = "", classes: tuple[str, ...] = ("m3", "icon", "flat"),
               size: int = 24, fill: bool = False, cb=None, action: str | None = None) -> Gtk.Button:
    btn = Gtk.Button(tooltip_text=tooltip or None, valign=Gtk.Align.CENTER)
    for c in classes:
        btn.add_css_class(c)
    btn.sym = Sym(name, size, fill)
    btn.set_child(btn.sym)
    if cb:
        btn.connect("clicked", lambda *_: cb())
    if action:
        btn.set_action_name(action)
    return btn
