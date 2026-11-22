"""Album / playlist / artist detail page (M3 Expressive)."""

from __future__ import annotations

import asyncio
import logging
import time

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk
from music_assistant_models.enums import MediaType, QueueOption
from music_assistant_models.errors import MediaNotFoundError

from .common import (
    ArtImage,
    artists_str,
    attach_media_actions,
    fmt_time,
    label,
    m3_list,
    media_menu_model,
    media_row,
)
from ..backend import PLAYLIST_MOVE_SCHEMA, PLAYLIST_PAGING_SCHEMA
from ..listing import matches_query
from .expressive import Sym, sym_button
from .library import CHANGE_DEBOUNCE_MS, subtitle_for
from .media_list import MediaList
from .motion import pop_in
from .paging import PAGE
from .playlist_tracks import EDITOR_MAX, WholeList
from .reorder import AutoScroll, move_target, playlist_move, swallow_clicks

FILTER_DEBOUNCE_MS = 150
TRACK_PAGE = 200  # tracks per request when the server pages a playlist
REMOVAL_FALLBACK_MS = 5000
RELOAD_RETRY_MS = 3000
RELOAD_TRIES = 3  # failed reloads tolerated while the page waits for a fresh track list
log = logging.getLogger(__name__)

# artist page album groups, in display order
ALBUM_GROUPS = (("Albums", {"album", "unknown", "soundtrack", "live"}), ("Singles & EPs", {"single", "ep"}),
                ("Compilations", {"compilation"}))


