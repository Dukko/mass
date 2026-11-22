"""Browse music providers: folders and the media inside them."""

from __future__ import annotations

import logging

from gi.repository import Adw, GLib, Gtk
from music_assistant_models.enums import MediaType

from ..backend import BROWSE_PAGING_SCHEMA
from .common import ItemObject, m3_list, media_row, status_page
from .expressive import Sym
from .media_list import MediaList
from .paging import PAGE, Pager

log = logging.getLogger(__name__)

OPENABLE = {MediaType.FOLDER, MediaType.ALBUM, MediaType.ARTIST, MediaType.PLAYLIST, MediaType.PODCAST,
            MediaType.GENRE}
UNPAGED = 1 << 30
CHUNK = 50
VIRTUALIZE_ABOVE = 300


def subtitle_for(item) -> str:
    from .library import subtitle_for as library_subtitle

    return library_subtitle(item)


async def _no_more(_offset: int, _limit: int) -> list:
    return []


class BrowseList(Gtk.Stack):
    """One level of the provider tree; `path=None` lists the provider roots.

    Small folders are ListBox rows; once a folder passes `VIRTUALIZE_ABOVE` items it moves to a
    virtualized `MediaList` that this widget feeds, so huge folders never build thousands of rows.
    """

    def __init__(self, backend, path: str | None, open_item) -> None:
        super().__init__(vexpand=True)
        self.backend, self.path, self.open_item = backend, path, open_item
        paged = backend.supports(BROWSE_PAGING_SCHEMA)
        self._pager = Pager(self._fetch, self._on_items, backend.run, page=PAGE if paged else UNPAGED)
        self._paged = paged
        self._gen = 0
        self._items: list = []
        self._pending: list = []
        self._big = False
        self._idle = 0
        self._status_handler = 0

        spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                              width_request=32, height_request=32)
        self.add_named(spinner, "loading")
        clamp = Adw.Clamp(maximum_size=980, margin_bottom=120, margin_start=20, margin_end=20, margin_top=12)
        self.list = m3_list()
        clamp.set_child(self.list)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp)
        self.add_named(self.scroller, "list")
        self.big = MediaList(backend, _no_more, self._activate, subtitle_for,
                          menu=lambda item: item.media_type != MediaType.FOLDER)
        self.add_named(self.big, "big")
        self.add_named(status_page("folder-symbolic", "Nothing to browse here"), "empty")
        self.error = status_page("dialog-error-symbolic", "Couldn't load this folder")
        retry = Gtk.Button(label="Retry", halign=Gtk.Align.CENTER)
        retry.add_css_class("pill")
        retry.connect("clicked", lambda _b: self.reset())
        self.error.set_child(retry)
        self.add_named(self.error, "error")

        self.scroller.get_vadjustment().connect("value-changed", self._on_scroll)
        self.big.get_vadjustment().connect("value-changed", self._on_scroll)
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)

    def _on_map(self, _w) -> None:
        if not self._status_handler:
            self._status_handler = self.backend.connect("status-changed", self._on_status)
        self._first_load()

    def _on_unmap(self, _w) -> None:
        if self._status_handler:
            self.backend.disconnect(self._status_handler)
            self._status_handler = 0

    def _on_status(self, _b, status: str, _detail: str) -> None:
        if status == "connected":
            self._first_load()

    async def _fetch(self, offset: int, limit: int) -> list:
        if not self._paged and offset:
            return []
        gen = self._gen
        try:
            music = self.backend.client.music
            if self._paged:
                return await music.browse(self.path, limit=limit, offset=offset)
            return await music.browse(self.path)
        except Exception as err:
            log.error("browse %s failed: %s", self.path, err)
            if offset == 0 and gen == self._gen:
                self.error.set_description(str(err))
                self.set_visible_child_name("error")
            raise

    def _first_load(self) -> None:
        if not self._pager.loaded_once:
            self.load_more()

    def reset(self) -> None:
        self._gen += 1
        self._cancel_chunks()
        self.list.remove_all()
        self.big.store.remove_all()
        self._items.clear()
        self._big = False
        self.set_visible_child_name("loading")
        self._pager.reset()
        if self.get_mapped():
            self.load_more()

    def load_more(self) -> None:
        if self.backend.connected:
            self._pager.load_more()

    def _on_scroll(self, adj: Gtk.Adjustment) -> None:
        if self._paged and adj.get_value() + adj.get_page_size() > adj.get_upper() - 800:
            self.load_more()

    def _on_items(self, items: list, first_page: bool) -> None:
        self._items.extend(items)
        if self._big:
            self.big.store.splice(self.big.store.get_n_items(), 0, [ItemObject(i) for i in items])
            return
        if len(self._items) > VIRTUALIZE_ABOVE:
            self._go_big()
            return
        self._pending.extend(items)
        if first_page:
            self.set_visible_child_name("list" if items else "empty")
        if self._pending and not self._idle:
            self._idle = GLib.idle_add(self._append_chunk)

    def _go_big(self) -> None:
        self._cancel_chunks()
        self.list.remove_all()
        self._big = True
        self.big.store.splice(0, 0, [ItemObject(i) for i in self._items])
        self.set_visible_child_name("big")

    def _activate(self, item) -> None:
        if item.media_type in OPENABLE:
            self.open_item(item)
        else:
            self.backend.play_media(item)

    def _cancel_chunks(self) -> None:
        self._pending.clear()
        if self._idle:
            GLib.source_remove(self._idle)
            self._idle = 0

    def _append_chunk(self) -> bool:
        chunk, self._pending = self._pending[:CHUNK], self._pending[CHUNK:]
        for item in chunk:
            self.list.append(self._row(item))
        if self._pending:
            return GLib.SOURCE_CONTINUE
        self._idle = 0
        return GLib.SOURCE_REMOVE

    def _row(self, item):
        if item.media_type == MediaType.FOLDER:
            return media_row(self.backend, item, art=False, leading=Sym("folder", 24), menu=False,
                             play_button=bool(getattr(item, "is_playable", False)),
                             on_activate=self.open_item)
        if item.media_type in OPENABLE:
            return media_row(self.backend, item, subtitle_for(item), on_activate=self.open_item)
        return media_row(self.backend, item, subtitle_for(item), on_activate=self.backend.play_media)


class BrowsePage(Adw.NavigationPage):
    """A folder opened from the Browse tab or from another folder."""

    def __init__(self, backend, folder, open_item) -> None:
        super().__init__(title=folder.name)
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar(show_title=False)
        hb.add_css_class("flat-header")
        tv.add_top_bar(hb)
        tv.set_content(BrowseList(backend, folder.path, open_item))
        self.set_child(tv)
