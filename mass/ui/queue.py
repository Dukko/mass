"""Queue view for the active player (segmented expressive list)."""

from __future__ import annotations

from gi.repository import Adw, Gio, GLib, Gtk

from .common import ArtImage, ItemObject, artists_str, fmt_time, label
from .dj import DJ_SHAPE, dj_segment, host_name
from .expressive import ShapeArt, sym_button
from .motion import after_motion



class QueuePage(Gtk.Box):
    """compact=True: just the list (Now Playing's Up next tab), kept scrolled to the current track."""

    def __init__(self, backend, compact: bool = False) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.backend = backend
        self.compact = compact
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
        if not compact:
            self.append(head)

        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(Adw.StatusPage(icon_name="view-list-symbolic", title="Queue is empty",
                                            description="Play something from your library or search."), "empty")
        # a recycling ListView: only the rows on screen exist, however long the queue is
        self.store = Gio.ListStore(item_type=ItemObject)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup)
        factory.connect("bind", self._bind)
        self.list = Gtk.ListView(model=Gtk.NoSelection(model=self.store), factory=factory,
                                 single_click_activate=True)
        self.list.add_css_class("m3-list")
        self.list.connect("activate", lambda _l, pos: self.backend.play_index(
            self.store.get_item(pos).item.queue_item_id))
        # scrollable lists must not have margins (GTK asserts); spacing comes from CSS padding
        self.list.add_css_class("compact" if compact else "full")
        clamp = Adw.ClampScrollable(maximum_size=900, child=self.list)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp)
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
        if not self.get_mapped() and not self.compact:
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
                after_motion(self, lambda: self._populate(q2, items))

        self.backend.run(load())

    def _clear(self) -> None:
        self.store.remove_all()

    def _populate(self, q, items) -> None:
        ids = [i.queue_item_id for i in items]
        if q.queue_id == self._qid and ids == self._ids:
            self._highlight(q.current_index)
            return
        vadj = self.scroller.get_vadjustment()
        scroll = vadj.get_value() if q.queue_id == self._qid else 0
        self._qid, self._ids = q.queue_id, ids
        if not items:
            self._clear()
            self.summary.set_label(q.display_name)
            self.stack.set_visible_child_name("empty")
            return
        self.stack.set_visible_child_name("list")
        total = sum(i.duration or 0 for i in items)
        self.summary.set_label(f"{q.display_name} · {len(items)} tracks · {fmt_time(total)}")
        self._current_index = q.current_index
        self.store.splice(0, self.store.get_n_items(), [ItemObject(i) for i in items])  # no widgets made here
        if self.compact:
            self._scroll_to_current()
        else:
            GLib.idle_add(lambda: (vadj.set_value(scroll), False)[1])

    # ------------------------------------------------------------------ rows (recycled)

    def _setup(self, _f, li) -> None:
        box = Gtk.Box(spacing=14)
        box.add_css_class("qrow")  # styled here, not on GTK's row: touching that from Python breaks it
        art = ArtImage(self.backend, 48)
        dj_art = ShapeArt(48, DJ_SHAPE, "microphone-sensitivity-high-symbolic")
        box.append(art)
        box.append(dj_art)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True, spacing=2)
        title = label("", "title-m")
        sub = label("", "body-m", "on-variant")
        sub.set_lines(2)
        text.append(title)
        text.append(sub)
        box.append(text)
        dur = label("", "label-l", "on-variant", "numeric", ellipsize=False)
        box.append(dur)
        nxt = sym_button("low_priority", "Play next",
                         cb=lambda: box.item and self.backend.move_queue_item_next(box.item.queue_item_id))
        rm = sym_button("delete", "Remove",
                        cb=lambda: box.item and self.backend.delete_queue_item(box.item.queue_item_id))
        box.append(nxt)
        box.append(rm)
        box.item = None
        box.parts = (art, dj_art, title, sub, dur)
        li.set_child(box)

    def _bind(self, _f, li) -> None:
        box = li.get_child()
        it = li.get_item().item
        pos = li.get_position()
        art, dj_art, title, sub, dur = box.parts
        box.item = it
        mi = it.media_item
        seg = dj_segment(it)
        art.set_visible(not seg)
        dj_art.set_visible(bool(seg))
        if seg:
            title.set_label(f"{seg['name']} · {host_name(self.backend, seg['host_id'])}")
            sub.set_label(seg["text"] or "Preparing…")
            sub.set_wrap(True)
        else:
            art.set_url(self.backend.image_url(it, 96))
            title.set_label(getattr(mi, "name", None) or it.name)
            sub.set_label(artists_str(mi) if mi else "")
            sub.set_wrap(False)
        dur.set_label(fmt_time(it.duration) if it.duration else "")
        n = self.store.get_n_items()
        for cls, on in (("current", pos == self._current_index), ("dj-row", bool(seg)),
                        ("first", pos == 0), ("last", pos == n - 1)):
            (box.add_css_class if on else box.remove_css_class)(cls)

    def _highlight(self, index: int | None) -> None:
        old, self._current_index = self._current_index, index
        n = self.store.get_n_items()
        for pos in {old, index}:
            if pos is not None and 0 <= pos < n:
                self.store.items_changed(pos, 1, 1)  # rebind just those rows
        if self.compact:
            self._scroll_to_current()

    def _scroll_to_current(self) -> None:
        idx = self._current_index
        if idx is not None and 0 <= idx < self.store.get_n_items():
            GLib.idle_add(lambda: (self.list.scroll_to(idx, Gtk.ListScrollFlags.NONE, None), False)[1])