class DetailPage(Adw.NavigationPage):
    """Album / playlist / artist page: expressive hero, then tracks."""

    def __init__(self, backend, item, open_item) -> None:
        super().__init__(title=item.name)
        self.backend, self.item, self.open_item = backend, item, open_item
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar(show_title=False)
        hb.add_css_class("flat-header")
        tv.add_top_bar(hb)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self._autoscroll = AutoScroll(scroller).update
        clamp = Adw.Clamp(maximum_size=980, margin_bottom=120, margin_start=20, margin_end=20)
        self.col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        clamp.set_child(self.col)
        scroller.set_child(clamp)
        tv.set_content(scroller)
        self._tv = tv
        self.set_child(tv)

        head = Gtk.Box(spacing=32)
        art = self.art = ArtImage(backend, 240, fetch_size=480)
        art.set_item(item)
        head.append(art)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.END, hexpand=True)
        info.append(label(str(item.media_type.value).upper(), "overline", "primary-text"))
        title = label(item.name, "display-s", ellipsize=False, wrap=True, lines=3)
        title.set_wrap_mode(2)
        info.append(title)
        self.subtitle = label(subtitle_for(item), "title-m", "on-variant")
        info.append(self.subtitle)
        btns = Gtk.Box(spacing=8, margin_top=16)
        play = Gtk.Button(action_name="media.play")
        for c in ("m3", "filled", "large"):
            play.add_css_class(c)
        pb = Gtk.Box(spacing=8)
        pb.append(Sym("play_arrow", 24, fill=True))
        pb.append(Gtk.Label(label="Play"))
        play.set_child(pb)
        btns.append(play)
        btns.append(sym_button("shuffle", "Shuffle", ("m3", "icon", "large", "tonal"), cb=self._shuffle))
        btns.append(sym_button("radio", "Start radio", ("m3", "icon", "large", "tonal"), action="media.radio"))
        self._menu = media_menu_model()
        more = Gtk.MenuButton(menu_model=self._menu, valign=Gtk.Align.CENTER)
        more.set_child(Sym("more_vert", 24))
        more.add_css_class("flat")
        more.add_css_class("circular")
        btns.append(more)
        info.append(btns)
        head.append(info)
        self._refresh_actions = attach_media_actions(head, backend, lambda: self.item)
        self._head = head
        self.col.append(head)

        self.spinner = Adw.Spinner(height_request=40, margin_top=24)
        self.col.append(self.spinner)

        self._tracks_slot: Gtk.Box | None = None
        self._remove_buttons: list[Gtk.Button] = []
        self._handles: list[Gtk.Widget] = []
        self._lb: Gtk.ListBox | None = None
        self._order: list[Gtk.ListBoxRow] = []  # editable playlist rows, top to bottom
        self._drag_row: Gtk.ListBoxRow | None = None
        self._drop_row: Gtk.ListBoxRow | None = None
        self._busy = False  # a removal or move is in flight: positions can't be trusted until a fresh list is shown
        self._retry_source = 0
        self._reload_tries = 0
        self._edit_accepted = False  # the server has answered the in-flight edit
        self._changed_early = False  # the playlist's change event came before that answer
        self._removal_fallback = 0
        self._tracks_gen = 0
        self._media_list: MediaList | None = None  # virtualized track list, for playlists too long to edit
        self._filter_box: Gtk.Box | None = None
        self._filter_bar: Gtk.Box | None = None
        self._no_match: Gtk.Label | None = None
        self._filter_source = 0
        self._server_filter = False  # the virtualized list filters on the server rather than on loaded rows
        self._query = ""
        self._force_refresh = False  # the next virtualized fetches must bypass the server's cache
        self._opened_at = 0.0
        self._change_source = 0
        self._changed_id = 0
        if item.media_type == MediaType.PLAYLIST:
            self._changed_id = backend.connect("media-item-changed", self._on_media_changed)
            self.connect("notify::root", self._on_root_changed)
        backend.run(self._load())

    def _on_root_changed(self, *_) -> None:
        """A popped page is unparented: stop listening for changes."""
        if self.get_root() is not None:
            return
        if self._changed_id:
            self.backend.disconnect(self._changed_id)
            self._changed_id = 0
        if self._change_source:
            GLib.source_remove(self._change_source)
            self._change_source = 0
        self._cancel_removal_fallback()
        self._cancel_retry()
        if self._filter_source:
            GLib.source_remove(self._filter_source)
            self._filter_source = 0

    def _on_media_changed(self, _b, _event: str, uri: str) -> None:
        if uri != self.item.uri:
            return
        if self._busy and not self._edit_accepted:
            self._changed_early = True  # the server announces some edits before it answers the command
        if self._change_source:
            GLib.source_remove(self._change_source)
        self._change_source = GLib.timeout_add(CHANGE_DEBOUNCE_MS, self._debounced_refresh)

    def _debounced_refresh(self) -> bool:
        self._change_source = 0
        self.backend.run(self._refresh())
        return False

    async def _refresh(self) -> None:
        """Re-read the playlist after a server-side change; leave the page if it no longer exists."""
        it = self.item
        try:
            full = await self.backend.client.music.get_item(it.media_type, it.item_id, it.provider)
        except MediaNotFoundError:
            self._pop_if_visible()
            return
        except Exception as err:  # noqa: BLE001
            log.debug("could not refresh %s: %s", it.uri, err)
            return
        self.item = full
        self._refresh_actions()
        self.subtitle.set_label(subtitle_for(full))
        await self._reload_tracks()

    def _pop_if_visible(self) -> None:
        nav = self.get_ancestor(Adw.NavigationView)
        if nav is not None and nav.get_visible_page() is self:
            nav.pop()

    def _toast(self, text: str) -> None:
        root = self.get_root()
        if hasattr(root, "toast"):
            root.toast(text)

    def _enable_playlist_editing(self) -> None:
        group = Gio.SimpleActionGroup()
        delete = Gio.SimpleAction.new("delete", None)
        delete.connect("activate", lambda *_: self._confirm_delete())
        group.add_action(delete)
        self.insert_action_group("page", group)
        section = Gio.Menu()
        section.append("Delete playlist", "page.delete")
        self._menu.append_section(None, section)

    def _confirm_delete(self) -> None:
        dlg = Adw.AlertDialog(heading="Delete playlist?",
                              body=f"“{self.item.name}” will be removed from your library. This can't be undone.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")
        dlg.connect("response", lambda _d, r: r == "delete" and self._delete())
        dlg.present(self.get_root())

    def _delete(self) -> None:
        name = self.item.name

        def done() -> None:
            self._toast(f"Deleted {name}")
            self._pop_if_visible()

        self.backend.delete_playlist(self.item, done, lambda err: self._toast(f"Could not delete {name}: {err}"))

    def _remove_track(self, position: int) -> None:
        """Queue removal of the track at a server playlist position.

        The server only queues the removal, so the buttons stay off until the playlist's change event
        rebuilds the list; the fallback timer covers a missed event.
        """
        if self._busy:
            return
        self._set_busy(True)

        def fail(err: Exception) -> None:
            self._set_busy(False)
            self._toast(f"Could not remove track: {err}")

        self.backend.remove_from_playlist(self.item, [position], self._edit_done, fail)

    def _edit_done(self) -> None:
        """The server accepted an edit. Lists fetched before this point carry the old order, so discard them."""
        self._tracks_gen += 1
        self._edit_accepted = True
        self._arm_fallback()
        if self._changed_early:
            self._changed_early = False
            self.backend.run(self._reload_tracks())

    def _arm_fallback(self) -> None:
        self._cancel_removal_fallback()
        self._removal_fallback = GLib.timeout_add(REMOVAL_FALLBACK_MS, self._removal_fallback_fired)

    def _removal_fallback_fired(self) -> bool:
        self._removal_fallback = 0
        self.backend.run(self._reload_tracks())
        return False

    def _cancel_removal_fallback(self) -> None:
        if self._removal_fallback:
            GLib.source_remove(self._removal_fallback)
            self._removal_fallback = 0

    def _cancel_retry(self) -> None:
        if self._retry_source:
            GLib.source_remove(self._retry_source)
            self._retry_source = 0

    def _set_busy(self, on: bool) -> None:
        self._busy = on
        if on:
            self._tracks_gen += 1  # a list already being fetched predates this edit
            self._edit_accepted = self._changed_early = False
        else:
            self._cancel_removal_fallback()
            self._cancel_retry()
        for b in self._remove_buttons:
            b.set_sensitive(not on)
        # handles stay sensitive: an insensitive widget isn't picked, so a click would fall through to the row
        for h in self._handles:
            (h.add_css_class if on else h.remove_css_class)("locked")

    def _retry_reload(self) -> bool:
        self._retry_source = 0
        self.backend.run(self._reload_tracks())
        return False

    def _give_up_reloading(self) -> None:
        """No fresh list after several tries: stop editing from positions that may be wrong."""
        for w in (*self._remove_buttons, *self._handles):
            w.set_visible(False)
        self._order = []
        self._set_busy(False)
        self._toast("Couldn’t refresh the playlist")

    def _can_edit(self) -> bool:
        """Remove and delete take a library id, so provider copies of an editable playlist stay read-only."""
        return bool(getattr(self.item, "is_editable", False)) and self.item.provider == "library"

    def _can_reorder(self) -> bool:
        return self._can_edit() and self.backend.supports(PLAYLIST_MOVE_SCHEMA)

    # ------------------------------------------------------------------ reordering

    def _move(self, src: int, insert_at: int) -> int | None:
        """Move row `src` now and tell the server; a refusal reloads the real order. Returns the new row."""
        if self._busy or self._lb is None:
            return None
        move = playlist_move([r.position for r in self._order], src, insert_at)
        if move is None:
            return None
        final, from_pos, to_pos = move
        row = self._order.pop(src)
        self._order.insert(final, row)
        self._lb.remove(row)
        self._lb.insert(row, final)
        self._set_busy(True)

        def fail(_err: Exception) -> None:
            self._toast("Couldn’t move track")
            self.backend.run(self._reload_tracks())

        self.backend.move_playlist_track(self.item, from_pos, to_pos, self._edit_done, fail)
        return final

    def _add_handle(self, row: Gtk.ListBoxRow) -> None:
        # only the handle starts a drag, so clicks still play and the page still scrolls
        handle = Sym("drag_indicator", 20)
        handle.add_css_class("drag-handle")
        handle.set_cursor_from_name("grab")
        handle.set_tooltip_text("Drag to reorder (Alt+↑/↓)")
        if self._busy:
            handle.add_css_class("locked")
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        src.connect("prepare", self._drag_prepare, row)
        src.connect("drag-begin", self._drag_begin, row, handle)
        src.connect("drag-end", lambda *_: self._drag_end())
        handle.add_controller(src)
        swallow_clicks(handle, src)
        row.get_child().prepend(handle)
        self._handles.append(handle)
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("motion", self._drop_motion, row)
        drop.connect("leave", lambda *_: self._mark_drop(None, ""))
        drop.connect("drop", self._on_drop, row)
        row.add_controller(drop)

    def _drag_prepare(self, _src, x: float, y: float, row: Gtk.ListBoxRow) -> Gdk.ContentProvider | None:
        if self._busy:
            return None
        self._drag_row = row
        row.hot_spot = (x, y)
        return Gdk.ContentProvider.new_for_value(str(row.position))

    def _drag_begin(self, src: Gtk.DragSource, _drag, row: Gtk.ListBoxRow, handle: Gtk.Widget) -> None:
        src.set_icon(Gtk.WidgetPaintable.new(row).get_current_image(), *self._hot_spot(row, handle))
        row.add_css_class("dragging")

    @staticmethod
    def _hot_spot(row: Gtk.ListBoxRow, handle: Gtk.Widget) -> tuple[int, int]:
        ok, pt = handle.compute_point(row, Graphene.Point().init(*row.hot_spot))
        return (int(pt.x), int(pt.y)) if ok else (0, 0)

    def _drag_end(self) -> None:
        if self._drag_row is not None:
            self._drag_row.remove_css_class("dragging")
        self._drag_row = None
        self._mark_drop(None, "")
        self._autoscroll(None)

    def _drop_allowed(self, row: Gtk.ListBoxRow, y: float) -> int | None:
        """Insertion index for a drop at y on this row, or None where it can't land."""
        if self._busy or self._query or self._drag_row not in self._order:
            return None
        at = row.get_index() + (1 if y > row.get_height() / 2 else 0)
        return at if move_target(self._order.index(self._drag_row), at) else None

    def _drop_motion(self, _t, _x: float, y: float, row: Gtk.ListBoxRow) -> Gdk.DragAction:
        at = self._drop_allowed(row, y)
        if at is None:
            self._mark_drop(None, "")
            return 0
        self._mark_drop(row, "drop-below" if at > row.get_index() else "drop-above")
        return Gdk.DragAction.MOVE

    def _on_drop(self, _t, value: str, _x: float, y: float, row: Gtk.ListBoxRow) -> bool:
        at = self._drop_allowed(row, y)
        self._mark_drop(None, "")
        if at is None or value != str(self._drag_row.position):
            return False
        self._move(self._order.index(self._drag_row), at)
        return True

    def _mark_drop(self, row: Gtk.ListBoxRow | None, cls: str) -> None:
        if self._drop_row is not None:
            self._drop_row.remove_css_class("drop-above")
            self._drop_row.remove_css_class("drop-below")
        self._drop_row = row
        if row is not None:
            row.add_css_class(cls)

    def _on_key(self, _c, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if self._query or not state & Gdk.ModifierType.ALT_MASK or keyval not in (Gdk.KEY_Up, Gdk.KEY_Down):
            return False
        row = self._lb.get_focus_child() if self._lb else None
        if row not in self._order:
            return False
        i = self._order.index(row)
        at = i - 1 if keyval == Gdk.KEY_Up else i + 2
        if 0 <= at <= len(self._order) and self._move(i, at) is not None:
            row.grab_focus()
        return True

    async def _fetch_tracks(self) -> list[tuple]:
        """The whole playlist straight from its provider: the editor needs the real positions."""
        return await self.backend.playlist_tracks(self.item, force_refresh=True)

    async def _open_tracks(self) -> None:
        """Pick the track view: the editor for a small editable playlist, a virtualized list otherwise.

        Reading the cached copy is enough to size an editable playlist; only the editor re-reads it
        from the provider, because edits go by position.
        """
        self._opened_at = time.monotonic()
        if not self._can_edit():
            self._show_virtual()
            return
        self._tracks_gen += 1
        gen = self._tracks_gen  # a change event or edit during the open supersedes this one
        paged = self.backend.supports(PLAYLIST_PAGING_SCHEMA)
        probe = await self.backend.playlist_tracks(self.item, force_refresh=False,
                                                   limit=EDITOR_MAX + 1 if paged else None, offset=0 if paged else None)
        if gen != self._tracks_gen:
            return
        if len(probe) > EDITOR_MAX:
            self._show_virtual(None if paged else [t for t, _p in probe])
            return
        pairs = await self._fetch_tracks()
        if gen == self._tracks_gen:
            self._show_playlist_tracks(pairs)

    def _take_force_refresh(self) -> bool:
        """Whether this fetch must bypass the server's cache; only the first one after a reload does."""
        force, self._force_refresh = self._force_refresh, False
        return force

    async def _fetch_page(self, offset: int, limit: int) -> list:
        pairs = await self.backend.playlist_tracks(self.item, force_refresh=self._take_force_refresh(), limit=limit,
                                                   offset=offset, search=self._query)
        return [t for t, _p in pairs]

    async def _fetch_all(self) -> list:
        pairs = await self.backend.playlist_tracks(self.item, force_refresh=self._take_force_refresh())
        return [t for t, _p in pairs]

    def _show_virtual(self, primed: list | None = None) -> None:
        self._server_filter = self.backend.supports(PLAYLIST_PAGING_SCHEMA)
        if self._server_filter:
            fetch, page = self._fetch_page, TRACK_PAGE
        else:
            fetch, page = WholeList(self._fetch_all, primed), PAGE
        view = self._media_list = MediaList(self.backend, fetch, self._play_from, on_empty=self._on_list_empty,
                                            page=page)
        # a list inside another scroller would be laid out in full, so the page scrolls only this list
        self.col.remove(self._head)
        self.col.remove(self._filter_box)
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        top.append(self._head)
        top.append(self._filter_box)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(Adw.Clamp(maximum_size=980, margin_start=20, margin_end=20, margin_top=8, child=top))
        outer.append(view)
        self._tv.set_content(outer)

    def _on_list_empty(self, empty: bool) -> None:
        if self._opened_at:
            log.debug("playlist %s: first rows %.0f ms after opening", self.item.item_id,
                      (time.monotonic() - self._opened_at) * 1000)
            self._opened_at = 0.0
        self._filter_bar.set_visible(not empty or bool(self._query))
        self._no_match.set_visible(empty and bool(self._query))

    def _build_filter(self) -> None:
        bar = self._filter_bar = Gtk.Box(spacing=12, visible=False)
        bar.add_css_class("search-bar")
        bar.append(Sym("search", 24))
        entry = Gtk.Text(placeholder_text="Filter tracks", hexpand=True)
        entry.connect("changed", self._on_filter_changed)
        bar.append(entry)
        self._no_match = label("No tracks match", "body-m", "on-variant", visible=False)
        box = self._filter_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(bar)
        box.append(self._no_match)
        self.col.insert_child_after(box, self.spinner.get_prev_sibling())

    def _on_filter_changed(self, entry: Gtk.Text) -> None:
        if self._filter_source:
            GLib.source_remove(self._filter_source)
        self._filter_source = GLib.timeout_add(FILTER_DEBOUNCE_MS, self._filter_timeout, entry.get_text())

    def _filter_timeout(self, text: str) -> bool:
        self._filter_source = 0
        return self._apply_filter(text)

    def _apply_filter(self, text: str) -> bool:
        if self._filter_source:
            GLib.source_remove(self._filter_source)
            self._filter_source = 0
        self._query = text.strip()
        if self._media_list is not None:
            if self._server_filter:
                self._media_list.reset()
            else:
                self._media_list.set_query(self._query)
            return False
        shown = 0
        for row in self._order:
            match = matches_query(row.item, self._query)
            row.set_visible(match)
            shown += match
        # reordering assumes the full list, so it is off while rows are hidden
        for handle in self._handles:
            handle.set_visible(not self._query and bool(self._order))
        self._no_match.set_visible(bool(self._order) and not shown)
        return False

    async def _reload_tracks(self) -> None:
        if self._media_list is not None:
            self._force_refresh = True
            self._media_list.reset()
            return
        self._tracks_gen += 1
        gen = self._tracks_gen
        try:
            pairs = await self._fetch_tracks()
        except Exception as err:  # noqa: BLE001
            log.warning("could not reload playlist tracks: %s", err)
            if gen != self._tracks_gen or not self._busy:
                return
            # positions are unreliable until a list arrives, so keep editing locked and try again
            if self._reload_tries >= RELOAD_TRIES:
                self._give_up_reloading()
            elif not self._retry_source:
                self._reload_tries += 1
                self._retry_source = GLib.timeout_add(RELOAD_RETRY_MS, self._retry_reload)
            return
        if gen == self._tracks_gen and self._tracks_slot is not None:
            self._show_playlist_tracks(pairs)

    def _show_playlist_tracks(self, pairs: list[tuple]) -> None:
        self._clear(self._tracks_slot)
        self._remove_buttons = []
        self._handles = []
        self._order = []
        self._lb = self._drag_row = self._drop_row = None
        self._reload_tries = 0
        # a skipped (unresolvable) track leaves a gap in the server's positions; row order is only the fallback
        positions = [pos or i for i, (_t, pos) in enumerate(pairs, start=1)] if self._can_edit() else None
        self._add_tracks([t for t, _p in pairs], parent=self._tracks_slot, positions=positions)
        self._filter_bar.set_visible(bool(pairs))
        self._apply_filter(self._query)
        self._set_busy(False)

    def _shuffle(self) -> None:
        b = self.backend
        if b.client and (target := b.active_queue or b.active_player):
            qid = getattr(target, "queue_id", None) or target.player_id
            b.run(b.client.player_queues.play_media(qid, self.item, option=QueueOption.REPLACE, shuffle=True))

    async def _full_item(self):
        """The complete item (images, genres, bio) when we were handed a bare ItemMapping."""
        it = self.item
        if getattr(it, "metadata", None) is not None and getattr(it, "provider_mappings", None):
            return it
        try:
            full = await self.backend.client.music.get_item(it.media_type, it.item_id, it.provider)
        except Exception as err:  # noqa: BLE001
            log.debug("could not resolve %s: %s", getattr(it, "uri", it), err)
            return it
        self.item = full
        self._refresh_actions()
        self.art.set_item(full)
        self.subtitle.set_label(subtitle_for(full))
        return full

    def _sources(self, it) -> list[tuple[str, str]]:
        """(item_id, provider) for the item itself, then each streaming provider it maps to."""
        out = [(it.item_id, it.provider)]
        for m in getattr(it, "provider_mappings", None) or []:
            pair = (m.item_id, m.provider_instance)
            if pair not in out and getattr(m, "available", True):
                out.append(pair)
        return out

    async def _stream(self, fetch, sources, on_update, timeout: float = 60.0) -> None:
        """Fetch from every source at once; call on_update(all results so far, in source order)
        each time one answers, so fast sources show up without waiting for slow ones."""
        results: list[list] = [[] for _ in sources]

        async def one(idx, item_id, prov):
            try:
                results[idx] = await asyncio.wait_for(fetch(item_id, prov), timeout)
            except Exception as err:  # noqa: BLE001
                log.debug("%s from %s failed: %r", fetch.__name__, prov, err)
                return
            if results[idx]:
                on_update([x for lst in results for x in lst])

        await asyncio.gather(*(one(i, iid, p) for i, (iid, p) in enumerate(sources)))

    @staticmethod
    def _clear(box: Gtk.Box) -> None:
        while (c := box.get_first_child()) is not None:
            box.remove(c)

    def _slot(self) -> Gtk.Box:
        """A placeholder in page order, so sections can fill in whenever their data arrives."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.col.insert_child_after(box, self.spinner.get_prev_sibling())  # just above the spinner
        return box

    async def _load(self) -> None:
        music = self.backend.client.music
        try:
            it = await self._full_item()
            if it.media_type == MediaType.ALBUM:
                self._add_tracks(await music.get_album_tracks(it.item_id, it.provider), numbered=True)
                self._about(it)
            elif it.media_type == MediaType.PLAYLIST:
                if self._can_edit():
                    self._enable_playlist_editing()
                self._build_filter()
                self._tracks_slot = self._slot()
                await self._open_tracks()
            elif it.media_type == MediaType.ARTIST:
                await self._load_artist(it)
        finally:
            self.spinner.set_visible(False)

    async def _load_artist(self, it) -> None:
        music = self.backend.client.music
        sources = self._sources(it)
        # the library only knows what you saved; the providers know the whole discography.
        # Each section fills its own slot as soon as it's ready (a provider can be slow).
        tracks_slot, albums_slot = self._slot(), self._slot()

        def show_tracks(items):
            seen, out = set(), []
            for t in items:
                if (key := t.name.casefold()) not in seen:
                    seen.add(key)
                    out.append(t)
            self._clear(tracks_slot)
            self._section("Popular", tracks_slot)
            self._add_tracks(out[:10], parent=tracks_slot)

        def show_albums(items):
            seen, out = set(), []
            for a in items:
                if (key := (a.name.casefold(), getattr(a, "year", None))) not in seen:  # library copy wins
                    seen.add(key)
                    out.append(a)
            out.sort(key=lambda a: getattr(a, "year", None) or 0, reverse=True)
            self._clear(albums_slot)
            for title, kinds in ALBUM_GROUPS:
                group = [a for a in out if str(getattr(a.album_type, "value", a.album_type) or "unknown") in kinds]
                if group:
                    self._section(title, albums_slot)
                    self._album_grid(group, albums_slot)

        self._about(it)  # bio is already here; it sits below the slots
        await asyncio.gather(self._stream(music.get_artist_tracks, sources, show_tracks),
                             self._stream(music.get_artist_albums, sources, show_albums))

    def _album_grid(self, albums, parent: Gtk.Box | None = None) -> None:
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=8,
                           min_children_per_line=2, homogeneous=True, activate_on_single_click=True)
        for i, a in enumerate(albums):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            card.add_css_class("tile")
            art = ArtImage(self.backend, 150)
            art.set_item(a)
            card.append(art)
            card.append(label(a.name, "title-s", max_width_chars=1, margin_top=6))
            card.append(label(str(getattr(a, "year", "") or ""), "label-m", "on-variant"))
            tile = pop_in(card, delay_ms=min(i, 12) * 35)  # staggered, settling with a bounce
            tile.album = a
            flow.append(tile)
        flow.connect("child-activated", lambda _f, ch: self.open_item(ch.get_child().album))
        (parent or self.col).append(flow)

    def _about(self, it) -> None:
        desc = (getattr(getattr(it, "metadata", None), "description", None) or "").strip()
        if not desc:
            return
        self._section("About")
        text = label(desc, "body-l", "on-variant", ellipsize=False, wrap=True, lines=6, selectable=True)
        text.set_wrap_mode(2)
        text.set_ellipsize(3)
        self.col.append(text)
        if len(desc) > 400:
            more = Gtk.Button(label="Show more", halign=Gtk.Align.START)
            more.add_css_class("flat")

            def toggle(btn):
                expanded = text.get_lines() == -1
                text.set_lines(6 if expanded else -1)
                text.set_ellipsize(3 if expanded else 0)
                btn.set_label("Show more" if expanded else "Show less")

            more.connect("clicked", toggle)
            self.col.append(more)

    def _section(self, title: str, parent: Gtk.Box | None = None) -> None:
        (parent or self.col).append(label(title, "section-title", margin_top=12))

    def _add_tracks(self, tracks, numbered: bool = False, parent: Gtk.Box | None = None,
                    positions: list[int] | None = None) -> None:
        lb = m3_list()
        for i, t in enumerate(tracks):
            lead = None
            if numbered:
                lead = label(str(getattr(t, "track_number", "") or ""), "title-m", "on-variant", "numeric",
                             xalign=0.5, width_chars=2, ellipsize=False)
            row = media_row(self.backend, t, artists_str(t), art=not numbered, leading=lead,
                            trailing_text=fmt_time(t.duration) if t.duration else "", play_button=False,
                            on_activate=self._play_from)
            if positions is not None:
                rm = sym_button("playlist_remove", "Remove from playlist", cb=lambda p=positions[i]: self._remove_track(p))
                rm.set_sensitive(not self._busy)
                self._remove_buttons.append(rm)
                row.get_child().append(rm)
                row.position = positions[i]
                if self._can_reorder():
                    self._add_handle(row)
                self._order.append(row)
            lb.append(row)
        if positions is not None:
            self._lb = lb
            if self._can_reorder():
                keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
                keys.connect("key-pressed", self._on_key)
                lb.add_controller(keys)
        (parent or self.col).append(pop_in(lb, dy=20, scale=0.98, preset="expressive"))

    def _play_from(self, track) -> None:
        if self.item.media_type in (MediaType.ALBUM, MediaType.PLAYLIST):
            self.backend.play_media(self.item, option=QueueOption.REPLACE, start_item=track.item_id)
        else:
            self.backend.play_media(track, option=QueueOption.PLAY)
