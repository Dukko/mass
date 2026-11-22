"""Home: greeting, now-playing hero card, carousels."""

from __future__ import annotations

import asyncio
import logging
import time

from gi.repository import GLib, Gtk
from music_assistant_models.enums import MediaType, SortDirection, SortField

from ..backend import RECOMMENDATION_ITEMS_SCHEMA
from .common import ArtImage, label
from .expressive import Sym, WavySeekbar
from .library import library_sort, subtitle_for
from .nowplaying import art_url, is_playing, schedule_refresh, track_info

log = logging.getLogger(__name__)

OPENABLE = (MediaType.ALBUM, MediaType.ARTIST, MediaType.PLAYLIST, MediaType.PODCAST, MediaType.GENRE)


def greeting() -> str:
    h = time.localtime().tm_hour
    return "Good morning" if 5 <= h < 12 else "Good afternoon" if h < 18 else "Good evening"


class Carousel(Gtk.Box):
    """Titled horizontal row of shaped cards."""

    def __init__(self, backend, title: str, open_item, size: int = 160) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.backend, self.open_item, self.size = backend, open_item, size
        self.append(label(title, "section-title", margin_start=8))
        sw = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER)
        self.row = Gtk.Box(spacing=4)
        sw.set_child(self.row)
        self.append(sw)
        self.set_visible(False)

    def set_items(self, items) -> None:
        while c := self.row.get_first_child():
            self.row.remove(c)
        seen = set()
        for it in items:
            if it.uri in seen:
                continue
            seen.add(it.uri)
            btn = Gtk.Button()
            btn.add_css_class("flat")
            btn.add_css_class("tile")
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            art = ArtImage(self.backend, self.size)
            art.set_item(it)
            col.append(art)
            xa = 0.5 if it.media_type in (MediaType.ARTIST, MediaType.RADIO) else 0
            col.append(label(it.name, "title-s", xalign=xa, max_width_chars=1, margin_top=8))
            col.append(label(subtitle_for(it), "label-m", "on-variant", xalign=xa, max_width_chars=1))
            col.set_size_request(self.size, -1)
            btn.set_child(col)
            if it.media_type in OPENABLE:
                btn.connect("clicked", lambda _b, i=it: self.open_item(i))
            else:
                btn.connect("clicked", lambda _b, i=it: self.backend.play_media(i))
            self.row.append(btn)
        self.set_visible(bool(seen))


class HeroCard(Gtk.Button):
    """Big primary-container card for what's playing now."""

    def __init__(self, backend, open_np) -> None:
        super().__init__()
        self.backend = backend
        self.add_css_class("flat")
        self.add_css_class("hero-card")
        self.connect("clicked", lambda *_: open_np())
        box = Gtk.Box(spacing=28)
        self.art = ArtImage(backend, 168, shape="cookie9", keep_stale=True)
        box.append(self.art)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER, hexpand=True)
        self.where = label("", "overline")
        self.title = label("", "headline-l", max_width_chars=1, hexpand=True)
        self.artist = label("", "title-m", "on-variant", max_width_chars=1)
        self.wave = WavySeekbar(height=24, amplitude=3, wavelength=28, line_width=4, interactive=False, pad=2)
        self.wave.set_margin_top(10)
        for w in (self.where, self.title, self.artist, self.wave):
            col.append(w)
        box.append(col)
        self.play = Gtk.Button(valign=Gtk.Align.CENTER, tooltip_text="Play / pause")
        self.play.add_css_class("play-hero")
        self.play.sym = Sym("play_arrow", 40, fill=True)
        self.play.set_child(self.play.sym)
        self.play.connect("clicked", lambda *_: backend.play_pause())
        box.append(self.play)
        self.set_child(box)
        for sig in ("player-updated", "queue-updated", "active-player-changed", "status-changed"):
            backend.connect(sig, lambda *_: schedule_refresh(self))
        self._tick_source = GLib.timeout_add(500, self._tick)
        self.connect("destroy", lambda *_: GLib.source_remove(self._tick_source))
        self.refresh()

    def refresh(self) -> None:
        b = self.backend
        title, artist, _album, _dur, src = track_info(b)
        p = b.active_player
        self.where.set_label(f"NOW PLAYING · {p.name.upper()}" if p else "NOW PLAYING")
        self.title.set_label(title)
        self.artist.set_label(artist)
        self.art.set_url(art_url(b, src, 336))
        playing = is_playing(b)
        self.play.sym.set_symbol("pause" if playing else "play_arrow")
        (self.play.add_css_class if playing else self.play.remove_css_class)("playing")
        self.art.set_spinning(0.25 if playing else 0.0)
        self._tick()

    def _tick(self) -> bool:
        if self.get_mapped():
            _t, _a, _al, dur, _s = track_info(self.backend)
            self.wave.set_state(self.backend.elapsed() / dur if dur else 0, is_playing(self.backend))
        return True


