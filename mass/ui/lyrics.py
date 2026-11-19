"""Time-synced lyrics (LRC) view: current line highlighted, smooth auto-scroll, click to seek."""

from __future__ import annotations

import bisect
import re

from gi.repository import Adw, Gtk

from .common import label
from .motion import LYRIC_SCROLL_MS

_TAG = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")
LEAD = 0.25  # show a line slightly before it is sung


def parse_lrc(text: str) -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    for raw in text.splitlines():
        tags = list(_TAG.finditer(raw))
        if not tags:
            continue
        line = raw[tags[-1].end():].strip()
        for m in tags:
            out.append((int(m.group(1)) * 60 + float(m.group(2).replace(":", ".")), line))
    out.sort(key=lambda x: x[0])
    return out


class LyricsView(Gtk.ScrolledWindow):
    def __init__(self, on_seek) -> None:
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        self.add_css_class("lyrics")
        self.on_seek = on_seek
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.set_child(self.box)
        self._times: list[float] = []
        self._rows: list[Gtk.Widget] = []
        self._cur = -2
        self._anim: Adw.TimedAnimation | None = None
        self.set_message("Lyrics")

    def _clear(self) -> None:
        while c := self.box.get_first_child():
            self.box.remove(c)
        self._times, self._rows, self._cur = [], [], -2

    def set_message(self, text: str) -> None:
        self._clear()
        msg = label(text, "lyric", "dim", ellipsize=False, wrap=True, vexpand=True)
        msg.set_valign(Gtk.Align.CENTER)
        self.box.append(msg)

    def set_lyrics(self, plain: str | None, lrc: str | None) -> None:
        synced = parse_lrc(lrc) if lrc else []
        if not synced and not plain:
            self.set_message("No lyrics for this track")
            return
        self._clear()
        self.box.append(Gtk.Box(height_request=self.get_height() // 3 or 120))  # lead-in space
        if synced:
            for t, text in synced:
                btn = Gtk.Button(halign=Gtk.Align.START)
                btn.add_css_class("lyric-line")
                lbl = label(text or "♪", "lyric", ellipsize=False, wrap=True)
                lbl.set_wrap_mode(2)
                btn.set_child(lbl)
                btn.connect("clicked", lambda _b, t=t: self.on_seek(t))
                self.box.append(btn)
                self._rows.append(btn)
                self._times.append(t)
        else:
            for text in plain.splitlines():
                lbl = label(text, "lyric", "plain", ellipsize=False, wrap=True)
                lbl.set_wrap_mode(2)
                self.box.append(lbl)
        self.box.append(Gtk.Box(height_request=240))
        self.get_vadjustment().set_value(0)

    def update(self, elapsed: float) -> None:
        if not self._times:
            return
        i = bisect.bisect_right(self._times, elapsed + LEAD) - 1
        if i == self._cur:
            return
        for j, row in enumerate(self._rows):
            for cls, on in (("current", j == i), ("past", j < i)):
                (row.add_css_class if on else row.remove_css_class)(cls)
        jump = abs(i - self._cur) > 3
        self._cur = i
        if i >= 0:
            self._scroll_to(self._rows[i], animate=not jump)

    def _scroll_to(self, row: Gtk.Widget, animate: bool) -> None:
        ok, pt = row.compute_point(self.box, _point(0, 0))
        if not ok:
            return
        adj = self.get_vadjustment()
        target = max(0.0, min(pt.y - self.get_height() * 0.35, adj.get_upper() - adj.get_page_size()))
        if self._anim:
            self._anim.pause()
        if not animate:
            adj.set_value(target)
            return
        self._anim = Adw.TimedAnimation.new(self, adj.get_value(), target, LYRIC_SCROLL_MS,
                                            Adw.CallbackAnimationTarget.new(adj.set_value))
        self._anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._anim.play()


def _point(x: float, y: float):
    from gi.repository import Graphene

    p = Graphene.Point()
    p.x, p.y = x, y
    return p
