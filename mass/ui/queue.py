"""Queue view for the active player (segmented expressive list)."""

from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from .common import ArtImage, artists_str, fmt_time, label, m3_list
from .dj import DJ_SHAPE, dj_segment, host_name
from .expressive import ShapeArt, sym_button


class QueuePage(Gtk.Box):
    def __init__(self, backend) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.backend = backend
        self._pending = False
        self._qid: str | None = None
        self._ids: list[str] | None = None
        self._current_index: int | None = None

        head = Gtk.Box(spacing=12, margin_start=28, margin_end=28, margin_top=56, margin_bottom=8)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        titles.append(label("Queue", "display-m", ellipsize=False))
        self.summary = label("", "title-m", "on-variant")
        titles.append(self.summary)
        head.append(titles)
        clear = sym_button("clear_all", "Clear queue", ("m3", "icon", "large", "tonal"), cb=backend.clear_queue)
        clear.set_valign(Gtk.Align.END)
        head.append(clear)
        self.append(head)

        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(Adw.StatusPage(icon_name="view-list-symbolic", title="Queue is empty",
                                            description="Play something from your library or search."), "empty")
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=900, margin_top=8, margin_bottom=120, margin_start=20, margin_end=20)
        self.list = m3_list()
        self.list.connect("row-activated", lambda _l, row: self.backend.play_index(row.item.queue_item_id))
        clamp.set_child(self.list)
        self.scroller.set_child(clamp)
        self.stack.add_named(self.scroller, "list")
        self.append(self.stack)

        backend.connect("queue-items-updated", lambda _b, qid: self._maybe_reload(qid))
        backend.connect("queue-updated", lambda _b, qid: self._on_queue_updated(qid))
        backend.connect("active-player-changed", lambda *_: self.reload())
        backend.connect("status-changed", lambda *_: self.reload())
        self.connect("map", lambda *_: self._pending and self.reload())

    def _maybe_reload(self, qid: str) -> None:
        if self.backend.active_queue and qid == self.backend.active_queue.queue_id:
            self.reload()

    def _on_queue_updated(self, qid: str) -> None:
        q = self.backend.active_queue
        if not q or q.queue_id != qid:
            return
        if q.queue_id != self._qid:
            self.reload()
        elif q.current_index != self._current_index:
            self._highlight(q.current_index)

    def reload(self) -> None:
        if not self.get_mapped():
            self._pending = True
            return
        self._pending = False
        q = self.backend.active_queue
        if not q or not self.backend.client:
            self._clear()
            self._qid, self._ids = None, None
            self.summary.set_label("Playing from another source" if self.backend.active_player else "")
            self.stack.set_visible_child_name("empty")
            return
        qid = q.queue_id

        async def load():
            items = await self.backend.client.player_queues.get_queue_items(qid, limit=500)
            q2 = self.backend.active_queue
            if q2 and q2.queue_id == qid:
                self._populate(q2, items)

        self.backend.run(load())

    def _clear(self) -> None:
        while row := self.list.get_row_at_index(0):
            self.list.remove(row)

    def _populate(self, q, items) -> None:
        ids = [i.queue_item_id for i in items]
        if q.queue_id == self._qid and ids == self._ids:
            self._highlight(q.current_index)
            return
        vadj = self.scroller.get_vadjustment()
        scroll = vadj.get_value() if q.queue_id == self._qid else 0
        self._qid, self._ids = q.queue_id, ids
        self._clear()
        if not items:
            self.summary.set_label(q.display_name)
            self.stack.set_visible_child_name("empty")
            return
        self.stack.set_visible_child_name("list")
        total = sum(i.duration or 0 for i in items)
        self.summary.set_label(f"{q.display_name} · {len(items)} tracks · {fmt_time(total)}")
        for it in items:
            self.list.append(self._row(it))
        self._current_index = None
        self._highlight(q.current_index)
        GLib.idle_add(lambda: (vadj.set_value(scroll), False)[1])

    def _row(self, it) -> Gtk.ListBoxRow:
        mi = it.media_item
        row = Gtk.ListBoxRow(activatable=True)
        row.item = it
        box = Gtk.Box(spacing=14, margin_start=12, margin_end=8, margin_top=8, margin_bottom=8)
        seg = dj_segment(it)
        if seg:
            row.add_css_class("dj-row")
            art = ShapeArt(48, DJ_SHAPE, "microphone-sensitivity-high-symbolic")
        else:
            art = ArtImage(self.backend, 48)
            art.set_url(self.backend.image_url(it, 96))
        box.append(art)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True, spacing=2)
        if seg:
            text.append(label(f"{seg['name']} · {host_name(self.backend, seg['host_id'])}", "title-m"))
            sub = label(seg["text"] or "Preparing…", "body-m", "on-variant", ellipsize=False, wrap=True, lines=2)
            sub.set_ellipsize(3)
            text.append(sub)
        else:
            text.append(label(getattr(mi, "name", None) or it.name, "title-m"))
            text.append(label(artists_str(mi) if mi else "", "body-m", "on-variant"))
        box.append(text)
        box.append(label(fmt_time(it.duration) if it.duration else "", "label-l", "on-variant", "numeric",
                         ellipsize=False))
        box.append(sym_button("low_priority", "Play next", cb=lambda i=it.queue_item_id:
                              self.backend.move_queue_item_next(i)))
        box.append(sym_button("delete", "Remove", cb=lambda i=it.queue_item_id: self.backend.delete_queue_item(i)))
        row.set_child(box)
        return row

    def _highlight(self, index: int | None) -> None:
        if self._current_index is not None and (old := self.list.get_row_at_index(self._current_index)):
            old.remove_css_class("current")
        self._current_index = index
        if index is not None and (row := self.list.get_row_at_index(index)):
            row.add_css_class("current")
