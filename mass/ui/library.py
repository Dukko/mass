"""Library browsing: lazy grids and detail pages (M3 Expressive)."""

from __future__ import annotations

import asyncio
import logging
import time

from gi.repository import Adw, Gdk, Gio, Gtk
from music_assistant_models.enums import MediaType, QueueOption, SortDirection, SortField

from .common import (
    ArtImage,
    ItemObject,
    artists_str,
    attach_media_actions,
    fmt_time,
    label,
    m3_list,
    media_menu_model,
    media_row,
)
from .expressive import Sym, sym_button
from .motion import pop_in

PAGE = 100
RETRY_COOLDOWN = 5.0
log = logging.getLogger(__name__)

# artist page album groups, in display order
ALBUM_GROUPS = (("Albums", {"album", "unknown", "soundtrack", "live"}), ("Singles & EPs", {"single", "ep"}),
                ("Compilations", {"compilation"}))


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

    def __init__(self, backend, fetch, on_open, card_size: int = 168, center_text: bool = False) -> None:
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.backend, self.fetch, self.on_open = backend, fetch, on_open
        self.card_size = card_size
        self.center_text = center_text
        self.store = Gio.ListStore(item_type=ItemObject)
        self._offset = 0
        self._exhausted = False
        self._loading = False
        self._loaded_once = False
        self._generation = 0
        self._retry_after = 0.0

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
        self.connect("map", lambda *_: self._loaded_once or self.load_more())

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
        attach_media_actions(box, self.backend, lambda: box.current)
        li.set_child(box)

    def _bind(self, _f, li) -> None:
        box = li.get_child()
        item = li.get_item().item
        art, title, sub = box.parts
        box.current = item
        art.set_item(item)
        title.set_label(item.name)
        sub.set_label(subtitle_for(item))

    def reset(self) -> None:
        self._generation += 1
        self.store.remove_all()
        self._offset, self._exhausted, self._loaded_once = 0, False, False
        self._loading = False
        self._retry_after = 0.0
        if self.get_mapped():
            self.load_more()

    def _on_scroll(self, adj) -> None:
        if adj.get_value() + adj.get_page_size() > adj.get_upper() - 800:
            self.load_more()

    def load_more(self) -> None:
        if self._loading or self._exhausted or not self.backend.connected:
            return
        if time.monotonic() < self._retry_after:
            return
        self._loading = True
        self._loaded_once = True
        generation = self._generation

        async def go():
            try:
                items = await self.fetch(self._offset, PAGE)
            except Exception:
                if generation == self._generation:
                    self._loading = False
                    self._retry_after = time.monotonic() + RETRY_COOLDOWN
                raise
            if generation != self._generation:
                return
            self._loading = False
            self._offset += len(items)
            self._exhausted = len(items) < PAGE
            self.store.splice(self.store.get_n_items(), 0, [ItemObject(i) for i in items])

        self.backend.run(go())


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
        clamp = Adw.Clamp(maximum_size=980, margin_bottom=120, margin_start=20, margin_end=20)
        self.col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        clamp.set_child(self.col)
        scroller.set_child(clamp)
        tv.set_content(scroller)
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
        more = Gtk.MenuButton(menu_model=media_menu_model(), valign=Gtk.Align.CENTER)
        more.set_child(Sym("more_vert", 24))
        more.add_css_class("flat")
        more.add_css_class("circular")
        btns.append(more)
        info.append(btns)
        head.append(info)
        attach_media_actions(head, backend, lambda: self.item)
        self.col.append(head)

        self.spinner = Adw.Spinner(height_request=40, margin_top=24)
        self.col.append(self.spinner)
        backend.run(self._load())

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
                self._add_tracks(await music.get_playlist_tracks(it.item_id, it.provider))
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

    def _add_tracks(self, tracks, numbered: bool = False, parent: Gtk.Box | None = None) -> None:
        lb = m3_list()
        for t in tracks:
            lead = None
            if numbered:
                lead = label(str(getattr(t, "track_number", "") or ""), "title-m", "on-variant", "numeric",
                             xalign=0.5, width_chars=2, ellipsize=False)
            row = media_row(self.backend, t, artists_str(t), art=not numbered, leading=lead,
                            trailing_text=fmt_time(t.duration) if t.duration else "", play_button=False,
                            on_activate=self._play_from)
            lb.append(row)
        (parent or self.col).append(pop_in(lb, dy=20, scale=0.98, preset="expressive"))

    def _play_from(self, track) -> None:
        if self.item.media_type in (MediaType.ALBUM, MediaType.PLAYLIST):
            self.backend.play_media(self.item, option=QueueOption.REPLACE, start_item=track.item_id)
        else:
            self.backend.play_media(track, option=QueueOption.PLAY)


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
            btn.connect("toggled", lambda b: b.get_active() and self.stack.set_visible_child_name(name))
            self.seg.append(btn)

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

    def set_narrow(self, narrow: bool) -> None:
        self.head.set_orientation(Gtk.Orientation.VERTICAL if narrow else Gtk.Orientation.HORIZONTAL)
        self.seg.set_halign(Gtk.Align.START if narrow else Gtk.Align.FILL)

    def _on_status(self, _b, status, _d) -> None:
        if status == "connected":
            for g in self.grids:
                g.reset()
