"""Queue view for the active player (segmented expressive list)."""

from __future__ import annotations

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk

from .common import ArtImage, ItemObject, artists_str, fmt_time, label
from .dj import AI_ICON, DJ_SHAPE, dj_icon, dj_segment, host_name
from .expressive import ShapeArt, Sym, sym_button
from .motion import after_motion

AUTOSCROLL_EDGE = 48  # px from the scroller's top/bottom where a drag starts scrolling


def move_target(src: int, insert_at: int) -> tuple[int, int] | None:
    """(final row, server pos_shift) for moving row `src` to insertion index `insert_at`; None if it stays put."""
    final = insert_at - 1 if insert_at > src else insert_at
    shift = final - src
    return None if shift == 0 else (final, shift)


def can_move(pos: int, current: int | None, is_dj: bool) -> bool:
    """The server refuses to move the playing (or buffered) item; history and DJ breaks stay where they are."""
    return not is_dj and (current is None or pos > current)


def can_drop(insert_at: int, n_rows: int, current: int | None) -> bool:
    return 0 <= insert_at <= n_rows and (current is None or insert_at > current)


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
        self._rows: set[Gtk.Box] = set()  # the row widgets that exist (only those on screen, recycled)
        self._gen = 0  # bumps per fetch, so a slow older response can't overwrite a newer one
        self._reload_source = 0  # pending debounced reload
        self._drag_id: str | None = None  # queue_item_id being dragged
        self._drop_box: Gtk.Box | None = None  # row showing the drop line
        self._scroll_y: float | None = None  # pointer y over the scroller during a drag
        self._scroll_source = 0

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
        factory.connect("unbind", lambda _f, li: setattr(li.get_child(), "pos", None))
        factory.connect("teardown", lambda _f, li: self._rows.discard(li.get_child()))
        self.list = Gtk.ListView(model=Gtk.NoSelection(model=self.store), factory=factory,
                                 single_click_activate=True)
        self.list.add_css_class("m3-list")
        self.list.connect("activate", lambda _l, pos: self.backend.play_index(
            self.store.get_item(pos).item.queue_item_id))
        # scrollable lists must not have margins (GTK asserts); spacing comes from CSS padding
        self.list.add_css_class("compact" if compact else "full")
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.list.add_controller(keys)
        clamp = Adw.ClampScrollable(maximum_size=900, child=self.list)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp)
        # GTK doesn't scroll a list while something is dragged over its edges
        motion = Gtk.DropControllerMotion()
        motion.connect("motion", lambda _c, _x, y: self._autoscroll(y))
        motion.connect("leave", lambda *_: self._autoscroll(None))
        self.scroller.add_controller(motion)
        self.stack.add_named(self.scroller, "list")
        self.append(self.stack)

        backend.connect("queue-items-updated", lambda _b, qid: self._maybe_reload(qid))
        backend.connect("queue-updated", lambda _b, qid: self._on_queue_updated(qid))
        backend.connect("active-player-changed", lambda *_: self._schedule_reload())
        backend.connect("status-changed", lambda *_: self._schedule_reload())
        self.connect("map", lambda *_: self._pending and self.reload())

    def _schedule_reload(self) -> None:
        """Collapse bursts of server events into one queue fetch."""
        if self._reload_source:
            GLib.source_remove(self._reload_source)
        self._reload_source = GLib.timeout_add(200, self._debounced_reload)

    def _debounced_reload(self) -> bool:
        self._reload_source = 0
        self.reload()
        return False

    def _maybe_reload(self, qid: str) -> None:
        if self.backend.active_queue and qid == self.backend.active_queue.queue_id:
            self._schedule_reload()

    def _on_queue_updated(self, qid: str) -> None:
        q = self.backend.active_queue
        if not q or q.queue_id != qid:
            return
        cur = q.current_item.queue_item_id if q.current_item else None
        idx = q.current_index
        # the current item isn't in the list we show (and should be): it's out of date
        stale = (cur is not None and self._ids is not None and cur not in self._ids
                 and idx is not None and idx < len(self._ids))
        if q.queue_id != self._qid or stale:
            self._schedule_reload()
        else:
            self._highlight(self._current_pos(q))

    def _current_pos(self, q) -> int | None:
        """Row of the playing item, found by id: the server's index can lag or refer to another list."""
        cur = q.current_item.queue_item_id if q.current_item else None
        if cur is not None and self._ids and cur in self._ids:
            return self._ids.index(cur)
        return q.current_index

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
        self._gen += 1
        gen = self._gen

        def populate(items):
            # the client replaces the PlayerQueue object on every update, so read it now, not at fetch time
            q2 = self.backend.active_queue
            if gen == self._gen and q2 and q2.queue_id == qid:
                self._populate(q2, items)

        async def load():
            items = await self.backend.client.player_queues.get_queue_items(qid, limit=500)
            if gen == self._gen:
                after_motion(self, lambda: populate(items))

        self.backend.run(load())

    def _clear(self) -> None:
        self.store.remove_all()

    def _populate(self, q, items) -> None:
        ids = [i.queue_item_id for i in items]
        if q.queue_id == self._qid and ids == self._ids:
            self._highlight(self._current_pos(q))
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
        self._current_index = self._current_pos(q)
        self.store.splice(0, self.store.get_n_items(), [ItemObject(i) for i in items])  # no widgets made here
        if self.compact:
            self._scroll_to_current()
        else:
            GLib.idle_add(lambda: (vadj.set_value(scroll), False)[1])

    # ------------------------------------------------------------------ rows (recycled)

    def _setup(self, _f, li) -> None:
        box = Gtk.Box(spacing=14)
        box.add_css_class("qrow")  # styled here, not on GTK's row: touching that from Python breaks it
        # only the handle starts a drag, so clicks still play and the list still scrolls
        handle = Sym("drag_indicator", 20)
        handle.add_css_class("drag-handle")
        handle.set_cursor_from_name("grab")
        handle.set_tooltip_text("Drag to reorder (Alt+↑/↓)")
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        src.connect("prepare", self._drag_prepare, box)
        src.connect("drag-begin", self._drag_begin, box)
        src.connect("drag-end", lambda *_: self._drag_end())
        handle.add_controller(src)
        box.append(handle)
        box.handle = handle
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("motion", self._drop_motion, box)
        drop.connect("leave", lambda *_: self._mark_drop(None, ""))
        drop.connect("drop", self._on_drop, box)
        box.add_controller(drop)
        art = ArtImage(self.backend, 48)
        dj_art = ShapeArt(48, DJ_SHAPE, "sym:" + AI_ICON)
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
        box.pos = None
        box.is_dj = False
        self._rows.add(box)
        box.parts = (art, dj_art, title, sub, dur)
        li.set_child(box)

    def _bind(self, _f, li) -> None:
        box = li.get_child()
        it = li.get_item().item
        pos = li.get_position()
        art, dj_art, title, sub, dur = box.parts
        box.item = it
        box.pos = pos
        mi = it.media_item
        seg = dj_segment(it)
        art.set_visible(not seg)
        dj_art.set_visible(bool(seg))
        if seg:
            dj_art.set_icon("sym:" + dj_icon(self.backend, seg["host_id"]))
            title.set_label(f"{seg['name']} · {host_name(self.backend, seg['host_id'])}")
            sub.set_label(seg["text"] or "Preparing…")
            sub.set_wrap(True)
        else:
            art.set_url(self.backend.image_url(it, 96))
            title.set_label(getattr(mi, "name", None) or it.name)
            sub.set_label(artists_str(mi) if mi else "")
            sub.set_wrap(False)
        dur.set_label(fmt_time(it.duration) if it.duration else "")
        box.is_dj = bool(seg)
        n = self.store.get_n_items()
        for cls, on in (("current", pos == self._current_index), ("dj-row", bool(seg)),
                        ("first", pos == 0), ("last", pos == n - 1),
                        ("dragging", it.queue_item_id == self._drag_id)):
            (box.add_css_class if on else box.remove_css_class)(cls)
        box.remove_css_class("drop-above")
        box.remove_css_class("drop-below")
        box.handle.set_sensitive(self._movable(box))

    def _highlight(self, index: int | None) -> None:
        if index == self._current_index:
            return
        self._current_index = index
        # restyle the rows directly: items_changed() with the same item doesn't rebind it (GTK 4.24)
        for box in self._rows:
            (box.add_css_class if box.pos is not None and box.pos == index else box.remove_css_class)("current")
            box.handle.set_sensitive(self._movable(box))
        if self.compact:
            self._scroll_to_current()

    # ------------------------------------------------------------------ reordering

    def _movable(self, box: Gtk.Box) -> bool:
        return box.item is not None and box.pos is not None and can_move(box.pos, self._current_index, box.is_dj)

    def _move(self, src: int, insert_at: int) -> int | None:
        """Move row `src` now and tell the server; a refusal reloads the real order. Returns the new row."""
        target = move_target(src, insert_at)
        if target is None or self._ids is None:
            return None
        final, shift = target
        item_id = self._ids[src]
        lo, hi = min(src, final), max(src, final)
        items = [self.store.get_item(i).item for i in range(lo, hi + 1)]
        items.insert(final - lo, items.pop(src - lo))
        # fresh wrappers so every shifted row rebinds: a moved-but-same item keeps its stale position
        self.store.splice(lo, hi - lo + 1, [ItemObject(i) for i in items])
        self._ids.insert(final, self._ids.pop(src))
        self.backend.move_queue_item(item_id, shift, self._move_failed)
        return final

    def _move_failed(self, _err: Exception) -> None:
        if hasattr(root := self.get_root(), "toast"):
            root.toast("Couldn’t move track")
        self._schedule_reload()

    def _insert_index(self, box: Gtk.Box, y: float) -> int | None:
        if box.pos is None:
            return None
        return box.pos + (1 if y > box.get_height() / 2 else 0)

    def _drag_prepare(self, _src, x: float, y: float, box: Gtk.Box) -> Gdk.ContentProvider | None:
        if not self._movable(box):
            return None
        self._drag_id = box.item.queue_item_id
        box.hot_spot = (x, y)
        return Gdk.ContentProvider.new_for_value(self._drag_id)

    def _drag_begin(self, src: Gtk.DragSource, _drag, box: Gtk.Box) -> None:
        # a still image: the row widget itself may be recycled for another item while autoscrolling
        icon = Gtk.WidgetPaintable.new(box).get_current_image()
        ok, pt = box.handle.compute_point(box, Graphene.Point().init(*box.hot_spot))
        src.set_icon(icon, int(pt.x) if ok else 0, int(pt.y) if ok else 0)
        box.add_css_class("dragging")

    def _drag_end(self) -> None:
        self._drag_id = None
        self._mark_drop(None, "")
        self._autoscroll(None)
        for box in self._rows:
            box.remove_css_class("dragging")

    def _drop_allowed(self, box: Gtk.Box, y: float) -> int | None:
        """Insertion index for a drop at y on this row, or None where it can't land."""
        at = self._insert_index(box, y)
        if at is None or not self._ids or self._drag_id not in self._ids:
            return None
        if not can_drop(at, len(self._ids), self._current_index):
            return None
        return at if move_target(self._ids.index(self._drag_id), at) else None

    def _drop_motion(self, _t, _x: float, y: float, box: Gtk.Box) -> Gdk.DragAction:
        at = self._drop_allowed(box, y)
        if at is None:
            self._mark_drop(None, "")
            return 0
        self._mark_drop(box, "drop-below" if at > box.pos else "drop-above")
        return Gdk.DragAction.MOVE

    def _on_drop(self, _t, value: str, _x: float, y: float, box: Gtk.Box) -> bool:
        at = self._drop_allowed(box, y)
        self._mark_drop(None, "")
        if at is None or value != self._drag_id:
            return False
        self._move(self._ids.index(value), at)
        return True

    def _mark_drop(self, box: Gtk.Box | None, cls: str) -> None:
        if self._drop_box is not None:
            self._drop_box.remove_css_class("drop-above")
            self._drop_box.remove_css_class("drop-below")
        self._drop_box = box
        if box is not None:
            box.add_css_class(cls)

    def _autoscroll(self, y: float | None) -> None:
        self._scroll_y = y
        if y is not None and not self._scroll_source:
            self._scroll_source = GLib.timeout_add(16, self._scroll_tick)

    def _scroll_tick(self) -> bool:
        y = self._scroll_y
        if y is None:
            self._scroll_source = 0
            return False
        h = self.scroller.get_height()
        step = 0.0
        if y < AUTOSCROLL_EDGE:
            step = -(AUTOSCROLL_EDGE - y) / 3
        elif y > h - AUTOSCROLL_EDGE:
            step = (y - (h - AUTOSCROLL_EDGE)) / 3
        if step:
            adj = self.scroller.get_vadjustment()
            adj.set_value(adj.get_value() + step)
        return True

    def _focused_box(self) -> Gtk.Box | None:
        row = self.list.get_focus_child()
        child = row.get_first_child() if row else None
        return child if child in self._rows else None

    def _on_key(self, _c, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if not state & Gdk.ModifierType.ALT_MASK or keyval not in (Gdk.KEY_Up, Gdk.KEY_Down):
            return False
        box = self._focused_box()
        if box is None or not self._movable(box):
            return True
        at = box.pos - 1 if keyval == Gdk.KEY_Up else box.pos + 2
        if not can_drop(at, self.store.get_n_items(), self._current_index):
            return True
        final = self._move(box.pos, at)
        if final is not None:
            self.list.scroll_to(final, Gtk.ListScrollFlags.FOCUS, None)
        return True

    def _scroll_to_current(self) -> None:
        idx = self._current_index
        if idx is not None and 0 <= idx < self.store.get_n_items():
            GLib.idle_add(lambda: (self.list.scroll_to(idx, Gtk.ListScrollFlags.NONE, None), False)[1])
