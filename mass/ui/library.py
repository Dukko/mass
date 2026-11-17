"""Library browsing: lazy grids and detail pages (M3 Expressive)."""

from __future__ import annotations

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

PAGE = 100


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
        return "Artist"
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
        self.store.remove_all()
        self._offset, self._exhausted, self._loaded_once = 0, False, False
        if self.get_mapped():
            self.load_more()

    def _on_scroll(self, adj) -> None:
        if adj.get_value() + adj.get_page_size() > adj.get_upper() - 800:
            self.load_more()

    def load_more(self) -> None:
        if self._loading or self._exhausted or not self.backend.connected:
            return
        self._loading = True
        self._loaded_once = True

        async def go():
            try:
                items = await self.fetch(self._offset, PAGE)
            except Exception:
                self._exhausted = True  # don't hammer the server on every scroll event
                raise
            finally:
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
        art = ArtImage(backend, 240, fetch_size=480)
        art.set_item(item)
        head.append(art)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.END, hexpand=True)
        info.append(label(str(item.media_type.value).upper(), "overline", "primary-text"))
        title = label(item.name, "display-s", ellipsize=False, wrap=True, lines=3)
        title.set_wrap_mode(2)
        info.append(title)
        info.append(label(subtitle_for(item), "title-m", "on-variant"))
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
        attach_media_actions(head, backend, lambda: item)
        self.col.append(head)

        self.spinner = Adw.Spinner(height_request=40, margin_top=24)
        self.col.append(self.spinner)
        backend.run(self._load())

    def _shuffle(self) -> None:
        b = self.backend
        if b.client and (target := b.active_queue or b.active_player):
            qid = getattr(target, "queue_id", None) or target.player_id
            b.run(b.client.player_queues.play_media(qid, self.item, option=QueueOption.REPLACE, shuffle=True))

    async def _load(self) -> None:
        music = self.backend.client.music
        it = self.item
        try:
            if it.media_type == MediaType.ALBUM:
                self._add_tracks(await music.get_album_tracks(it.item_id, it.provider), numbered=True)
            elif it.media_type == MediaType.PLAYLIST:
                self._add_tracks(await music.get_playlist_tracks(it.item_id, it.provider))
            elif it.media_type == MediaType.ARTIST:
                albums = await music.get_artist_albums(it.item_id, it.provider)
                tracks = await music.get_artist_tracks(it.item_id, it.provider)
                if tracks:
                    self._section("Popular")
                    self._add_tracks(tracks[:10])
                if albums:
                    self._section("Albums")
                    flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=8,
                                       min_children_per_line=2, homogeneous=True, activate_on_single_click=True)
                    for a in albums:
                        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                        card.add_css_class("tile")
                        art = ArtImage(self.backend, 150)
                        art.set_item(a)
                        card.append(art)
                        card.append(label(a.name, "title-s", max_width_chars=1, margin_top=6))
                        card.append(label(str(getattr(a, "year", "") or ""), "label-m", "on-variant"))
                        card.album = a
                        flow.append(card)
                    flow.connect("child-activated", lambda _f, ch: self.open_item(ch.get_child().album))
                    self.col.append(flow)
        finally:
            self.spinner.set_visible(False)

    def _section(self, title: str) -> None:
        self.col.append(label(title, "section-title", margin_top=12))

    def _add_tracks(self, tracks, numbered: bool = False) -> None:
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
        self.col.append(lb)

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
