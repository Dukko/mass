"""Startup splash: the Mass mark on an M3 Expressive loading-indicator shape.

The backdrop shape slowly turns and springs between expressive shapes (like M3E's loading
indicator), in the theme's colors, with the logo's record + note drawn on top. It's one small
widget, so it stays cheap while the window warms its pages up behind it.
"""

from __future__ import annotations

from gi.repository import Adw, GLib, Graphene, Gsk, Gtk

from .common import label
from .expressive import ShapeArt

SHAPES = ("cookie9", "sunny", "clover", "cookie4", "burst", "squircle")
MORPH_EVERY_MS = 750


class SplashMark(ShapeArt):
    """ShapeArt whose placeholder is the Mass logo mark instead of an icon."""

    __gtype_name__ = "MassSplashMark"

    def _draw_icon(self, snapshot, w, h, bg) -> None:
        fg = self.get_color()  # the shape's own color is the backdrop; the mark uses the label color
        mark = getattr(self, "mark_color", None) or fg
        s = min(w, h) / 128.0 * 0.84  # a little inset, so the note stays inside every shape
        ox, oy = (w - 128 * s) / 2, (h - 128 * s) / 2

        def pt(x, y):
            return Graphene.Point().init(ox + x * s, oy + y * s)

        disc = Gsk.PathBuilder.new()
        disc.add_circle(pt(64, 64), 34 * s)
        snapshot.append_fill(disc.to_path(), Gsk.FillRule.WINDING, mark)
        dot = Gsk.PathBuilder.new()
        dot.add_circle(pt(64, 64), 10 * s)
        snapshot.append_fill(dot.to_path(), Gsk.FillRule.WINDING, bg)
        note = Gsk.PathBuilder.new()
        note.move_to(ox + 84 * s, oy + 64 * s)
        note.line_to(ox + 84 * s, oy + 30 * s)
        note.quad_to(ox + 98 * s, oy + 34 * s, ox + 98 * s, oy + 46 * s)
        stroke = Gsk.Stroke.new(6 * s)
        stroke.set_line_cap(Gsk.LineCap.ROUND)
        snapshot.append_stroke(note.to_path(), stroke, mark)


class Splash(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=20, halign=Gtk.Align.FILL,
                         valign=Gtk.Align.FILL, hexpand=True, vexpand=True)
        self.add_css_class("splash")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, halign=Gtk.Align.CENTER,
                        valign=Gtk.Align.CENTER, vexpand=True)
        self.mark = SplashMark(168, SHAPES[0], "")
        self.mark.add_css_class("splash-shape")
        inner.append(self.mark)
        inner.append(label("Mass", "splash-title", xalign=0.5, ellipsize=False))
        self.status = label("Connecting…", "splash-status", xalign=0.5, ellipsize=False)
        inner.append(self.status)
        self.append(inner)
        self._i = 0
        self._timer = 0
        self._fade: Adw.TimedAnimation | None = None
        self.connect("map", lambda *_: self._start())
        self.connect("unmap", lambda *_: self._stop())

    def do_css_changed(self, change) -> None:  # noqa: D102 - keep the mark in the label color
        Gtk.Box.do_css_changed(self, change)
        self.mark.mark_color = self.status.get_color()

    def _start(self) -> None:
        self.mark.mark_color = self.status.get_color()
        self.mark.set_spinning(0.9)
        if not self._timer:
            self._timer = GLib.timeout_add(MORPH_EVERY_MS, self._next_shape)

    def _stop(self) -> None:
        self.mark.set_spinning(0.0)
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _next_shape(self) -> bool:
        self._i = (self._i + 1) % len(SHAPES)
        self.mark.set_shape(SHAPES[self._i])
        return True

    def set_status(self, text: str) -> None:
        self.status.set_label(text)

    def dismiss(self) -> None:
        """Fade out, then hide (and stop animating)."""
        if not self.get_visible() or self._fade is not None:
            return
        self.set_can_target(False)  # clicks go through while it fades
        target = Adw.CallbackAnimationTarget.new(lambda v: self.set_opacity(v))
        self._fade = Adw.TimedAnimation.new(self, 1.0, 0.0, 320, target)
        self._fade.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._fade.connect("done", lambda *_: self.set_visible(False))
        self._fade.play()
