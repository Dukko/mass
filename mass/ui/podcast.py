"""Podcast page: hero with favorite heart, then a virtualized episode list."""

from __future__ import annotations

import logging

from gi.repository import Adw, Gtk
from music_assistant_models.enums import QueueOption
from music_assistant_models.media_items import ItemMapping

from ..backend import EPISODE_PAGING_SCHEMA
from .common import ArtImage, fmt_time, label, retry_page, status_page
from .expressive import Sym, sym_button
from .media_list import MediaList
from .playlist_tracks import WholeList

log = logging.getLogger(__name__)


def episode_subtitle(ep) -> str:
    """Played state, resume point or episode number, whichever the server knows."""
    if getattr(ep, "fully_played", None):
        return "Played"
    if resume := getattr(ep, "resume_position_ms", None):
        return f"Resume at {fmt_time(resume / 1000)}"
    number = getattr(ep, "episode_number", None)
    season = getattr(ep, "season", None)
    if number is not None and season is not None:
        return f"Season {season} · Episode {number}"
    return f"Episode {number}" if number is not None else ""


def latest_episode(episodes: list):
    """The newest episode: the highest publisher number when every episode has one, else the first.

    Episodes carry no release date, and the server lists them in the provider's feed order, which
    is newest first for the feeds Music Assistant reads.
    """
    if not episodes:
        return None
    if all(getattr(e, "episode_number", None) is not None for e in episodes):
        return max(episodes, key=lambda e: (getattr(e, "season", None) or 0, e.episode_number))
    return episodes[0]


def _set_class(w: Gtk.Widget, cls: str, on: bool) -> None:
    (w.add_css_class if on else w.remove_css_class)(cls)


class PodcastPage(Adw.NavigationPage):
    """Podcast hero plus its episodes; activating an episode plays it."""

    def __init__(self, backend, podcast, open_item) -> None:
        super().__init__(title=podcast.name)
        self.backend, self.item, self.open_item = backend, podcast, open_item
        self._fav_on = self._fav_busy = self._fav_loaded = False
        self._whole = WholeList(self._load_all)

        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar(show_title=False)
        hb.add_css_class("flat-header")
        tv.add_top_bar(hb)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        body.append(Adw.Clamp(maximum_size=980, margin_start=20, margin_end=20, child=self._hero(podcast)))
        self.stack = Gtk.Stack(vexpand=True)
        self.episodes = MediaList(backend, self._fetch, self._play, subtitle=episode_subtitle,
                                  on_empty=self._on_empty)
        self.stack.add_named(self.episodes, "list")
        self.stack.add_named(status_page("microphone-sensitivity-high-symbolic", "No episodes found"), "empty")
        self.stack.add_named(retry_page("Couldn’t load episodes", self._retry), "error")
        body.append(self.stack)
        tv.set_content(body)
        self.set_child(tv)
        backend.run(self._load())

    def _hero(self, podcast) -> Gtk.Widget:
        head = Gtk.Box(spacing=28)
        art = ArtImage(self.backend, 200, fetch_size=400)
        art.set_item(podcast)
        head.append(art)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.END, hexpand=True)
        info.append(label("PODCAST", "overline", "primary-text"))
        title = label(podcast.name, "display-s", ellipsize=False, wrap=True, lines=3)
        title.set_wrap_mode(2)
        info.append(title)
        self.publisher = label(getattr(podcast, "publisher", "") or "", "title-m", "on-variant")
        info.append(self.publisher)
        btns = Gtk.Box(spacing=8, margin_top=16)
        self.play_btn = Gtk.Button(sensitive=False)
        for c in ("m3", "filled", "large"):
            self.play_btn.add_css_class(c)
        pb = Gtk.Box(spacing=8)
        pb.append(Sym("play_arrow", 24, fill=True))
        pb.append(Gtk.Label(label="Play latest"))
        self.play_btn.set_child(pb)
        self.play_btn.connect("clicked", lambda *_: self._play_latest())
        btns.append(self.play_btn)
        self.fav = sym_button("favorite", "Add to favorites", ("m3", "icon", "large", "toggle"),
                              cb=self._toggle_favorite)
        self.fav.set_visible(False)
        btns.append(self.fav)
        info.append(btns)
        head.append(info)
        return head

    def _toast(self, text: str) -> None:
        root = self.get_root()
        if hasattr(root, "toast"):
            root.toast(text)

    async def _load(self) -> None:
        item = self.item
        if isinstance(item, ItemMapping):
            try:
                item = await self.backend.client.music.get_podcast(item.item_id, item.provider)
            except Exception as err:  # noqa: BLE001 - the mapping already has what the list needs
                log.debug("could not load podcast %s: %s", item.uri, err)
            else:
                self.item = item
                self.publisher.set_label(item.publisher or "")
        on, _lib = await self.backend.favorite_state(self.item)
        if self._fav_loaded:  # the user toggled before the state arrived
            return
        self._fav_loaded = True
        self._fav_on = on
        self._show_favorite(on)
        self.fav.set_visible(True)

    async def _load_all(self) -> list:
        return await self.backend.client.music.get_podcast_episodes(self.item.item_id, self.item.provider)

    async def _fetch(self, offset: int, limit: int) -> list:
        try:
            if not self.backend.supports(EPISODE_PAGING_SCHEMA):
                return await self._whole(offset, limit)
            return await self.backend.client.music.get_podcast_episodes(
                self.item.item_id, self.item.provider, limit=limit, offset=offset)
        except Exception:
            if offset == 0:
                self.play_btn.set_sensitive(False)
                self.stack.set_visible_child_name("error")
            raise

    def _retry(self) -> None:
        self._whole = WholeList(self._load_all)
        self.episodes.reset()
        self.stack.set_visible_child_name("list")

    def _on_empty(self, empty: bool) -> None:
        self.stack.set_visible_child_name("empty" if empty else "list")
        self.play_btn.set_sensitive(not empty)

    def _play(self, episode) -> None:
        self.backend.play_media(episode, option=QueueOption.PLAY)

    def _play_latest(self) -> None:
        store = self.episodes.store
        latest = latest_episode([store.get_item(i).item for i in range(store.get_n_items())])
        if latest is not None:
            self._play(latest)

    def _show_favorite(self, on: bool) -> None:
        _set_class(self.fav, "on", on)
        self.fav.sym.set_fill(on)
        self.fav.set_tooltip_text("Remove from favorites" if on else "Add to favorites")

    def _toggle_favorite(self) -> None:
        if self._fav_busy:
            return
        want = not self._fav_on
        self._fav_busy = self._fav_loaded = True
        self.fav.set_sensitive(False)
        self._fav_on = want
        self._show_favorite(want)

        def finish(on: bool) -> None:
            self._fav_busy = False
            self.fav.set_sensitive(True)
            self._fav_on = on
            self._show_favorite(on)

        def done(_on: bool) -> None:
            finish(want)
            self._toast("Added to favorites" if want else "Removed from favorites")

        def fail(_err: Exception) -> None:
            finish(not want)
            self._toast("Couldn’t update favorites")

        self.backend.toggle_favorite(self.item, want, done, fail)