class HomeView(Gtk.ScrolledWindow):
    def __init__(self, backend, open_item, open_np) -> None:
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.backend, self.open_item = backend, open_item
        self._recs_gen = 0
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=28, margin_start=20, margin_end=20,
                      margin_top=56, margin_bottom=120)
        self.set_child(col)
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=8)
        self.hello = label(greeting(), "display-l", ellipsize=False)
        self.server = label("", "title-m", "on-variant")
        head.append(self.hello)
        head.append(self.server)
        col.append(head)
        self.hero = HeroCard(backend, open_np)
        col.append(self.hero)
        self.recent = Carousel(backend, "Jump back in", open_item)
        self.added = Carousel(backend, "Recently added", open_item)
        self.playlists = Carousel(backend, "Your playlists", open_item, 150)
        self.radio = Carousel(backend, "Radio", open_item, 120)
        for c in (self.recent, self.added, self.playlists, self.radio):
            col.append(c)
        self.recs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=28)
        self.recs.set_visible(False)
        col.append(self.recs)
        backend.connect("status-changed", self._on_status)
        self._loaded = False
        self.connect("map", lambda *_: self._loaded or self.load())

    def _on_status(self, _b, status, detail) -> None:
        if status == "connected":
            info = self.backend.client.server_info
            self.server.set_label(f"{getattr(info, 'name', None) or 'Music Assistant'} · v{info.server_version}")
            self._loaded = False
            if self.get_mapped():
                self.load()

    def load(self) -> None:
        if not self.backend.connected:
            return
        self._loaded = True
        self.hello.set_label(greeting())
        m = self.backend.client.music
        b = self.backend

        async def recent():
            items = await m.recently_played(limit=20, media_types=[MediaType.ALBUM, MediaType.PLAYLIST,
                                                                    MediaType.ARTIST, MediaType.RADIO])
            self.recent.set_items(items)
            return {it.uri for it in items}

        async def added():
            self.added.set_items(await m.get_library_albums(
                limit=20, **library_sort(b, SortField.TIMESTAMP_ADDED, SortDirection.DESC, "timestamp_added_desc")))

        async def playlists():
            self.playlists.set_items(await m.get_library_playlists(
                limit=20, **library_sort(b, SortField.LAST_PLAYED, SortDirection.DESC, "last_played_desc")))

        async def radio():
            self.radio.set_items(await m.get_library_radios(
                limit=20, **library_sort(b, SortField.SORT_NAME, SortDirection.ASC, "sort_name")))

        self._recs_gen += 1
        gen = self._recs_gen

        async def recent_then_recs():
            shown: set[str] = set()
            try:
                shown = await recent()
            finally:
                await self._load_recs(shown, gen)

        for coro in (recent_then_recs(), added(), playlists(), radio()):
            b.run(coro)

    async def _row_items(self, row) -> list:
        items = list(row.items)
        if not items and self.backend.supports(RECOMMENDATION_ITEMS_SCHEMA):
            items = await self.backend.client.music.recommendation_items(row.provider, row.item_id)
        return [it for it in items if it.media_type != MediaType.FOLDER]

    async def _load_recs(self, shown: set[str], gen: int) -> None:
        try:
            rows = [r for r in await self.backend.client.music.recommendations() if r.enabled_by_default]

            async def fetch(row):
                try:
                    return await self._row_items(row)
                except Exception as err:
                    log.debug("recommendation row %s failed: %s", row.name, err)
                    return []

            results = await asyncio.gather(*(fetch(r) for r in rows))
            if gen != self._recs_gen:
                return
            while c := self.recs.get_first_child():
                self.recs.remove(c)
            for row, items in zip(rows, results, strict=True):
                if not items or all(it.uri in shown for it in items):
                    continue
                car = Carousel(self.backend, row.name, self.open_item)
                car.set_items(items)
                self.recs.append(car)
        except Exception as err:
            log.debug("recommendations unavailable: %s", err)
            return
        self.recs.set_visible(self.recs.get_first_child() is not None)
