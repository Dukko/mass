"""Now-playing page and the floating mini player (M3 Expressive)."""

from __future__ import annotations

import logging

from gi.repository import GLib, Gtk
from music_assistant_models.enums import PlaybackState, RepeatMode

from .common import ArtImage, artists_str, fmt_time, label
from .dj import DJ_SHAPE, DJCaption, DJChip, DJTranscript, dj_icon, dj_segment, host_name
from .expressive import Sym, WavySeekbar, sym_button
from .lyrics import LyricsView
from .np_options import NowPlayingOptions, audio_quality
from .motion import spring_stack
from .queue import QueuePage

log = logging.getLogger(__name__)

ART_PLAYING_SHAPE = "cookie12"
ART_PAUSED_SHAPE = "square"
SPIN = 0.12  # rad/s: a slow sway of the cookie edge while playing


def track_info(backend):
    """(title, artist, album, duration, art_source) for the active queue/player."""
    q = backend.active_queue
    p = backend.active_player
    if q and q.current_item:
        it = q.current_item
        if seg := dj_segment(it):
            return host_name(backend, seg["host_id"]), seg["name"], "AI DJ", it.duration, None
        mi = it.media_item
        album = getattr(mi, "album", None)
        return (getattr(mi, "name", None) or it.name, artists_str(mi), album.name if album else "",
                it.duration, it)
    if p and p.current_media:
        cm = p.current_media
        return cm.title or cm.uri, cm.artist or "", cm.album or "", cm.duration, cm.image_url
    return "Nothing playing", "", "", None, None


def is_playing(backend) -> bool:
    q, p = backend.active_queue, backend.active_player
    state = q.state if q else (p.playback_state if p else None)
    return state == PlaybackState.PLAYING


def schedule_refresh(widget) -> None:
    """Run widget.refresh() once on idle, however many events arrive meanwhile."""
    if getattr(widget, "_refresh_pending", False):
        return
    widget._refresh_pending = True

    def run():
        widget._refresh_pending = False
        widget.refresh()
        return False

    GLib.idle_add(run)


def art_url(backend, art_src, size: int) -> str | None:
    if art_src is None or isinstance(art_src, str):
        return art_src
    return backend.image_url(art_src, size)


def _set_class(w: Gtk.Widget, cls: str, on: bool) -> None:
    (w.add_css_class if on else w.remove_css_class)(cls)


CARD_MIN, PANEL_MIN = 440, 660
ART_MIN, ART_MAX = 220, 520

TABS = (("lyrics", "Lyrics"), ("queue", "Up next"), ("dj", "AI DJ"), ("about", "About"))


def current_media_item(backend):
    q = backend.active_queue
    return q.current_item.media_item if q and q.current_item else None


class TrackAbout(Gtk.ScrolledWindow):
    """Key/value facts about the current track (About tab)."""

    FIELDS = ("Title", "Artist", "Album", "Year", "Length", "Format", "Source", "Player")

    def __init__(self) -> None:
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        grid = Gtk.Grid(column_spacing=24, row_spacing=14, margin_start=28, margin_end=28, margin_top=20,
                        margin_bottom=20)
        self.set_child(grid)
        self.values: dict[str, Gtk.Label] = {}
        for i, name in enumerate(self.FIELDS):
            grid.attach(label(name, "label-l", "on-variant", ellipsize=False), 0, i, 1, 1)
            val = label("", "body-l", ellipsize=False, wrap=True, hexpand=True, selectable=True)
            val.set_wrap_mode(2)
            grid.attach(val, 1, i, 1, 1)
            self.values[name] = val

    def refresh(self, backend) -> None:
        title, artist, album, duration, _src = track_info(backend)
        mi = current_media_item(backend)
        alb = getattr(mi, "album", None)
        q, p = backend.active_queue, backend.active_player
        sd = getattr(q.current_item, "streamdetails", None) if q and q.current_item else None
        qual = audio_quality(backend)
        prov = getattr(sd, "provider", None) or getattr(mi, "provider", None) or ""
        facts = {
            "Title": title, "Artist": artist, "Album": album, "Year": str(getattr(alb, "year", "") or ""),
            "Length": fmt_time(duration) if duration else "", "Format": qual[1] if qual else "",
            "Source": str(prov).split("--")[0].replace("_", " ").title(), "Player": p.name if p else "",
        }
        for name, val in facts.items():
            lbl = self.values[name]
            lbl.set_label(val or "—")


