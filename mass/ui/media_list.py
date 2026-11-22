"""Virtualized, lazily paged list of media rows (M3 Expressive)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from gi.repository import Adw, Gio, Gtk

from ..listing import matches_query
from .common import ArtImage, ItemObject, artists_str, attach_media_actions, fmt_time, label, media_menu_button
from .paging import PAGE, Pager


def default_subtitle(item: Any) -> str:
    album = getattr(getattr(item, "album", None), "name", "") or ""
    return " · ".join(p for p in (artists_str(item), album) if p)


class MediaList(Gtk.ScrolledWindow):
    """Rows recycled by a Gtk.ListView, filled page by page from `fetch(offset, limit)`.

    It scrolls itself, so it must not sit inside another scroller (the list would then be laid out in
    full and nothing would be recycled). `set_query` filters the loaded items on the client;
    a caller that filters on the server changes its own `fetch` and calls `reset()` instead.
    `menu` may be a predicate deciding per item whether the row shows its menu button.
    """

    def __init__(self, backend, fetch, on_activate: Callable[[Any], None],
                 subtitle: Callable[[Any], str] = default_subtitle,
                 menu: bool | Callable[[Any], bool] = True,
                 on_empty: Callable[[bool], None] | None = None, page: int = PAGE) -> None:
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.backend, self.on_activate, self.subtitle, self.menu = backend, on_activate, subtitle, menu
        self.on_empty = on_empty
        self.query = ""
        self.store = Gio.ListStore(item_type=ItemObject)
        self._filter = Gtk.CustomFilter.new(self._matches)
        self.model = Gtk.FilterListModel(model=self.store, filter=self._filter)
        self._pager = Pager(fetch, self._on_items, backend.run, page)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup)
        factory.connect("bind", self._bind)
        self.view = Gtk.ListView(model=Gtk.NoSelection(model=self.model), factory=factory,
                                 single_click_activate=True)
        self.view.add_css_class("m3-list")
        self.view.add_css_class("full")
        self.view.connect("activate", lambda _v, pos: self.on_activate(self.model.get_item(pos).item))
        self.set_child(Adw.ClampScrollable(maximum_size=980, child=self.view))
        self.get_vadjustment().connect("value-changed", self._on_scroll)
        self.connect("map", lambda *_: self._pager.loaded_once or self.load_more())

    def _matches(self, obj: ItemObject) -> bool:
        return matches_query(obj.item, self.query)

    def _setup(self, _f, li) -> None:
        box = Gtk.Box(spacing=14)
        box.add_css_class("qrow")
        art = ArtImage(self.backend, 48)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True, spacing=2)
        title = label("", "title-m")
        sub = label("", "body-m", "on-variant")
        text.append(title)
        text.append(sub)
        duration = label("", "label-l", "on-variant", "numeric", ellipsize=False)
        box.append(art)
        box.append(text)
        box.append(duration)
        box.menu_button = media_menu_button() if self.menu else None
        if box.menu_button is not None:
            box.append(box.menu_button)
        box.parts = (art, title, sub, duration)
        box.current = None
        box.refresh_actions = attach_media_actions(box, self.backend, lambda: box.current)
        li.set_child(box)

    def _bind(self, _f, li) -> None:
        box = li.get_child()
        item = li.get_item().item
        art, title, sub, duration = box.parts
        box.current = item
        box.refresh_actions()
        if box.menu_button is not None and callable(self.menu):
            box.menu_button.set_visible(bool(self.menu(item)))
        art.set_item(item)
        title.set_label(getattr(item, "name", "") or "")
        sub.set_label(self.subtitle(item))
        seconds = getattr(item, "duration", None)
        duration.set_label(fmt_time(seconds) if seconds else "")

    def set_query(self, query: str) -> None:
        """Show only loaded items matching `query`."""
        self.query = query
        self._filter.changed(Gtk.FilterChange.DIFFERENT)
        if self.on_empty is not None:
            self.on_empty(self.model.get_n_items() == 0)

    def reset(self) -> None:
        self.store.remove_all()
        self._pager.reset()
        if self.get_mapped():
            self.load_more()

    def _on_scroll(self, adj: Gtk.Adjustment) -> None:
        if adj.get_value() + adj.get_page_size() > adj.get_upper() - 800:
            self.load_more()

    def load_more(self) -> None:
        if self.backend.connected:
            self._pager.load_more()

    def _on_items(self, items: list, first_page: bool) -> None:
        self.store.splice(self.store.get_n_items(), 0, [ItemObject(i) for i in items])
        if first_page and self.on_empty is not None:
            self.on_empty(self.model.get_n_items() == 0)
