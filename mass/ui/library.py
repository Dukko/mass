"""Library browsing: lazy grids and the library tabs (M3 Expressive)."""

from __future__ import annotations

import logging
from collections.abc import Callable

from gi.repository import Gdk, Gio, GLib, Gtk
from music_assistant_models.enums import EventType, MediaType, SortDirection, SortField

from .common import (
    ArtImage,
    ItemObject,
    artists_str,
    ask_text,
    attach_media_actions,
    label,
    media_menu_model,
)
from .expressive import sym_button
from .paging import PAGE, Pager

CHANGE_DEBOUNCE_MS = 300
log = logging.getLogger(__name__)


def subtitle_for(item) -> str:
    mt = getattr(item, "media_type", None)
    if mt in (MediaType.ALBUM, MediaType.TRACK):
        s = artists_str(item)
        if mt == MediaType.ALBUM and getattr(item, "year", None):
            s = f"{s} · {item.year}" if s else str(item.year)
        return s
    if mt == MediaType.PLAYLIST:
        return getattr(item, "owner", "") or "Playlist"
    if mt == MediaType.RADIO:
        return "Radio"
    if mt == MediaType.ARTIST:
        genres = sorted(getattr(getattr(item, "metadata", None), "genres", None) or [])
        return " · ".join(g.title() for g in genres[:3]) or "Artist"
    return ""


def library_sort(backend, field: SortField, direction: SortDirection, legacy: str) -> dict:
    # typed sort options arrived in server schema 86; older servers take order_by
    info = backend.client.server_info if backend.client else None
    if info and info.schema_version >= 86:
        return {"sort_field": field, "sort_direction": direction}
    return {"order_by": legacy}


class MediaGrid(Gtk.ScrolledWindow):
    """Infinite-scrolling grid of shaped cards backed by `fetch(offset, limit)`."""

    def __init__(self, backend, fetch, on_open, card_size: int = 168, center_text: bool = False,
                 menu: bool = True, on_empty: Callable[[bool], None] | None = None) -> None:
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.backend, self.fetch, self.on_open = backend, fetch, on_open
        self.card_size = card_size
        self.center_text = center_text
        self.menu = menu
        self.on_empty = on_empty
        self.store = Gio.ListStore(item_type=ItemObject)
        self._pager = Pager(fetch, self._on_items, backend.run, PAGE)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup)
        factory.connect("bind", self._bind)
        self.grid = Gtk.GridView(model=Gtk.NoSelection(model=self.store), factory=factory,
                                 max_columns=12, min_columns=2, single_click_activate=True)
        self.grid.set_margin_start(20)
        self.grid.set_margin_end(20)
        self.grid.set_margin_bottom(110)  # room for the floating mini player
        self.grid.connect("activate", lambda _g, pos: self.on_open(self.store.get_item(pos).item))
        self.set_child(self.grid)
        self.get_vadjustment().connect("value-changed", self._on_scroll)
        self.connect("map", lambda *_: self._pager.loaded_once or self.load_more())

    def _setup(self, _f, li) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=10, margin_bottom=12,
                      margin_start=10, margin_end=10)
        art = ArtImage(self.backend, self.card_size)
        xa = 0.5 if self.center_text else 0
        title = label("", "title-s", xalign=xa, max_width_chars=1, hexpand=True, margin_top=8)
        sub = label("", "label-m", "on-variant", xalign=xa, max_width_chars=1)
        box.append(art)
        box.append(title)
        box.append(sub)
        if self.menu:
            pop = Gtk.PopoverMenu.new_from_model(media_menu_model())
            pop.set_parent(box)
            pop.set_has_arrow(False)
            click = Gtk.GestureClick(button=3)

            def show_menu(_g, _n, x, y):
                rect = Gdk.Rectangle()
                rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
                pop.set_pointing_to(rect)
                pop.popup()

            click.connect("pressed", show_menu)
            box.add_controller(click)
        box.parts = (art, title, sub)
        box.current = None
        box.refresh_actions = attach_media_actions(box, self.backend, lambda: box.current)
        li.set_child(box)

    def _bind(self, _f, li) -> None:
        box = li.get_child()
        item = li.get_item().item
        art, title, sub = box.parts
        box.current = item
        box.refresh_actions()
        art.set_item(item)
        title.set_label(item.name)
        sub.set_label(subtitle_for(item))

    def reset(self) -> None:
        self.store.remove_all()
        self._pager.reset()
        if self.get_mapped():
            self.load_more()

    def _on_scroll(self, adj) -> None:
        if adj.get_value() + adj.get_page_size() > adj.get_upper() - 800:
            self.load_more()

    def load_more(self) -> None:
        if self.backend.connected:
            self._pager.load_more()

    def _on_items(self, items: list, first_page: bool) -> None:
        self.store.splice(self.store.get_n_items(), 0, [ItemObject(i) for i in items])
        if first_page and self.on_empty is not None:
            self.on_empty(not items)


