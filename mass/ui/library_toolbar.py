"""Search, favorites, sort and filter controls above a library grid."""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, replace

from gi.repository import GLib, Gtk
from music_assistant_models.api import SortOptionInfo
from music_assistant_models.enums import AlbumType, ListingType, SortDirection, SortField

from ..listing import (
    DEFAULT_SORT,
    RANDOM_FIELDS,
    SORT_LABELS,
    TYPED_SORT_SCHEMA,
    LibraryKind,
    ListingState,
    available_filters,
    effective_state,
    fallback_sort_fields,
    listing_kwargs,
    usable_sort_fields,
)

from .common import label
from .expressive import Sym

SEARCH_DEBOUNCE_MS = 300
GENRE_LIMIT = 500
ALBUM_TYPE_CHOICES = (AlbumType.ALBUM, AlbumType.SINGLE, AlbumType.EP, AlbumType.COMPILATION,
                      AlbumType.LIVE, AlbumType.SOUNDTRACK)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SortChoice:
    field: SortField
    label: str
    supports_direction: bool
    default_direction: SortDirection


def field_label(field: SortField) -> str:
    return SORT_LABELS.get(field, field.value.replace("_", " ").capitalize())


def sort_choices(kind: LibraryKind, options: list[SortOptionInfo], schema: int) -> list[SortChoice]:
    """Sort menu entries: the server's typed options when it has them, else the fixed list per kind."""
    default_field, default_dir = DEFAULT_SORT[kind]
    if schema >= TYPED_SORT_SCHEMA and options:
        raw = [(o.field, o.supports_direction, o.default_direction) for o in usable_sort_fields(options, schema)]
    else:
        raw = [(f, True, None) for f in fallback_sort_fields(kind)]
    choices: list[SortChoice] = []
    for field, supports_direction, direction in raw:
        text = field_label(field)
        if any(c.label == text for c in choices):
            continue
        if direction is None:
            direction = default_dir if field == default_field else (
                SortDirection.DESC if field in (SortField.TIMESTAMP_ADDED, SortField.LAST_PLAYED, SortField.PLAY_COUNT)
                else SortDirection.ASC)
        choices.append(SortChoice(field, text, supports_direction, direction))
    return choices


def _menu_button(text: str, popover: Gtk.Popover) -> Gtk.MenuButton:
    btn = Gtk.MenuButton(popover=popover, valign=Gtk.Align.CENTER, direction=Gtk.ArrowType.DOWN)
    btn.add_css_class("chip")
    btn.set_label(text)
    return btn


def _option_row(text: str, checked: bool, cb: Callable[[], None]) -> Gtk.Button:
    row = Gtk.Button()
    row.add_css_class("flat")
    box = Gtk.Box(spacing=10)
    mark = Sym("check", 18)
    mark.set_opacity(1.0 if checked else 0.0)
    box.append(mark)
    box.append(label(text, hexpand=True))
    row.set_child(box)
    row.update_state([Gtk.AccessibleState.CHECKED], [int(Gtk.AccessibleTristate.TRUE if checked else Gtk.AccessibleTristate.FALSE)])
    row.connect("clicked", lambda *_: cb())
    return row


