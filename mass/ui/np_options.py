"""Now Playing option chips + ⋮ menu, mirroring the Music Assistant web UI.

Widgets (placed by NowPlayingPage): HR badge · Autoplay · Crossfade · Sleep timer · ⋮ (stop,
audio overlay, announcement, transfer, clear, save as playlist, AI DJ, settings, wavy progress bar).
The "np" action group is installed on the page so every one of them can reach it.
"""

from __future__ import annotations

import time

from gi.repository import Gio, GLib, Gtk
from music_assistant_models.enums import MediaType

from .. import webapp
from ..backend import ANNOUNCEMENT_SCHEMA, OVERLAY_SCHEMA, SLEEP_TIMER_SCHEMA
from . import links
from .common import ask_text, label, open_add_to_playlist
from .expressive import Sym
from .player_settings import DSPDialog, PlayerSettingsDialog
from .signal_path import SignalPathPopover

SLEEP_MINUTES = (15, 30, 45, 60, 90, 120)


def _tool(symbol: str, text: str, toggle: bool = False) -> Gtk.Button:
    btn = Gtk.ToggleButton() if toggle else Gtk.Button()
    btn.add_css_class("np-tool")
    box = Gtk.Box(spacing=6)
    btn.sym = Sym(symbol, 18)
    box.append(btn.sym)
    box.append(Gtk.Label(label=text))
    btn.set_child(box)
    return btn


def _item(text: str, action: str, target: str) -> Gio.MenuItem:
    item = Gio.MenuItem.new(text, None)
    item.set_action_and_target_value(action, GLib.Variant("s", target))
    return item


def audio_quality(backend) -> tuple[str, str] | None:
    """('HR' | codec, tooltip) for the current queue item, if the server reported a format."""
    q = backend.active_queue
    sd = getattr(q.current_item, "streamdetails", None) if q and q.current_item else None
    fmt = getattr(sd, "audio_format", None)
    if not fmt or not fmt.sample_rate:
        return None
    codec = str(getattr(fmt.content_type, "value", fmt.content_type) or "").upper()
    rate = fmt.sample_rate / 1000
    tip = f"{codec} · {rate:g} kHz · {fmt.bit_depth}-bit"
    chain = getattr(sd, "audio_processing", None)
    quality = str(getattr(chain.input_fidelity.quality, "value", "")) if chain else ""
    if quality and quality != "unknown":
        hires = quality == "hi_res"
    else:
        hires = (fmt.bit_depth or 16) > 16 or fmt.sample_rate > 48000
    return ("HR" if hires else codec or "?"), tip


