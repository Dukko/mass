"""Now-playing page and the floating mini player (M3 Expressive)."""

from __future__ import annotations

from gi.repository import GLib, Gtk
from music_assistant_models.enums import PlaybackState, RepeatMode

from .common import ArtImage, artists_str, fmt_time, label
from .dj import DJ_SHAPE, DJCaption, DJChip, dj_segment, host_name
from .expressive import Sym, WavySeekbar, sym_button
from .lyrics import LyricsView
from .np_options import NowPlayingOptions

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


class NowPlayingPage(Gtk.Box):
    def __init__(self, backend, theme, open_players, open_queue) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.backend, self.theme = backend, theme
        self.add_css_class("np-page")

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.append(scroller)
        self.layout = Gtk.Box(spacing=48, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                              margin_top=64, margin_bottom=120, margin_start=32, margin_end=32)
        scroller.set_child(self.layout)

        self.art = ArtImage(backend, 400, shape=ART_PAUSED_SHAPE, fetch_size=0, keep_stale=True)
        self.lyrics = LyricsView(lambda t: backend.seek(t))
        self.lyrics.set_size(440, 420)
        self.visual = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=250,
                                valign=Gtk.Align.CENTER, interpolate_size=True, vhomogeneous=False,
                                hhomogeneous=False)
        self.visual.add_named(self.art, "art")
        self.visual.add_named(self.lyrics, "lyrics")
        self.layout.append(self.visual)
        self._lyrics_key: str | None = None

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER,
                      width_request=380)
        self.col = col
        self.layout.append(col)

        # player chip
        chip = Gtk.Button(halign=Gtk.Align.START, tooltip_text="Choose player")
        chip.add_css_class("player-chip")
        cb = Gtk.Box(spacing=8)
        cb.append(Sym("speaker", 20, fill=True))
        self.player_label = label("No player", max_width_chars=28)
        cb.append(self.player_label)
        cb.append(Sym("expand_more", 20))
        chip.set_child(cb)
        chip.connect("clicked", lambda *_: open_players(chip))
        self.chip = chip
        chips = Gtk.Box(spacing=8)
        chips.append(chip)
        self.dj_chip = DJChip(backend)
        chips.append(self.dj_chip)
        col.append(chips)

        col.append(Gtk.Box(height_request=12))
        self.title = label("", "np-title", ellipsize=False, wrap=True, lines=3)
        self.title.set_wrap_mode(2)  # word-char
        self.artist = label("", "np-artist")
        self.album = label("", "np-album")
        for w in (self.title, self.artist, self.album):
            col.append(w)
        self.caption = DJCaption()
        self.caption.set_margin_top(12)
        col.append(self.caption)

        col.append(Gtk.Box(height_request=12))
        self.seek = WavySeekbar(height=40, amplitude=4.5, wavelength=38, line_width=5)
        self.seek.connect("seek", self._on_seek)
        col.append(self.seek)
        times = Gtk.Box(margin_start=10, margin_end=10)
        self.elapsed = label("", "label-l", "on-variant", "numeric", hexpand=True)
        self.duration = label("", "label-l", "on-variant", "numeric", xalign=1)
        times.append(self.elapsed)
        times.append(self.duration)
        col.append(times)

        # transport: shuffle · [prev | PLAY | next] · repeat
        transport = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=16)
        self.shuffle = sym_button("shuffle", "Shuffle", ("m3", "icon", "large", "toggle"),
                                  cb=lambda: backend.set_shuffle(
                                      not (backend.active_queue and backend.active_queue.shuffle_enabled)))
        group = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
        group.add_css_class("btn-group")
        self.prev = sym_button("skip_previous", "Previous", (), 28, True, backend.previous)
        self.play = sym_button("play_arrow", "Play / pause", ("play-hero",), 40, True, backend.play_pause)
        self.next = sym_button("skip_next", "Next", (), 28, True, backend.next)
        for w in (self.prev, self.play, self.next):
            group.append(w)
        self.repeat = sym_button("repeat", "Repeat", ("m3", "icon", "large", "toggle"), cb=backend.cycle_repeat)
        for w in (self.shuffle, group, self.repeat):
            transport.append(w)
        col.append(transport)

        # volume + extras
        extras = Gtk.Box(spacing=8, margin_top=20)
        self.mute = sym_button("volume_up", "Mute", ("m3", "icon", "flat"),
                               cb=lambda: backend.set_mute(
                                   not (backend.active_player and backend.active_player.volume_muted)))
        self.volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.volume.set_hexpand(True)
        self.volume.set_draw_value(False)
        self.volume.add_css_class("m3-slider")
        self.volume.set_valign(Gtk.Align.CENTER)
        self._vol_handler = self.volume.connect("value-changed", self._on_volume)
        self._vol_timer = 0
        self.fav = sym_button("favorite", "Add to favorites", ("m3", "icon", "toggle"), cb=self._favorite)
        queue_btn = sym_button("queue_music", "Queue", ("m3", "icon", "tonal"), cb=open_queue)
        for w in (self.mute, self.volume, self.fav, queue_btn):
            extras.append(w)
        col.append(extras)

        self.options = NowPlayingOptions(backend, self._show_lyrics, self.seek.set_wavy)
        col.append(self.options)
        self.seek.set_wavy(backend.config.wavy_progress)
        self.visual.set_visible_child_name("lyrics" if backend.config.show_lyrics else "art")

        for sig in ("player-updated", "queue-updated", "active-player-changed", "players-changed",
                    "status-changed"):
            backend.connect(sig, self._on_change)
        GLib.timeout_add(250, self._tick)
        self.refresh()

    def set_narrow(self, narrow: bool) -> None:
        self.layout.set_orientation(Gtk.Orientation.VERTICAL if narrow else Gtk.Orientation.HORIZONTAL)
        self.layout.set_spacing(24 if narrow else 48)
        self.col.set_size_request(-1 if narrow else 380, -1)
        self.art.size = 280 if narrow else 400
        self.art.queue_resize()
        self.lyrics.set_size(-1 if narrow else 440, 300 if narrow else 420)

    # ------------------------------------------------------------------ updates

    def _on_change(self, _b, *args) -> None:
        if (args and isinstance(args[0], str) and args[0] and not self.backend.is_relevant(args[0])
                and args[0] not in ("connected", "connecting", "disconnected", "auth-required")):
            return
        schedule_refresh(self)

    def refresh(self) -> None:
        b = self.backend
        p, q = b.active_player, b.active_queue
        title, artist, album, duration, art_src = track_info(b)
        if self.title.get_label() != title:
            self.title.set_label(title)
        self.artist.set_label(artist)
        self.artist.set_visible(bool(artist))
        self.album.set_label(album)
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
            self.art.set_icon("microphone-sensitivity-high-symbolic")
            self.art.set_shape(DJ_SHAPE)
            self.art.set_spinning(0.9 if playing else 0.0)
        else:
            self.art.set_icon("audio-x-generic-symbolic")
            self.art.set_shape(ART_PLAYING_SHAPE if playing else ART_PAUSED_SHAPE)
            self.art.set_spinning(SPIN if playing else 0.0)
        self.options.refresh()
        self._update_lyrics(title, artist, bool(seg))
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
            muted = bool(p.volume_muted)
            self.mute.sym.set_symbol("volume_off" if muted or vol == 0 else
                                     "volume_mute" if vol < 34 else
                                     "volume_down" if vol < 67 else "volume_up")
        self._tick()

    def _tick(self) -> bool:
        if not self.get_mapped():
            return True
        b = self.backend
        _t, _a, _al, dur, _s = track_info(b)
        el = b.elapsed()
        self.seek.set_state(el / dur if dur else 0.0, is_playing(b))
        self.elapsed.set_label(fmt_time(el) if dur else "")
        if self.visual.get_visible_child() is self.lyrics:
            self.lyrics.update(el)
        return True

    # ------------------------------------------------------------------ lyrics

    def _show_lyrics(self, on: bool) -> None:
        self.visual.set_visible_child(self.lyrics if on else self.art)
        if on:
            self._lyrics_key = None
            self.refresh()

    def _update_lyrics(self, title: str, artist: str, dj_talking: bool) -> None:
        if not self.backend.config.show_lyrics:
            return
        key = f"{title}\x00{artist}"
        if key == self._lyrics_key:
            return
        self._lyrics_key = key
        if dj_talking:
            self.lyrics.set_message("The DJ is on air")
            return
        if not title:
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

        def send():
            self._vol_timer = 0
            self.backend.set_volume(int(scale.get_value()))
            return False

        self._vol_timer = GLib.timeout_add(120, send)

    def _favorite(self) -> None:
        self.backend.toggle_favorite_current()
        self.fav.add_css_class("on")


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
        self.progress = WavySeekbar(height=10, amplitude=2.0, wavelength=18, line_width=3, interactive=False)
        self.progress.set_margin_start(-8)
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
        self.art.set_icon("microphone-sensitivity-high-symbolic" if seg else "audio-x-generic-symbolic")
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