class LibraryView(Gtk.Box):
    """Display header + segmented button group over Albums / Artists / Playlists / Radio."""

    def __init__(self, backend, open_item) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.backend = backend
        music = lambda: backend.client.music  # noqa: E731
        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=200)
        self.grids: list[MediaGrid] = []

        self.head = Gtk.Box(spacing=16, margin_start=28, margin_end=28, margin_top=56, margin_bottom=12)
        self.head.append(label("Library", "display-m", hexpand=True, ellipsize=False))
        self.new_playlist = sym_button("playlist_add", "New playlist", ("m3", "icon", "tonal"), cb=self._new_playlist)
        self.new_playlist.set_halign(Gtk.Align.START)
        self.new_playlist.set_visible(False)
        self.head.append(self.new_playlist)
        self._playlists_grid: MediaGrid | None = None
        self._playlists_source = 0
        self.seg = Gtk.Box(valign=Gtk.Align.CENTER)
        self.seg.add_css_class("seg")
        self.head.append(self.seg)
        self.append(self.head)
        self.append(self.stack)
        first = None

        def add(name, title, fetch, size=168, center=False):
            nonlocal first
            grid = MediaGrid(backend, fetch, open_item, size, center)
            self.grids.append(grid)
            self.stack.add_named(grid, name)
            btn = Gtk.ToggleButton(label=title, group=first)
            first = first or btn
            btn.connect("toggled", lambda b: b.get_active() and self._show_tab(name))
            self.seg.append(btn)
            if name == "playlists":
                self._playlists_grid = grid

        S = lambda f, d, legacy: library_sort(backend, f, d, legacy)  # noqa: E731
        add("albums", "Albums", lambda o, n: music().get_library_albums(
            limit=n, offset=o, **S(SortField.TIMESTAMP_ADDED, SortDirection.DESC, "timestamp_added_desc")))
        add("artists", "Artists", lambda o, n: music().get_library_artists(
            limit=n, offset=o, **S(SortField.SORT_NAME, SortDirection.ASC, "sort_name")), 150, True)
        add("playlists", "Playlists", lambda o, n: music().get_library_playlists(
            limit=n, offset=o, **S(SortField.SORT_NAME, SortDirection.ASC, "sort_name")))
        add("radios", "Radio", lambda o, n: music().get_library_radios(
            limit=n, offset=o, **S(SortField.SORT_NAME, SortDirection.ASC, "sort_name")), 130, True)
        first.set_active(True)
        backend.connect("status-changed", self._on_status)
        backend.connect("media-item-changed", self._on_media_changed)

    def _show_tab(self, name: str) -> None:
        self.stack.set_visible_child_name(name)
        self.new_playlist.set_visible(name == "playlists")

    def _new_playlist(self) -> None:
        ask_text(self, "New playlist", "", "Playlist name", "Create", self._create_playlist)

    def _create_playlist(self, text: str) -> None:
        name = text.strip()
        if not name:
            return

        async def go() -> None:
            try:
                await self.backend.create_playlist(name)
            except Exception as err:  # noqa: BLE001
                log.error("creating playlist failed: %s", err)
                root = self.get_root()
                if hasattr(root, "toast"):
                    root.toast(f"Could not create {name}: {err}")

        self.backend.run(go())

    def _on_media_changed(self, _b, event: str, uri: str) -> None:
        # updates are frequent (favourites, metadata syncs) and don't change which playlists exist
        if "://playlist/" not in uri or event not in (EventType.MEDIA_ITEM_ADDED.value, EventType.MEDIA_ITEM_DELETED.value):
            return
        if self._playlists_source:
            GLib.source_remove(self._playlists_source)
        self._playlists_source = GLib.timeout_add(CHANGE_DEBOUNCE_MS, self._reset_playlists)

    def _reset_playlists(self) -> bool:
        self._playlists_source = 0
        self._playlists_grid.reset()
        return False

    def set_narrow(self, narrow: bool) -> None:
        self.head.set_orientation(Gtk.Orientation.VERTICAL if narrow else Gtk.Orientation.HORIZONTAL)
        self.seg.set_halign(Gtk.Align.START if narrow else Gtk.Align.FILL)

    def _on_status(self, _b, status, _d) -> None:
        if status == "connected":
            for g in self.grids:
                g.reset()