class NowPlayingOptions:
    def __init__(self, backend, host: Gtk.Widget, on_wavy) -> None:
        self.backend, self.host, self.on_wavy = backend, host, on_wavy

        # quality badge: click for the signal path (input → processing → output)
        self.badge = Gtk.MenuButton(valign=Gtk.Align.CENTER, popover=SignalPathPopover(backend))
        self.badge.add_css_class("np-badge")
        inner = Gtk.Box(spacing=6)
        dot = Gtk.Box(valign=Gtk.Align.CENTER)
        dot.add_css_class("dot")
        inner.append(dot)
        self.badge_lbl = label("HR", ellipsize=False)
        inner.append(self.badge_lbl)
        self.badge.set_child(inner)

        self.autoplay = _tool("autoplay", "Autoplay")
        self.autoplay.set_tooltip_text("Keep playing similar music when the queue ends")
        self.autoplay.connect("clicked", lambda *_: self._flip("autoplay_enabled", backend.set_autoplay))
        self.crossfade = _tool("join", "Crossfade")
        self.crossfade.connect("clicked", lambda *_: self._flip("crossfade_enabled", backend.set_crossfade))

        self._sleep_menu = Gio.Menu()
        self._sleep_at: float | None = None
        self._sleep_tick = 0
        self._sleep_refresh = 0
        self.sleep = Gtk.MenuButton(menu_model=self._sleep_menu, valign=Gtk.Align.CENTER,
                                    tooltip_text="Stop playback after a while")
        self.sleep.add_css_class("np-tool")
        sb = Gtk.Box(spacing=6)
        self.sleep_sym = Sym("bedtime", 18)
        sb.append(self.sleep_sym)
        self.sleep_lbl = Gtk.Label(label="Sleep timer")
        sb.append(self.sleep_lbl)
        self.sleep.set_child(sb)
        self.sleep.set_create_popup_func(lambda *_: (self._fill_sleep(), self._fetch_sleep()))

        self.more = Gtk.MenuButton(tooltip_text="More options", valign=Gtk.Align.CENTER)
        self.more.set_child(Sym("more_vert", 22))
        for c in ("m3", "icon", "flat"):
            self.more.add_css_class(c)
        self.more.set_create_popup_func(self._build_menu)

        self._install_actions()
        backend.connect("active-player-changed", lambda *_: self._fetch_sleep())
        self._sleep_tick = GLib.timeout_add_seconds(30, self._count_down_sleep)
        self.host.connect("destroy", self._on_destroy)
        self._fetch_sleep()

    # ------------------------------------------------------------------ chips

    def _flip(self, attr: str, setter) -> None:
        q = self.backend.active_queue
        if q:
            setter(not getattr(q, attr))

    def refresh(self) -> None:
        q = self.backend.active_queue
        for btn, attr in ((self.autoplay, "autoplay_enabled"), (self.crossfade, "crossfade_enabled")):
            on = bool(q and getattr(q, attr, False))
            (btn.add_css_class if on else btn.remove_css_class)("on")
            btn.sym.set_fill(on)
            btn.set_sensitive(q is not None)
        self.sleep.set_visible(self.backend.supports(SLEEP_TIMER_SCHEMA))
        qual = audio_quality(self.backend)
        self.badge.set_visible(qual is not None)
        if qual:
            self.badge_lbl.set_label(qual[0])
            self.badge.set_tooltip_text(f"{qual[1]}\nClick for the signal path")
            (self.badge.add_css_class if qual[0] == "HR" else self.badge.remove_css_class)("hires")

    # ------------------------------------------------------------------ actions

    def _install_actions(self) -> None:
        b = self.backend
        group = Gio.SimpleActionGroup()

        def add(name, cb, ptype=None, state=None):
            if state is not None:
                act = Gio.SimpleAction.new_stateful(name, GLib.VariantType(ptype) if ptype else None, state)
            else:
                act = Gio.SimpleAction.new(name, GLib.VariantType(ptype) if ptype else None)
            act.connect("activate", cb)
            group.add_action(act)
            return act

        add("stop", lambda *_: b.stop_playback())
        add("clear", lambda *_: b.clear_queue())

        def set_sleep(_action, value) -> None:
            b.set_sleep_timer(value.get_int32())
            if self._sleep_refresh:
                GLib.source_remove(self._sleep_refresh)
            self._sleep_refresh = GLib.timeout_add(600, self._refresh_sleep_soon)

        add("sleep", set_sleep, "i")
        self.act_overlay = add("overlay", self._on_overlay, "s", GLib.Variant("s", ""))
        add("transfer", lambda _a, v: b.transfer_to(v.get_string()), "s")
        self.act_dj = add("dj", lambda a, v: (a.set_state(v), b.set_dj(v.get_string() or None)), "s",
                          GLib.Variant("s", ""))
        add("announce", lambda *_: self._ask("Play announcement", "Speak a message on this player",
                                             "Dinner is ready!", "Announce", b.announce))
        add("save", lambda *_: self._ask("Save queue as playlist", "Creates a new playlist in your library",
                                         time.strftime("Queue %b %-d"), "Save", b.save_queue_as_playlist,
                                         prefill=True))
        add("playlist", self._add_current_to_playlist)
        add("settings", self._open_settings, "s")
        self.act_wavy = add("wavy", self._on_wavy, None, GLib.Variant("b", b.config.wavy_progress))
        self.host.insert_action_group("np", group)

    def _on_overlay(self, act, v) -> None:
        act.set_state(v)
        self.backend.set_overlay(v.get_string() or None)

    def _on_wavy(self, act, _v) -> None:
        on = not act.get_state().get_boolean()
        act.set_state(GLib.Variant("b", on))
        self.backend.config.wavy_progress = on
        self.backend.config.save()
        self.on_wavy(on)

    def _open_settings(self, _a, v) -> None:
        what = v.get_string()
        if what == "app":
            self.host.activate_action("app.preferences", None)
            return
        p = self.backend.active_player
        if what == "web":
            if p:
                links.open_webapp(self.host, self.backend, webapp.player_route(p.player_id))
            elif hasattr(self.host, "toast"):
                self.host.toast("No active player")
            return
        if not p or not self.backend.client:
            return
        (DSPDialog if what == "dsp" else PlayerSettingsDialog)(self.backend, p.player_id, self.host)

    def _ask(self, heading, body, placeholder, verb, cb, prefill=False) -> None:
        ask_text(self.host, heading, body, placeholder, verb, cb, prefill)

    def _current_track(self):
        q = self.backend.active_queue
        mi = q.current_item.media_item if q and q.current_item else None
        return mi if getattr(mi, "media_type", None) == MediaType.TRACK else None

    def _add_current_to_playlist(self, *_args) -> None:
        if (track := self._current_track()) is not None:
            open_add_to_playlist(self.host, self.backend, [track])

    # ------------------------------------------------------------------ menu

    def _count_down_sleep(self) -> bool:
        self._fill_sleep()
        return True

    def _refresh_sleep_soon(self) -> bool:
        self._sleep_refresh = 0
        self._fetch_sleep()
        return False

    def _on_destroy(self, *_args) -> None:
        for source in (self._sleep_tick, self._sleep_refresh):
            if source:
                GLib.source_remove(source)
        self._sleep_tick = self._sleep_refresh = 0

    def _fetch_sleep(self) -> None:
        async def go():
            self._sleep_at = await self.backend.sleep_timer()
            self._fill_sleep()

        self.backend.run(go())

    def _fill_sleep(self) -> None:
        m = self._sleep_menu
        m.remove_all()
        active = bool(self._sleep_at and self._sleep_at > time.time())
        left = max(1, round((self._sleep_at - time.time()) / 60)) if active else 0
        self.sleep_lbl.set_label(f"Sleep · {left} min" if active else "Sleep timer")
        (self.sleep.add_css_class if active else self.sleep.remove_css_class)("on")
        self.sleep_sym.set_fill(active)
        if active:
            m.append(f"Stops in {left} min", "np.none")
            m.append("Turn off", "np.sleep(0)")
        for mins in SLEEP_MINUTES:
            m.append(f"{mins} minutes" if mins < 60 else f"{mins / 60:g} hour{'s' if mins > 60 else ''}",
                     f"np.sleep({mins})")

    def _build_menu(self, button) -> None:
        b = self.backend
        q, p = b.active_queue, b.active_player
        menu = Gio.Menu()
        main = Gio.Menu()
        main.append("Stop playback", "np.stop")

        if b.supports(OVERLAY_SCHEMA):
            overlay = Gio.Menu()
            self.act_overlay.set_state(
                GLib.Variant("s", (q.overlay_source or "") if q and q.overlay_enabled else ""))
            overlay.append_item(_item("Off", "np.overlay", ""))
            sub = overlay  # filled async the first time

            async def fill_effects():
                for fx in await b.sound_effects():
                    item = Gio.MenuItem.new(fx.get("name", "?"), None)
                    item.set_action_and_target_value("np.overlay", GLib.Variant("s", fx["uri"]))
                    sub.append_item(item)

            b.run(fill_effects())
            main.append_submenu("Audio overlay", overlay)
        if b.supports(ANNOUNCEMENT_SCHEMA):
            main.append("Play announcement…", "np.announce")

        transfer = Gio.Menu()
        for other in b.players():
            if p and other.player_id != p.player_id and other.available and not other.synced_to:
                item = Gio.MenuItem.new(other.name, None)
                item.set_action_and_target_value("np.transfer", GLib.Variant("s", other.player_id))
                transfer.append_item(item)
        main.append_submenu("Transfer queue", transfer)
        if self._current_track() is not None:
            main.append("Add to playlist…", "np.playlist")
        main.append("Clear queue", "np.clear")
        main.append("Save queue as playlist…", "np.save")

        if b.ai_available:
            dj = Gio.Menu()
            cur = b.dj_for(q.queue_id if q else (p.player_id if p else None))
            self.act_dj.set_state(GLib.Variant("s", cur["id"] if cur else ""))
            dj.append_item(_item("Off", "np.dj", ""))
            for hid, h in b.dj_hosts.items():
                item = Gio.MenuItem.new(h["name"], None)
                item.set_action_and_target_value("np.dj", GLib.Variant("s", hid))
                dj.append_item(item)
            main.append_submenu("AI Radio DJ", dj)

        settings = Gio.Menu()
        settings.append("Player settings…", "np.settings::player")
        settings.append("Player DSP…", "np.settings::dsp")
        settings.append("Player in web app…", "np.settings::web")
        settings.append("Mass preferences", "np.settings::app")
        main.append_submenu("Settings", settings)
        menu.append_section(None, main)

        view = Gio.Menu()
        view.append("Wavy progress bar", "np.wavy")
        menu.append_section(None, view)
        button.set_menu_model(menu)