class LibraryToolbar(Gtk.Box):
    """Edits one `ListingState` at a time; `on_change` receives each new state."""

    def __init__(self, backend, on_change: Callable[[ListingState], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.backend = backend
        self._on_change = on_change
        self._kind: LibraryKind | None = None
        self._listing: ListingType | None = None
        self._title = ""
        self._state = ListingState()
        self._syncing = False
        self._debounce = 0
        self._sort_options: list[SortOptionInfo] = []
        self._genres: list[tuple[int, str]] | None = None
        self._genres_loading = False
        self.genres_usable: bool | None = None
        self._genre_rows: list[tuple[str, Gtk.CheckButton]] = []

        bar = Gtk.Box(spacing=12)
        bar.add_css_class("search-bar")
        bar.append(Sym("search", 24))
        self.entry = Gtk.Text(hexpand=True)
        self.entry.connect("changed", self._on_text_changed)
        self.entry.connect("activate", lambda *_: self._apply_search())
        bar.append(self.entry)
        self.clear_btn = Gtk.Button(valign=Gtk.Align.CENTER, visible=False, tooltip_text="Clear search")
        self.clear_btn.add_css_class("flat")
        self.clear_btn.add_css_class("circular")
        self.clear_btn.set_child(Sym("close", 20))
        self.clear_btn.connect("clicked", lambda *_: self.entry.set_text(""))
        bar.append(self.clear_btn)
        self.append(bar)

        chips = Gtk.Box(spacing=8)
        self.favorites = Gtk.ToggleButton(label="Favorites", valign=Gtk.Align.CENTER)
        self.favorites.add_css_class("chip")
        self.favorites.connect("toggled", self._on_favorites)
        chips.append(self.favorites)
        self.sort_pop = Gtk.Popover()
        self.sort_btn = _menu_button("Sort", self.sort_pop)
        chips.append(self.sort_btn)
        self.filter_pop = Gtk.Popover()
        self.filter_pop.connect("show", self._on_filters_shown)
        self.filter_btn = _menu_button("Filters", self.filter_pop)
        chips.append(self.filter_btn)
        self.append(chips)

    @property
    def state(self) -> ListingState:
        return self._state

    @property
    def genres_available(self) -> bool:
        return self.genres_usable is not False

    def set_genres_usable(self, usable: bool | None) -> None:
        """Server-wide genre capability, probed by the owner; None until known."""
        self.genres_usable = usable
        self._rebuild_filters()

    def flush(self) -> None:
        """Apply a search still waiting on its debounce to the tab being edited."""
        if self._debounce:
            self._cancel_debounce()
            self._apply_search()

    def show_tab(self, kind: LibraryKind, listing: ListingType, state: ListingState, title: str = "") -> None:
        self._cancel_debounce()
        self._kind, self._listing, self._state = kind, listing, state
        self._title = title or self._title
        self._sync_widgets()
        self._refresh_tab()

    def refresh_capabilities(self) -> None:
        """Re-read what the connected server offers; call after (re)connecting."""
        self._genres = None
        self._refresh_tab()

    def _refresh_tab(self) -> None:
        if self._kind is None:
            return
        self._sort_options = []
        self._rebuild_sort()
        self._rebuild_filters()
        if self.backend.connected:
            self.backend.run(self._load_sort_options(self._kind))

    async def _load_sort_options(self, kind: LibraryKind) -> None:
        listing = self._listing
        options = await self.backend.sort_options(listing) if listing is not None else []
        if kind != self._kind:
            return
        self._sort_options = options
        self._rebuild_sort()

    def _filters_for(self, kind: LibraryKind) -> frozenset[str]:
        found = available_filters(kind, self.backend.schema, len(self.backend.music_providers()))
        if self.genres_usable is False:
            found -= {"genre"}
        return found

    def _sync_widgets(self) -> None:
        self._syncing = True
        try:
            self.entry.set_placeholder_text(f"Filter {self._title.lower()}…" if self._title else "Filter…")
            self.entry.update_property([Gtk.AccessibleProperty.LABEL],
                                       [f"Filter {self._title.lower()}" if self._title else "Filter"])
            self.entry.set_text(self._state.search)
            self.clear_btn.set_visible(bool(self._state.search))
            self.favorites.set_active(self._state.favorite_only)
        finally:
            self._syncing = False

    def _emit(self, state: ListingState) -> None:
        if self._syncing or state == self._state:
            return
        self._state = state
        self._on_change(state)

    def _cancel_debounce(self) -> None:
        if self._debounce:
            GLib.source_remove(self._debounce)
            self._debounce = 0

    def _on_text_changed(self, _entry) -> None:
        if self._syncing:
            return
        self.clear_btn.set_visible(bool(self.entry.get_text()))
        self._cancel_debounce()
        self._debounce = GLib.timeout_add(SEARCH_DEBOUNCE_MS, self._apply_search)

    def _apply_search(self) -> bool:
        self._debounce = 0
        self._emit(replace(self._state, search=self.entry.get_text()))
        return False

    def _on_favorites(self, btn: Gtk.ToggleButton) -> None:
        self._emit(replace(self._state, favorite_only=btn.get_active()))

    def _current_sort(self) -> tuple[SortField, SortDirection]:
        default_field, default_dir = DEFAULT_SORT[self._kind]
        field = self._state.sort_field or default_field
        direction = self._state.sort_direction or (default_dir if field == default_field else SortDirection.ASC)
        return field, direction

    def _rebuild_sort(self) -> None:
        if self._kind is None:
            return
        choices = sort_choices(self._kind, self._sort_options, self.backend.schema)
        field, direction = self._current_sort()
        current = next((c for c in choices if c.field == field), None)
        text = current.label if current else field_label(field)
        if current is None or current.supports_direction:
            text += " ↑" if direction == SortDirection.ASC else " ↓"
        self.sort_btn.set_label(text)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6)
        for choice in choices:
            box.append(_option_row(choice.label, choice.field == field, lambda c=choice: self._choose_field(c)))
        if current is None or current.supports_direction:
            box.append(Gtk.Separator(margin_top=4, margin_bottom=4))
            for text_, value in (("Ascending", SortDirection.ASC), ("Descending", SortDirection.DESC)):
                box.append(_option_row(text_, direction == value, lambda v=value: self._choose_direction(v)))
        self.sort_pop.set_child(box)

    def _choose_field(self, choice: SortChoice) -> None:
        self.sort_pop.popdown()
        seed = random.randrange(2**31) if choice.field in RANDOM_FIELDS else None
        self._emit_sort(replace(self._state, sort_field=choice.field, sort_direction=choice.default_direction,
                                random_seed=seed))

    def _choose_direction(self, direction: SortDirection) -> None:
        self.sort_pop.popdown()
        field, _ = self._current_sort()
        self._emit_sort(replace(self._state, sort_field=field, sort_direction=direction))

    def _emit_sort(self, state: ListingState) -> None:
        self._emit(state)
        self._rebuild_sort()

    def _filter_count(self) -> int:
        return effective_state(self._state, self._kind, self.backend.schema, len(self.backend.music_providers()),
                               self.genres_available).filter_count()

    def _rebuild_filters(self) -> None:
        if self._kind is None:
            return
        filters = self._filters_for(self._kind)
        self.filter_btn.set_visible(bool(filters))
        count = self._filter_count()
        self.filter_btn.set_label(f"Filters · {count}" if count else "Filters")
        self._genre_rows = []
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12, width_request=280)
        if "genre" in filters:
            box.append(self._genre_section())
        if "provider" in filters:
            box.append(self._provider_section())
        if "album_type" in filters:
            box.append(self._album_type_section())
        if "played" in filters:
            box.append(self._played_row())
        clear = Gtk.Button(label="Clear", halign=Gtk.Align.END)
        clear.add_css_class("flat")
        clear.set_sensitive(count > 0)
        clear.connect("clicked", lambda *_: self._clear_filters())
        box.append(clear)
        self.filter_pop.set_child(box)

    def _clear_filters(self) -> None:
        self._emit(replace(self._state, genre_ids=(), providers=(), album_types=(), played_only=False))
        self._rebuild_filters()

    def _section(self, title: str) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(label(title, "label-l", "on-variant"))
        return box

    def _check_list(self, entries: list[tuple[str, object]], selected: set, toggle: Callable[[object, bool], None]
                    ) -> Gtk.ListBox:
        lst = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lst.add_css_class("m3-list")
        for text, value in entries:
            check = Gtk.CheckButton(label=text, active=value in selected)
            check.connect("toggled", lambda b, v=value: toggle(v, b.get_active()))
            lst.append(check)
        return lst

    def _provider_section(self) -> Gtk.Box:
        sec = self._section("Provider")
        entries = [(p.name, p.instance_id) for p in self.backend.music_providers()]
        sec.append(self._check_list(entries, set(self._state.providers), self._toggle_provider))
        return sec

    def _toggle_provider(self, value: object, on: bool) -> None:
        current = [p for p in self._state.providers if p != value]
        self._apply_filter(providers=tuple(current + [value]) if on else tuple(current))

    def _album_type_section(self) -> Gtk.Box:
        sec = self._section("Album type")
        entries = [(t.value.upper() if t == AlbumType.EP else t.value.capitalize(), t) for t in ALBUM_TYPE_CHOICES]
        sec.append(self._check_list(entries, set(self._state.album_types), self._toggle_album_type))
        return sec

    def _toggle_album_type(self, value: object, on: bool) -> None:
        current = [t for t in self._state.album_types if t != value]
        self._apply_filter(album_types=tuple(current + [value]) if on else tuple(current))

    def _played_row(self) -> Gtk.Box:
        row = Gtk.Box(spacing=12)
        row.append(label("Played only", hexpand=True))
        switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=self._state.played_only)
        switch.connect("notify::active", lambda s, _p: self._apply_filter(played_only=s.get_active()))
        row.append(switch)
        return row

    def _genre_section(self) -> Gtk.Box:
        sec = self._section("Genre")
        search = Gtk.SearchEntry(placeholder_text="Find a genre")
        search.connect("search-changed", lambda e: self._filter_genre_rows(e.get_text()))
        sec.append(search)
        self._genre_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._genre_list.add_css_class("m3-list")
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=160,
                                      max_content_height=220, propagate_natural_height=True,
                                      child=self._genre_list)
        sec.append(scroller)
        if self._genres is not None:
            self._fill_genres()
        return sec

    def _fill_genres(self) -> None:
        selected = set(self._state.genre_ids)
        self._genre_rows = []
        for gid, name in self._genres or []:
            check = Gtk.CheckButton(label=name, active=gid in selected)
            check.connect("toggled", lambda b, g=gid: self._toggle_genre(g, b.get_active()))
            self._genre_list.append(check)
            self._genre_rows.append((name.casefold(), check))

    def _filter_genre_rows(self, query: str) -> None:
        needle = query.strip().casefold()
        for name, check in self._genre_rows:
            check.get_parent().set_visible(needle in name)

    def _toggle_genre(self, gid: int, on: bool) -> None:
        current = [g for g in self._state.genre_ids if g != gid]
        self._apply_filter(genre_ids=tuple(current + [gid]) if on else tuple(current))

    def _apply_filter(self, **changes) -> None:
        self._emit(replace(self._state, **changes))
        count = self._filter_count()
        self.filter_btn.set_label(f"Filters · {count}" if count else "Filters")

    def _on_filters_shown(self, _pop) -> None:
        if self._kind is None or self._genres is not None or self._genres_loading:
            return
        if "genre" not in self._filters_for(self._kind) or not self.backend.connected:
            return
        self._genres_loading = True
        self.backend.run(self._load_genres(self._kind))

    async def _load_genres(self, kind: LibraryKind) -> None:
        try:
            base = ListingState(sort_field=SortField.SORT_NAME, sort_direction=SortDirection.ASC)
            items = await self.backend.client.music.get_library_genres(
                limit=GENRE_LIMIT, **listing_kwargs(base, "genres", self.backend.schema))
        except Exception as err:  # noqa: BLE001
            log.warning("loading genres failed: %s", err)
            root = self.get_root()
            if hasattr(root, "toast"):
                root.toast(f"Could not load genres: {err}")
            return
        finally:
            self._genres_loading = False
        self._genres = [(int(g.item_id), g.name) for g in items]
        if kind == self._kind and "genre" in self._filters_for(kind):
            self._rebuild_filters()
            self.filter_pop.popup()
