"""Library browsing: lazy grids and the library tabs (M3 Expressive)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from music_assistant_models.enums import EventType, ListingType, MediaType, QueueOption, SortDirection, SortField

from ..listing import (
    LISTING_FILTER_SCHEMA,
    LibraryKind,
    ListingState,
    effective_state,
    listing_kwargs,
    state_from_json,
    state_to_json,
)
from .common import (
    ArtImage,
    ItemObject,
    artists_str,
    ask_text,
    attach_media_actions,
    label,
    media_menu_model,
)
from .browse import BrowseList
from .expressive import sym_button
from .library_toolbar import LibraryToolbar
from .media_list import MediaList
from .paging import PAGE, Pager

CHANGE_DEBOUNCE_MS = 300
SAVE_DEBOUNCE_MS = 800
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
    if mt == MediaType.GENRE:
        return "Genre"
    if mt == MediaType.PODCAST:
        return getattr(item, "publisher", "") or "Podcast"
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
    """Display header, segmented tab group and per-tab search/sort/filter toolbar over the library grids.

    Each tab keeps its own `ListingState`, saved to the config so it survives restarts.
    """

    def __init__(self, backend, open_item) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.backend = backend
        self.open_item = open_item
        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=200)
        self.tabs: dict[str, LibraryTab] = {}
        self.states: dict[str, ListingState] = {}
        self.current = ""
        self._first_button: Gtk.ToggleButton | None = None
        self._playlists_source = 0
        self._save_source = 0
        self._genres_tab = False  # survives reconnects so the Genres tab doesn't flicker out under the user

        self.head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=28, margin_end=28,
                            margin_top=56, margin_bottom=12)
        title_row = Gtk.Box(spacing=16)
        title_row.append(label("Library", "display-m", hexpand=True, ellipsize=False))
        self.new_playlist = sym_button("playlist_add", "New playlist", ("m3", "icon", "tonal"), cb=self._new_playlist)
        self.new_playlist.set_visible(False)
        title_row.append(self.new_playlist)
        self.head.append(title_row)
        self.seg = Gtk.Box(valign=Gtk.Align.CENTER)
        self.seg.add_css_class("seg")
        self.seg_scroll = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                             hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, child=self.seg)
        self.head.append(self.seg_scroll)
        self.toolbar = LibraryToolbar(backend, self._on_toolbar_change)
        self.toolbar.set_visible(False)
        self.connect("destroy", lambda *_: self.flush_save())
        self.connect("unmap", lambda *_: self.flush_save())
        self.head.append(self.toolbar)
        self.append(self.head)
        self.append(self.stack)

        def fetcher(name: str, getter: str):
            async def fetch(o: int, n: int):
                if self._genres_unknown(name):
                    await self._probe_genres()
                return await getattr(backend.client.music, getter)(limit=n, offset=o, **self._kwargs(name))

            return fetch

        def grid_tab(name: str, title: str, kind: LibraryKind, listing: ListingType, getter: str,
                     size: int = 168, center: bool = False, menu: bool = True,
                     available: Callable[[], bool] = lambda: True) -> None:
            self.add_tab(name, title, MediaGrid(backend, fetcher(name, getter), open_item, size, center, menu),
                         kind, listing, available)

        grid_tab("albums", "Albums", "albums", ListingType.LIBRARY_ALBUMS, "get_library_albums")
        grid_tab("artists", "Artists", "artists", ListingType.LIBRARY_ARTISTS, "get_library_artists", 150, True)
        self.add_tab("tracks", "Tracks",
                     MediaList(backend, fetcher("tracks", "get_library_tracks"),
                               lambda t: backend.play_media(t, option=QueueOption.PLAY)),
                     "tracks", ListingType.LIBRARY_TRACKS)
        grid_tab("playlists", "Playlists", "playlists", ListingType.LIBRARY_PLAYLISTS, "get_library_playlists")
        grid_tab("radios", "Radio", "radios", ListingType.LIBRARY_RADIOS, "get_library_radios", 130, True)
        grid_tab("podcasts", "Podcasts", "podcasts", ListingType.LIBRARY_PODCASTS, "get_library_podcasts", 150)
        grid_tab("genres", "Genres", "genres", ListingType.LIBRARY_GENRES, "get_library_genres", 150, True,
                 menu=False, available=lambda: self._genres_tab)
        self.add_tab("browse", "Browse", BrowseList(backend, None, open_item))
        self._first_button.set_active(True)
        backend.connect("status-changed", self._on_status)
        backend.connect("media-item-changed", self._on_media_changed)

    def add_tab(self, name: str, title: str, view: Gtk.Widget, kind: LibraryKind | None = None,
                listing: ListingType | None = None, available: Callable[[], bool] = lambda: True) -> None:
        """Register a tab. `view` needs `reset()`; a `MediaGrid` or `MediaList` also gets the empty/no-matches page.

        `kind` and `listing` enable the toolbar and the saved listing state for the tab.
        """
        saved = self.backend.config.library_listings.get(name)
        self.states[name] = state_from_json(saved) if kind else ListingState()
        wrapper = Gtk.Stack()
        wrapper.add_named(view, "grid")
        status = Adw.StatusPage(icon_name="folder-music-symbolic")
        clear = Gtk.Button(label="Clear filters", halign=Gtk.Align.CENTER)
        clear.add_css_class("pill")
        clear.connect("clicked", lambda *_: self._clear_filters(name))
        status.set_child(clear)
        wrapper.add_named(status, "empty")
        btn = Gtk.ToggleButton(label=title, group=self._first_button)
        self._first_button = self._first_button or btn
        btn.connect("toggled", lambda b: b.get_active() and self._show_tab(name))
        self.seg.append(btn)
        self.stack.add_named(wrapper, name)
        self.tabs[name] = LibraryTab(name, title, kind, listing, available, view, wrapper, status, clear, btn)
        btn.set_visible(available())
        if isinstance(view, (MediaGrid, MediaList)):
            view.on_empty = lambda empty: self._on_empty(name, empty)

    def _effective(self, name: str) -> ListingState:
        return effective_state(self.states[name], self.tabs[name].kind, self.backend.schema,
                               len(self.backend.music_providers()), self.toolbar.genres_available)

    def _kwargs(self, name: str) -> dict:
        return listing_kwargs(self._effective(name), self.tabs[name].kind, self.backend.schema)

    def _reset_tab(self, name: str) -> None:
        tab = self.tabs[name]
        tab.wrapper.set_visible_child_name("grid")
        tab.view.reset()

    def _genres_unknown(self, name: str) -> bool:
        return (self.toolbar.genres_usable is None and bool(self.states[name].genre_ids)
                and self.backend.schema >= LISTING_FILTER_SCHEMA)

    async def _probe_genres(self) -> None:
        """Learn once per connection whether genre filters work; a flip resets tabs that use them."""
        usable = await self.backend.has_genres()
        previous = self.toolbar.genres_usable
        self.toolbar.set_genres_usable(usable)
        self._genres_tab = usable
        if previous is not None and previous != usable:
            for name, tab in self.tabs.items():
                if tab.kind and self.states[name].genre_ids:
                    self._reset_tab(name)
        self._update_tabs()

    def _update_tabs(self) -> None:
        for tab in self.tabs.values():
            tab.button.set_visible(tab.available())
        current = self.tabs.get(self.current)
        if current is None or not current.available():
            first = next((t for t in self.tabs.values() if t.available()), None)
            if first is not None:
                first.button.set_active(True)

    def flush_save(self) -> None:
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_states()

    def _on_empty(self, name: str, empty: bool) -> None:
        tab = self.tabs[name]
        filtered = self._effective(name).is_filtered()
        tab.status.set_title("No matches" if filtered else "Nothing here yet")
        tab.status.set_description("Try changing or clearing the filters." if filtered else "")
        tab.clear.set_visible(filtered)
        tab.wrapper.set_visible_child_name("empty" if empty else "grid")

    def _clear_filters(self, name: str) -> None:
        self._set_state(name, self.states[name].cleared())
        self.toolbar.show_tab(self.tabs[name].kind, self.tabs[name].listing, self.states[name], self.tabs[name].title)

    def _on_toolbar_change(self, state: ListingState) -> None:
        self._set_state(self.current, state)

    def _set_state(self, name: str, state: ListingState) -> None:
        self.states[name] = state
        self._reset_tab(name)
        self._schedule_save()

    def _schedule_save(self) -> None:
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(SAVE_DEBOUNCE_MS, self._save_states)

    def _save_states(self) -> bool:
        self._save_source = 0
        config = self.backend.config
        config.library_listings = {n: state_to_json(s) for n, s in self.states.items() if self.tabs[n].kind}
        try:
            config.save()
        except OSError as err:
            log.warning("saving library filters failed: %s", err)
        return False

    def _show_tab(self, name: str) -> None:
        self.toolbar.flush()
        self.current = name
        tab = self.tabs[name]
        self.stack.set_visible_child_name(name)
        self.new_playlist.set_visible(name == "playlists")
        self.toolbar.set_visible(tab.kind is not None)
        if tab.kind is not None:
            self.toolbar.show_tab(tab.kind, tab.listing, self.states[name], tab.title)

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
        self._reset_tab("playlists")
        return False

    def set_narrow(self, narrow: bool) -> None:
        self.head.set_margin_start(16 if narrow else 28)
        self.head.set_margin_end(16 if narrow else 28)
        self.seg_scroll.set_halign(Gtk.Align.FILL if narrow else Gtk.Align.START)

    def _on_status(self, _b, status, _d) -> None:
        if status != "connected":
            return
        self.toolbar.genres_usable = None
        self._update_tabs()
        self.backend.run(self._probe_genres())
        if self.toolbar.get_visible():
            self.toolbar.refresh_capabilities()
        for name in self.tabs:
            self._reset_tab(name)


@dataclass
class LibraryTab:
    name: str
    title: str
    kind: LibraryKind | None
    listing: ListingType | None
    available: Callable[[], bool]
    view: Gtk.Widget
    wrapper: Gtk.Stack
    status: Adw.StatusPage
    clear: Gtk.Button
    button: Gtk.ToggleButton
