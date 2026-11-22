"""Genre page: compact hero, a collapsible albums row, then a virtualized track list."""

from __future__ import annotations

import logging

from gi.repository import Adw, Gtk
from music_assistant_models.enums import QueueOption, SortDirection, SortField

from .common import ArtImage, label, retry_page, status_page
from .expressive import Sym
from .home import Carousel
from .library import library_sort
from .media_list import MediaList

log = logging.getLogger(__name__)

ALBUMS_SHOWN = 50
ALBUM_TILE = 120


class GenrePage(Adw.NavigationPage):
    """Everything in the library mapped to one genre; sections appear only when they have content.

    The albums row folds away so a short window can give the track list the whole page.
    """

    def __init__(self, backend, genre, open_item) -> None:
        super().__init__(title=genre.name)
        self.backend, self.item, self.open_item = backend, genre, open_item
        self._albums_done = False
        self._albums_empty = True
        self._tracks_empty = False

        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar(show_title=False)
        hb.add_css_class("flat-header")
        tv.add_top_bar(hb)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.append(Adw.Clamp(maximum_size=980, margin_start=20, margin_end=20, child=self._hero(genre)))

        self.albums = Carousel(backend, "", open_item, ALBUM_TILE)
        self.albums_toggle = Gtk.Button(halign=Gtk.Align.START, margin_start=20, visible=False)
        self.albums_toggle.add_css_class("flat")
        self.albums_toggle.connect("clicked", lambda *_: self._toggle_albums())
        self.revealer = Gtk.Revealer(reveal_child=True, child=self.albums)
        self.albums_toggle.set_label("Albums  ▾")
        body.append(Adw.Clamp(maximum_size=980, child=self.albums_toggle))
        body.append(Adw.Clamp(maximum_size=980, margin_start=20, margin_end=20, child=self.revealer))

        self.tracks = MediaList(backend, self._fetch_tracks,
                                lambda t: backend.play_media(t, option=QueueOption.PLAY), on_empty=self._on_tracks)
        self.tracks_stack = Gtk.Stack(vexpand=True)
        self.tracks_stack.set_size_request(-1, 240)
        self.tracks_stack.add_named(self.tracks, "list")
        self.tracks_stack.add_named(retry_page("Couldn’t load tracks", self._retry_tracks), "error")
        body.append(self.tracks_stack)
        self.empty = status_page("view-grid-symbolic", "Nothing in this genre yet")
        self.empty.set_visible(False)
        self.empty.set_vexpand(True)
        body.append(self.empty)
        tv.set_content(body)
        self.set_child(tv)
        backend.run(self._load_albums())

    def _hero(self, genre) -> Gtk.Widget:
        head = Gtk.Box(spacing=20)
        art = ArtImage(self.backend, 120, fetch_size=240)
        art.set_item(genre)
        head.append(art)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER, hexpand=True)
        info.append(label("GENRE", "overline", "primary-text"))
        info.append(label(genre.name, "display-s"))
        play = Gtk.Button(margin_top=8, halign=Gtk.Align.START)
        for c in ("m3", "filled", "large"):
            play.add_css_class(c)
        pb = Gtk.Box(spacing=8)
        pb.append(Sym("play_arrow", 24, fill=True))
        pb.append(Gtk.Label(label="Play"))
        play.set_child(pb)
        play.connect("clicked", lambda *_: self._play())
        info.append(play)
        head.append(info)
        return head

    def _toast(self, text: str) -> None:
        root = self.get_root()
        if hasattr(root, "toast"):
            root.toast(text)

    def _toggle_albums(self) -> None:
        show = not self.revealer.get_reveal_child()
        self.revealer.set_reveal_child(show)
        self.albums_toggle.set_label("Albums  ▾" if show else "Albums  ▸")

    def _play(self) -> None:
        b = self.backend
        qid = b.active_queue.queue_id if b.active_queue else (b.active_player.player_id if b.active_player else None)
        if not qid or not b.client:
            self._toast("No player selected")
            return

        async def go() -> None:
            try:
                await b.client.player_queues.play_media(qid, self.item)
            except Exception as err:  # noqa: BLE001 - the server may refuse to play a genre
                log.warning("playing genre %s failed: %s", self.item.name, err)
                self._toast(f"Couldn’t play {self.item.name}")

        b.run(go())

    async def _load_albums(self) -> None:
        music = self.backend.client.music
        sort = library_sort(self.backend, SortField.SORT_NAME, SortDirection.ASC, "sort_name")
        try:
            albums = await music.get_genre_albums(self.item.item_id, limit=ALBUMS_SHOWN, **sort)
        except Exception as err:  # noqa: BLE001 - the tracks may still load
            log.warning("genre albums failed: %s", err)
            albums = []
        self.albums.set_items(albums)
        self.albums_toggle.set_visible(bool(albums))
        self._albums_done, self._albums_empty = True, not albums
        self._update_empty()

    async def _fetch_tracks(self, offset: int, limit: int) -> list:
        try:
            return await self.backend.client.music.get_genre_tracks(self.item.item_id, limit=limit, offset=offset)
        except Exception:
            if offset == 0:
                self.tracks_stack.set_visible_child_name("error")
            raise

    def _retry_tracks(self) -> None:
        self.tracks.reset()
        self.tracks_stack.set_visible_child_name("list")

    def _on_tracks(self, empty: bool) -> None:
        self._tracks_empty = empty
        self.tracks_stack.set_visible(not empty)
        self._update_empty()

    def _update_empty(self) -> None:
        self.empty.set_visible(self._albums_done and self._albums_empty and self._tracks_empty)