class NowPlayingPage(Gtk.Box):
    """Option C: a player card (art, track, transport, volume) beside a tabbed panel
    (Lyrics · Up next · AI DJ · About) with the queue option chips along its foot."""

    def __init__(self, backend, theme, open_players, open_queue) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.backend, self.theme = backend, theme
        self.add_css_class("np-page")
        self._narrow = False
        self._fit_for = (0, 0)
        self._fav_uri: str | None = None
        self._fav_on = False
        self._fav_lib: str | None = None

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.append(scroller)
        self.layout = Gtk.Box(spacing=16, margin_top=52, margin_bottom=16, margin_start=16, margin_end=16)
        scroller.set_child(self.layout)

        # ============================================================ player card
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, width_request=440)
        card.add_css_class("np-card")
        self.card = card
        self.layout.append(card)

        self.seek = WavySeekbar(height=36, amplitude=4.0, wavelength=34, line_width=5)
        self.seek.connect("seek", self._on_seek)
        self.options = NowPlayingOptions(backend, self, self.seek.set_wavy)

        head = Gtk.Box(spacing=8)
        chip = Gtk.Button(halign=Gtk.Align.START, tooltip_text="Choose player")
        chip.add_css_class("player-chip")
        cb = Gtk.Box(spacing=8)
        cb.append(Sym("speaker", 20, fill=True))
        self.player_label = label("No player", max_width_chars=24)
        cb.append(self.player_label)
        cb.append(Sym("expand_more", 20))
        chip.set_child(cb)
        chip.connect("clicked", lambda *_: open_players(chip))
        self.chip = chip
        head.append(chip)
        head.append(Gtk.Box(hexpand=True))
        head.append(self.options.badge)
        card.append(head)
        card.append(Gtk.Box(vexpand=True))  # centre the rest below the header in tall windows

        self.art = ArtImage(backend, 392, shape=ART_PAUSED_SHAPE, fetch_size=0, keep_stale=True)
        self.art.set_halign(Gtk.Align.CENTER)
        self.art.set_margin_top(4)
        card.append(self.art)

        trow = Gtk.Box(spacing=12, margin_top=4)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        self.title = label("", "np-title", ellipsize=False, wrap=True, lines=2)
        self.title.set_wrap_mode(2)  # word-char
        self.title.set_ellipsize(3)
        self.artist = label("", "np-artist")
        self.album = label("", "np-album")
        for w in (self.artist, self.album):
            w.connect("activate-link", self._open_link)
        self._links: dict[str, object] = {}
        for w in (self.title, self.artist, self.album):
            titles.append(w)
        trow.append(titles)
        self.fav = sym_button("favorite", "Add to favorites", ("m3", "icon", "large", "toggle", "np-fav"),
                              cb=self._favorite)
        self.fav.set_valign(Gtk.Align.START)
        trow.append(self.fav)
        card.append(trow)

        self.caption = DJCaption()
        card.append(self.caption)

        seekbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        seekbox.append(self.seek)
        times = Gtk.Box(margin_start=8, margin_end=8)
        self.elapsed = label("", "label-l", "on-variant", "numeric", hexpand=True)
        self.duration = label("", "label-l", "on-variant", "numeric", xalign=1)
        times.append(self.elapsed)
        times.append(self.duration)
        seekbox.append(times)
        card.append(seekbox)

        # transport: shuffle · [prev | PLAY | next] · repeat
        transport = Gtk.CenterBox()
        self.shuffle = sym_button("shuffle", "Shuffle", ("m3", "icon", "toggle"),
                                  cb=lambda: backend.set_shuffle(
                                      not (backend.active_queue and backend.active_queue.shuffle_enabled)))
        group = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
        group.add_css_class("btn-group")
        self.prev = sym_button("skip_previous", "Previous", (), 28, True, backend.previous)
        self.play = sym_button("play_arrow", "Play / pause", ("play-hero",), 40, True, backend.play_pause)
        self.next = sym_button("skip_next", "Next", (), 28, True, backend.next)
        for w in (self.prev, self.play, self.next):
            group.append(w)
        self.repeat = sym_button("repeat", "Repeat", ("m3", "icon", "toggle"), cb=backend.cycle_repeat)
        transport.set_start_widget(self.shuffle)
        transport.set_center_widget(group)
        transport.set_end_widget(self.repeat)
        card.append(transport)

        vol = Gtk.Box(spacing=10)
        self.mute = sym_button("volume_up", "Mute", ("m3", "icon", "flat"),
                               cb=lambda: backend.set_mute(
                                   not (backend.active_player and backend.active_player.volume_muted)))
        self.volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.volume.set_hexpand(True)
        self.volume.set_draw_value(False)
        self.volume.add_css_class("m3-slider")
        self.volume.add_css_class("thin")
        self.volume.set_valign(Gtk.Align.CENTER)
        self._vol_handler = self.volume.connect("value-changed", self._on_volume)
        self._vol_timer = 0
        self.vol_value = label("", "label-l", "on-variant", "numeric", ellipsize=False, width_chars=3, xalign=1)
        for w in (self.mute, self.volume, self.vol_value):
            vol.append(w)
        card.append(vol)
        card.append(Gtk.Box(vexpand=True))

        # ============================================================ tabbed panel
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        panel.add_css_class("np-panel")
        panel.set_overflow(Gtk.Overflow.HIDDEN)
        self.panel = panel
        self.layout.append(panel)

        phead = Gtk.Box(spacing=4, margin_start=16, margin_end=12, margin_top=12)
        self.tabs: dict[str, Gtk.ToggleButton] = {}
        first = None
        for name, text in TABS:
            t = Gtk.ToggleButton(label=text, valign=Gtk.Align.CENTER)
            t.add_css_class("np-tab")
            if first:
                t.set_group(first)
            first = first or t
            t.connect("toggled", lambda b, n=name: b.get_active() and self._show_tab(n))
            self.tabs[name] = t
            phead.append(t)
        phead.append(Gtk.Box(hexpand=True))
        phead.append(self.options.more)
        panel.append(phead)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=200,
                               vexpand=True)
        self.lyrics = LyricsView(lambda t: backend.seek(t))
        self.lyrics.set_margin_start(28)
        self.lyrics.set_margin_end(20)
        self.stack.add_named(self.lyrics, "lyrics")
        self.upnext = QueuePage(backend, compact=True)
        self.stack.add_named(self.upnext, "queue")
        djs = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        djbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=16, margin_end=16,
                        margin_top=12, margin_bottom=16)
        self.transcript = DJTranscript(backend)
        djbox.append(self.transcript)
        djs.set_child(djbox)
        self.stack.add_named(djs, "dj")
        self.about = TrackAbout()
        self.stack.add_named(self.about, "about")
        self.tab_motion = spring_stack(self.stack, preset="fast", directional=[t[0] for t in TABS])
        panel.append(self.tab_motion)
        self._lyrics_key: str | None = None

        foot = Gtk.Box(spacing=8, margin_start=20, margin_end=16, margin_bottom=16, margin_top=8)
        for w in (self.options.autoplay, self.options.crossfade, self.options.sleep):
            foot.append(w)
        foot.append(Gtk.Box(hexpand=True))
        self.dj_chip = DJChip(backend)
        foot.append(self.dj_chip)
        panel.append(foot)

        self.seek.set_wavy(backend.config.wavy_progress)
        tab = backend.config.np_tab if backend.config.np_tab in self.tabs else "lyrics"
        self.tabs[tab].set_active(True)
        self.stack.set_visible_child_name(tab)

        for sig in ("player-updated", "queue-updated", "active-player-changed", "players-changed",
                    "status-changed", "ai-changed"):
            backend.connect(sig, self._on_change)
        GLib.timeout_add(250, self._tick)
        self.refresh()

    # ------------------------------------------------------------------ layout

    def set_narrow(self, narrow: bool) -> None:
        self._narrow = narrow
        self.layout.set_orientation(Gtk.Orientation.VERTICAL if narrow else Gtk.Orientation.HORIZONTAL)
        self.card.set_size_request(-1 if narrow else CARD_MIN, -1)
        self.panel.set_size_request(-1, 520 if narrow else -1)
        self._fit_for = (0, 0)
        self._set_art_size(280 if narrow else 392)

    def _set_art_size(self, size: int) -> None:
        if size != self.art.size:
            self.art.size = size
            self.art.queue_resize()

    def _fit(self) -> None:
        """Size the cover to the window: as tall as fits without scrolling, as wide as the panel allows."""
        width, height = self.get_width(), self.get_height()
        if self._narrow or height <= 0 or (width, height) == self._fit_for:
            return
        self._fit_for = (width, height)
        _min, nat, _b, _b2 = self.card.measure(Gtk.Orientation.VERTICAL, -1)
        overhead = nat - self.art.size
        by_height = height - 52 - 16 - overhead
        by_width = width - 3 * 16 - PANEL_MIN - 48
        target = int(max(ART_MIN, min(ART_MAX, by_height, by_width)))
        if abs(target - self.art.size) > 4:
            self._fit_art(target)

    def _fit_art(self, size: int) -> None:
        self._set_art_size(size)
        self.card.set_size_request(max(CARD_MIN, size + 48), -1)

    # ------------------------------------------------------------------ tabs

    def _show_tab(self, name: str) -> None:
        self.stack.set_visible_child_name(name)
        if self.backend.config.np_tab != name:
            self.backend.config.np_tab = name
            self.backend.config.save()
        if name == "lyrics":
            self._lyrics_key = None
        elif name == "dj":
            self.transcript.load()
        self.refresh()

    # ------------------------------------------------------------------ updates

    def _on_change(self, _b, *args) -> None:
        if (args and isinstance(args[0], str) and args[0] and not self.backend.is_relevant(args[0])
                and args[0] not in ("connected", "connecting", "disconnected", "auth-required")):
            return
        schedule_refresh(self)

    def _set_links(self, artist: str, album: str) -> None:
        """Artist names and the album as links to their detail pages (plain text when unknown)."""
        q = self.backend.active_queue
        mi = current_media_item(self.backend)
        if q and q.current_item and dj_segment(q.current_item):
            mi = None
        esc = GLib.markup_escape_text
        artists = [a for a in (getattr(mi, "artists", None) or []) if getattr(a, "item_id", None)]
        key = (artist, album, tuple(a.item_id for a in artists), getattr(getattr(mi, "album", None), "item_id", None))
        if key == getattr(self, "_links_key", None):
            return
        self._links_key = key
        self._links = {}
        if artists:
            parts = []
            for i, a in enumerate(artists):
                self._links[f"artist:{i}"] = a
                parts.append(f'<a href="artist:{i}" title="Open artist">{esc(a.name)}</a>')
            self.artist.set_markup(", ".join(parts))
        else:
            self.artist.set_text(artist)
        alb = getattr(mi, "album", None)
        if alb and getattr(alb, "item_id", None):
            self._links["album"] = alb
            self.album.set_markup(f'<a href="album" title="Open album">{esc(alb.name)}</a>')
        else:
            self.album.set_text(album)

    def _open_link(self, _lbl, uri: str) -> bool:
        item = self._links.get(uri)
        win = self.get_root()
        if item is not None and hasattr(win, "open_item"):
            win.open_item(item)
        return True

    def refresh(self) -> None:
        b = self.backend
        p, q = b.active_player, b.active_queue
        title, artist, album, duration, art_src = track_info(b)
        if self.title.get_label() != title:
            self.title.set_label(title)
        self._set_links(artist, album)
        self.artist.set_visible(bool(artist))
        self.album.set_visible(bool(album))
        self.player_label.set_label(p.name if p else "No player")
        if art_src is None and q and dj_segment(q.current_item):
            self.art.set_texture(None)  # show the DJ mic, keep the url cache for the next song
            self.art._url = None
        else:
            self.art.set_url(art_url(b, art_src, 0), self.theme.set_artwork)

        playing = is_playing(b)
        self.play.sym.set_symbol("pause" if playing else "play_arrow")
        _set_class(self.play, "playing", playing)
        seg = dj_segment(q.current_item) if q else None
        self.caption.show_segment(seg, host_name(b, seg["host_id"]) if seg else "")
        if seg:
            # the DJ is talking: mic in a pulsing burst instead of cover art
            self.art.set_icon("sym:" + dj_icon(b, seg["host_id"]))
            self.art.set_shape(DJ_SHAPE)
            self.art.set_spinning(0.9 if playing else 0.0)
        else:
            self.art.set_icon("audio-x-generic-symbolic")
            self.art.set_shape(ART_PLAYING_SHAPE if playing else ART_PAUSED_SHAPE)
            self.art.set_spinning(SPIN if playing else 0.0)
        mi = current_media_item(b)
        self._load_favorite(mi)
        self.options.refresh()

        self.tabs["dj"].set_visible(b.ai_available)
        if self.stack.get_visible_child_name() == "dj" and not b.ai_available:
            self.tabs["lyrics"].set_active(True)
        if self.stack.get_visible_child_name() == "lyrics":
            self._update_lyrics(title, artist, bool(seg))
        elif self.stack.get_visible_child_name() == "about":
            self.about.refresh(b)
        self.seek.set_sensitive(bool(duration) and q is not None)
        self.duration.set_label(fmt_time(duration) if duration else "")

        _set_class(self.shuffle, "on", bool(q and q.shuffle_enabled))
        rep = q.repeat_mode if q else RepeatMode.OFF
        self.repeat.sym.set_symbol("repeat_one" if rep == RepeatMode.ONE else "repeat")
        _set_class(self.repeat, "on", rep != RepeatMode.OFF)
        for w in (self.shuffle, self.repeat):
            w.set_sensitive(q is not None)

        if p:
            vol = p.volume_level or 0
            if not self._vol_timer:
                self.volume.handler_block(self._vol_handler)
                self.volume.set_value(vol)
                self.volume.handler_unblock(self._vol_handler)
                self.vol_value.set_label(str(int(vol)))
            muted = bool(p.volume_muted)
            self.mute.sym.set_symbol("volume_off" if muted or vol == 0 else
                                     "volume_mute" if vol < 34 else
                                     "volume_down" if vol < 67 else "volume_up")
        self._tick()

    def _tick(self) -> bool:
        if not self.get_mapped():
            return True
        self._fit()
        b = self.backend
        _t, _a, _al, dur, _s = track_info(b)
        el = b.elapsed()
        self.seek.set_state(el / dur if dur else 0.0, is_playing(b))
        self.elapsed.set_label(fmt_time(el) if dur else "")
        if self.stack.get_visible_child_name() == "lyrics":
            self.lyrics.update(el)
        return True

    # ------------------------------------------------------------------ lyrics

    def _update_lyrics(self, title: str, artist: str, dj_talking: bool) -> None:
        key = f"{title}\x00{artist}"
        if key == self._lyrics_key:
            return
        self._lyrics_key = key
        if dj_talking:
            self.lyrics.set_message("The DJ is on air")
            return
        if not title or title == "Nothing playing":
            self.lyrics.set_message("Nothing playing")
            return
        self.lyrics.set_message("Loading lyrics…")

        async def go():
            _uri, plain, lrc = await self.backend.lyrics_for_current()
            if self._lyrics_key == key:
                self.lyrics.set_lyrics(plain, lrc)
                self._tick()

        self.backend.run(go())

    def _on_seek(self, _w, frac: float) -> None:
        _t, _a, _al, dur, _s = track_info(self.backend)
        if dur:
            self.backend.seek(frac * dur)

    def _on_volume(self, scale) -> None:
        if self._vol_timer:
            GLib.source_remove(self._vol_timer)
        self.vol_value.set_label(str(int(scale.get_value())))

        def send():
            self._vol_timer = 0
            self.backend.set_volume(int(scale.get_value()))
            return False

        self._vol_timer = GLib.timeout_add(120, send)

    # ------------------------------------------------------------------ favorite

    def _show_favorite(self, on: bool) -> None:
        _set_class(self.fav, "on", on)
        self.fav.sym.set_fill(on)
        self.fav.set_tooltip_text("Remove from favorites" if on else "Add to favorites")

    def _load_favorite(self, mi) -> None:
        uri = getattr(mi, "uri", None)
        if uri == self._fav_uri:
            return
        self._fav_uri, self._fav_on, self._fav_lib = uri, False, None
        self._show_favorite(False)
        if not uri:
            return

        async def go():
            on, lib = await self.backend.favorite_state(mi)
            if self._fav_uri == uri:
                self._fav_on, self._fav_lib = on, lib
                self._show_favorite(on)

        self.backend.run(go())

    def _favorite(self) -> None:
        mi = current_media_item(self.backend)
        if mi is None or not self._fav_uri:
            # external source (not a queue item): MA can only add what's playing
            self.backend.toggle_favorite_current()
            self._show_favorite(True)
            return
        uri, want = self._fav_uri, not self._fav_on
        self._fav_on = want
        self._show_favorite(want)  # optimistic

        async def go():
            try:
                await self.backend.set_favorite(mi, want, self._fav_lib)
                on, lib = await self.backend.favorite_state(mi)  # confirm with the server
            except Exception as err:  # noqa: BLE001
                log.warning("favorite failed: %s", err)
                on, lib = not want, self._fav_lib
                if (win := self.get_root()) and hasattr(win, "toast"):
                    win.toast("Couldn’t update favorites")
            if self._fav_uri == uri:
                self._fav_on, self._fav_lib = on, lib
                self._show_favorite(on)

        self.backend.run(go())


