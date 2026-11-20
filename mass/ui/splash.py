"""Startup splash: the Mass mark on an M3 Expressive loading-indicator shape.

The backdrop shape slowly turns and springs between expressive shapes (like M3E's loading
indicator), in the theme's colors, with the logo's record + note drawn on top. It's one small
widget, so it stays cheap while the window warms its pages up behind it. While it's up, the
continuous animations of the (hidden) pages behind it are held, see expressive.suspend_animations.
"""

from __future__ import annotations

from gi.repository import Adw, GLib, Graphene, Gsk, Gtk

from .common import label
from .expressive import ShapeArt, suspend_animations
from .motion import SPLASH_FADE_MS, SPLASH_MORPH_MS

SHAPES = ("cookie9", "sunny", "clover", "cookie4", "burst", "squircle")


class SplashMark(ShapeArt):
    """ShapeArt whose placeholder is the Mass logo mark instead of an icon."""

    __gtype_name__ = "MassSplashMark"
    suspendable = False  # the splash's own animation keeps running while the pages' are held

    def __init__(self, size: int, shape: str, color_source: Gtk.Widget | None = None) -> None:
        super().__init__(size, shape, "")
        self.color_source = color_source  # the mark is drawn in this widget's (label) color
        self._mark_cache: tuple | None = None

    def _mark_paths(self, w: float, h: float):
        if self._mark_cache and self._mark_cache[0] == (w, h):
            return self._mark_cache[1]
        s = min(w, h) / 128.0 * 0.84  # a little inset, so the note stays inside every shape
        ox, oy = (w - 128 * s) / 2, (h - 128 * s) / 2
        center = Graphene.Point().init(ox + 64 * s, oy + 64 * s)
        disc = Gsk.PathBuilder.new()
        disc.add_circle(center, 34 * s)
        dot = Gsk.PathBuilder.new()
        dot.add_circle(center, 10 * s)
        note = Gsk.PathBuilder.new()
        note.move_to(ox + 84 * s, oy + 64 * s)
        note.line_to(ox + 84 * s, oy + 30 * s)
        note.quad_to(ox + 98 * s, oy + 34 * s, ox + 98 * s, oy + 46 * s)
        stroke = Gsk.Stroke.new(6 * s)
        stroke.set_line_cap(Gsk.LineCap.ROUND)
        paths = (disc.to_path(), dot.to_path(), note.to_path(), stroke)
        self._mark_cache = ((w, h), paths)
        return paths

    def _draw_icon(self, snapshot, w, h, bg) -> None:
        # read the color at draw time, when the source's style is up to date
        mark = (self.color_source or self).get_color()
        disc, dot, note, stroke = self._mark_paths(w, h)
        snapshot.append_fill(disc, Gsk.FillRule.WINDING, mark)
        snapshot.append_fill(dot, Gsk.FillRule.WINDING, bg)
        snapshot.append_stroke(note, stroke, mark)


class Splash(Gtk.Box):
    """Full-window splash. CSS `color` = its backdrop (drawn here, so it can fade cheaply)."""

    __gtype_name__ = "MassSplash"

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=20, halign=Gtk.Align.FILL,
                         valign=Gtk.Align.FILL, hexpand=True, vexpand=True)
        self.add_css_class("splash")
        self.inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, halign=Gtk.Align.CENTER,
                             valign=Gtk.Align.CENTER, vexpand=True)
        self.status = label("Connecting…", "splash-status", xalign=0.5, ellipsize=False)
        self.mark = SplashMark(168, SHAPES[0], self.status)
        self.mark.add_css_class("splash-shape")
        self.inner.append(self.mark)
        self.inner.append(label("Mass", "splash-title", xalign=0.5, ellipsize=False))
        self.inner.append(self.status)
        self.append(self.inner)
        self._i = 0
        self._timer = 0
        self._alpha = 1.0
        self._fade: Adw.TimedAnimation | None = None
        self._holding = False
        self.connect("map", lambda *_: self._start())
        self.connect("unmap", lambda *_: self._stop())

    def hold_pages(self) -> None:
        """Hold the pages' continuous animations until the splash is gone (nobody can see them)."""
        if not self._holding:
            self._holding = True
            suspend_animations(True)

    def _release_pages(self) -> None:
        if self._holding:
            self._holding = False
            suspend_animations(False)

    def do_css_changed(self, change) -> None:  # noqa: D102 - redraw the mark in the new label color
        Gtk.Box.do_css_changed(self, change)
        self.mark.queue_draw()

    def do_snapshot(self, snapshot) -> None:  # noqa: D102
        # the backdrop is a plain color with the fade applied to its alpha, and only the small inner
        # box gets an opacity layer, instead of rendering the whole window-sized splash offscreen
        c = self.get_color()
        # The window may be translucent, but the splash has to hide the pages behind it; only
        # the fade may change its alpha.
        c.alpha = self._alpha
        snapshot.append_color(c, Graphene.Rect().init(0, 0, self.get_width(), self.get_height()))
        Gtk.Box.do_snapshot(self, snapshot)

    def _start(self) -> None:
        self.mark.set_spinning(0.9)
        if not self._timer and self._fade is None:
            self._timer = GLib.timeout_add(SPLASH_MORPH_MS, self._next_shape)

    def _stop(self) -> None:
        self.mark.set_spinning(0.0)
        self._stop_morphing()

    def _stop_morphing(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _next_shape(self) -> bool:
        self._i = (self._i + 1) % len(SHAPES)
        self.mark.set_shape(SHAPES[self._i])
        return True

    def set_status(self, text: str) -> None:
        self.status.set_label(text)

    def _set_alpha(self, v: float) -> None:
        self._alpha = v
        self.inner.set_opacity(v)
        self.queue_draw()

    def _on_faded(self) -> None:
        self.set_visible(False)
        self._release_pages()

    def dismiss(self) -> None:
        """Fade out, then hide (and stop animating)."""
        if self._fade is not None:
            return
        if not self.get_visible():
            self._release_pages()
            return
        self.set_can_target(False)  # clicks go through while it fades
        self._stop_morphing()  # no new shape mid-fade; the mark just keeps turning
        target = Adw.CallbackAnimationTarget.new(self._set_alpha)
        self._fade = Adw.TimedAnimation.new(self, 1.0, 0.0, SPLASH_FADE_MS, target)
        self._fade.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._fade.connect("done", lambda *_: self._on_faded())
        self._fade.play()