class MiniPlayer(Gtk.Box):
    """Floating pill toolbar with the current track; click to open Now Playing."""

    def __init__(self, backend, open_np) -> None:
        super().__init__(spacing=4, halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
                         margin_bottom=20, margin_start=16, margin_end=16)
        self.backend = backend
        self.add_css_class("mini-player")
        self.set_size_request(460, -1)

        target = Gtk.Button(hexpand=True)
        target.add_css_class("flat")
        target.add_css_class("open-target")
        target.connect("clicked", lambda *_: open_np())
        tb = Gtk.Box(spacing=12)
        self.art = ArtImage(backend, 48, shape="circle", keep_stale=True)
        tb.append(self.art)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        self.title = label("", "title-s", max_width_chars=1, hexpand=True)
        self.subtitle = label("", "label-m", "on-variant", max_width_chars=1)
        self.progress = WavySeekbar(height=10, amplitude=2.0, wavelength=18, line_width=3, interactive=False,
                                    pad=2)  # flush with the text above
        text.append(self.title)
        text.append(self.subtitle)
        text.append(self.progress)
        tb.append(text)
        target.set_child(tb)
        self.append(target)

        self.append(sym_button("skip_previous", "Previous", ("m3", "icon", "flat"), 24, True, backend.previous))
        self.play = sym_button("play_arrow", "Play / pause", ("play-mini",), 28, True, backend.play_pause)
        self.append(self.play)
        self.append(sym_button("skip_next", "Next", ("m3", "icon", "flat"), 24, True, backend.next))

        for sig in ("player-updated", "queue-updated", "active-player-changed", "players-changed",
                    "status-changed"):
            backend.connect(sig, lambda *_: schedule_refresh(self))
        GLib.timeout_add(500, self._tick)
        self.refresh()

    def set_narrow(self, narrow: bool) -> None:
        self.set_size_request(-1 if narrow else 460, -1)
        self.set_halign(Gtk.Align.FILL if narrow else Gtk.Align.CENTER)

    def refresh(self) -> None:
        b = self.backend
        title, artist, _album, _dur, art_src = track_info(b)
        p = b.active_player
        self.title.set_label(title)
        self.subtitle.set_label(" · ".join(x for x in (artist, p.name if p else "") if x))
        q = b.active_queue
        seg = dj_segment(q.current_item) if q else None
        self.art.set_url(None if seg else art_url(b, art_src, 96))
        self.art.set_icon("sym:" + dj_icon(b, seg["host_id"]) if seg else "audio-x-generic-symbolic")
        playing = is_playing(b)
        self.play.sym.set_symbol("pause" if playing else "play_arrow")
        _set_class(self.play, "playing", playing)
        self.art.set_shape(DJ_SHAPE if seg else ("cookie9" if playing else "circle"))
        self.art.set_spinning((1.0 if seg else 0.5) if playing else 0.0)
        self._tick()

    def _tick(self) -> bool:
        if self.get_mapped():
            _t, _a, _al, dur, _s = track_info(self.backend)
            self.progress.set_state(self.backend.elapsed() / dur if dur else 0.0, is_playing(self.backend))
        return True
