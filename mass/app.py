"""Adw.Application: wiring, actions, background mode, notifications."""

from __future__ import annotations

import asyncio
import logging

from gi.repository import Adw, Gio, GLib

from . import APP_ID, APP_NAME, __version__
from .backend import Backend
from .config import Config
from .endpoint import Endpoint
from .mpris import MprisServer
from .theme import ThemeManager
from .notify import Notifier
from .tray import Tray
from .updater import Updater
from .ui.nowplaying import art_url, track_info
from music_assistant_models.enums import PlaybackState

log = logging.getLogger(__name__)

BACKEND_STOP_TIMEOUT = 3


class MassApp(Adw.Application):
    def __init__(self) -> None:
        # MASS_SCREENSHOT debug runs: separate instance, no services (MPRIS, tray, local player)
        self._shot = bool(GLib.getenv("MASS_SCREENSHOT"))
        flags = Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        super().__init__(application_id=APP_ID,
                         flags=flags | Gio.ApplicationFlags.NON_UNIQUE if self._shot else flags)
        self.add_main_option("background", ord("b"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Start without showing the window", None)
        self.config = Config.load()
        self.window = None
        self.backend: Backend | None = None
        self._held = False
        self._last_track: str | None = None
        self._quit_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ lifecycle

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        self.backend = Backend(self.config)
        self.theme = ThemeManager(self.config)
        self.endpoint = Endpoint(self.config)
        self.mpris = MprisServer(self, self.backend)
        self.notifier = Notifier(self._on_notify_action)
        self.tray = Tray(self, self.backend)
        self.updater = Updater(self.config)
        self.updater.connect("changed", self._on_update_state)
        self._update_notified = ""

        from .ui.window import MainWindow

        self.window = MainWindow(self)
        self.window.connect("close-request", self._on_close)
        self._setup_actions()

        self.backend.connect("queue-updated", lambda *_: self._maybe_notify())
        self.backend.connect("player-updated", lambda *_: self._maybe_notify())
        self.backend.connect("active-player-changed", lambda *_: self._maybe_notify(force_silent=True))
        self.backend.connect("status-changed", self._on_status)

        if self.config.mpris and not self._shot:
            self.mpris.start()
        if self.config.tray and not self._shot:
            self.tray.start()
        if self.config.endpoint_enabled and not self._shot:
            self.backend.run(self.endpoint.apply())
        self.backend.start()
        if not self._shot:
            self.updater.start()
        # clean shutdown (stop the child endpoint, release MPRIS) on Ctrl+C / kill
        for sig in (2, 15):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: (self.quit(), False)[1])
        # keep running even with no window so MPRIS / media keys keep working
        self.hold()
        self._held = True

    def do_command_line(self, cmd) -> int:
        opts = cmd.get_options_dict().end().unpack()
        if not opts.get("background") or cmd.get_is_remote():
            self.activate()
        return 0

    def do_activate(self) -> None:
        self.window.present()
        if shot := GLib.getenv("MASS_SCREENSHOT"):
            # debug: MASS_SCREENSHOT=/path.png[:delay_s[:page]] saves the window and exits
            path, *rest = shot.split(":")
            if len(rest) > 2:  # optional WxH
                w, h = map(int, rest[2].split("x"))
                self.window.set_default_size(w, h)
            delay = int(rest[0]) if rest else 4
            if len(rest) > 1:
                page = rest[1]
                GLib.timeout_add_seconds(max(1, delay - 2), lambda: (self.window.show_page(page), False)[1])
            GLib.timeout_add_seconds(delay, self._screenshot, path)
        if self.backend.status == "auth-required":
            self.window.show_login()

    def _screenshot(self, path: str) -> bool:
        from gi.repository import Gtk, Graphene

        w = self.window
        paintable = Gtk.WidgetPaintable.new(w)
        snap = Gtk.Snapshot()
        paintable.snapshot(snap, w.get_width(), w.get_height())
        node = snap.to_node()
        if node:
            tex = w.get_native().get_renderer().render_texture(
                node, Graphene.Rect().init(0, 0, w.get_width(), w.get_height()))
            tex.save_to_png(path)
            log.info("screenshot saved to %s", path)
        self.quit()
        return False

    def do_shutdown(self) -> None:
        self.mpris.stop()
        self.tray.stop()
        self.endpoint.shutdown()
        Adw.Application.do_shutdown(self)

    def quit(self) -> None:
        """Close the server connection, then quit.

        The asyncio loop is the running GLib main loop, but it stops being iterated once
        do_shutdown starts, so the connection has to be closed before the real quit.
        """
        if self.backend is None:
            Adw.Application.quit(self)
            return
        if self._quit_task:
            return

        async def stop_then_quit() -> None:
            try:
                await asyncio.wait_for(self.backend.stop(), BACKEND_STOP_TIMEOUT)
            except Exception as err:  # noqa: BLE001
                log.warning("backend did not stop cleanly: %r", err)
            Adw.Application.quit(self)

        self._quit_task = asyncio.ensure_future(stop_then_quit())

    def _on_close(self, win) -> bool:
        if self.config.run_in_background:
            win.set_visible(False)
            return True  # keep the window object; app.hold() keeps us alive
        self.request_quit()
        return True  # request_quit decides (it may ask first)

    def _local_player_playing(self):
        """This PC's MA player, if it is playing right now (it outlives the app: systemd service)."""
        if not self.config.endpoint_enabled or not self.backend.client:
            return None
        pid = self.endpoint.find_player_id(self.backend)
        p = self.backend.get_player(pid) if pid else None
        return p if p and p.playback_state == PlaybackState.PLAYING else None

    def request_quit(self) -> None:
        """Quit, but first ask whether music playing on this PC should keep going."""
        p = self._local_player_playing()
        if p is None:
            self.quit()
            return
        self.window.present()
        dlg = Adw.AlertDialog(heading="Music is playing on this PC",
                              body=f"“{p.name}” keeps playing after Mass quits. Stop it too?")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("keep", "Keep Playing")
        dlg.add_response("stop", "Stop & Quit")
        dlg.set_response_appearance("stop", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("keep")
        dlg.set_close_response("cancel")

        def answered(_d, response):
            if response == "keep":
                self.quit()
            elif response == "stop":
                async def stop_then_quit():
                    try:
                        await self.backend.client.players.pause(p.player_id)
                    except Exception as err:  # noqa: BLE001
                        log.warning("could not pause %s: %s", p.name, err)
                    self.quit()

                asyncio.ensure_future(stop_then_quit())

        dlg.connect("response", answered)
        dlg.present(self.window)

    def _on_status(self, _b, status, _detail) -> None:
        if status == "connected" and self.config.endpoint_enabled:
            # first run: select the local player if nothing is selected
            if not self.config.active_player_id:
                GLib.timeout_add_seconds(2, self._select_local)

    def _on_update_state(self, up) -> None:
        self.window.show_update(up)
        if up.state == "ready" and self._update_notified != up.installed:
            self._update_notified = up.installed
            self.notifier.message("Mass update ready", "Restart Mass to use the new version.",
                                  [("restart", "Restart now"), ("default", "Later")],
                                  lambda key: key == "restart" and self.restart_for_update())

    def restart_for_update(self) -> None:
        self.updater.restart(self, show_window=self.window.get_visible())

    def _on_notify_action(self, key: str) -> None:
        {"next": self.backend.next, "previous": self.backend.previous}.get(key, self.activate)()

    def _select_local(self) -> bool:
        if (pid := self.endpoint.find_player_id(self.backend)) and not self.config.active_player_id:
            self.backend.set_active_player(pid)
        return False

    # ------------------------------------------------------------------ actions

    def _setup_actions(self) -> None:
        b = self.backend

        def add(name, cb, accels=()):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_: cb())
            self.add_action(act)
            if accels:
                self.set_accels_for_action(f"app.{name}", list(accels))

        add("quit", self.request_quit, ["<Ctrl>q"])
        add("show", self.activate)
        add("preferences", self._show_prefs, ["<Ctrl>comma"])
        add("about", self._show_about)
        add("shortcuts", self._show_shortcuts, ["<Ctrl>question"])
        add("play-pause", b.play_pause, ["<Ctrl>space"])
        add("next", b.next, ["<Ctrl>Right"])
        add("previous", b.previous, ["<Ctrl>Left"])
        add("search", lambda: self.window.focus_search(), ["<Ctrl>f"])
        add("volume-up", lambda: b.set_volume((b.active_player.volume_level or 0) + 5) if b.active_player else None,
            ["<Ctrl>Up"])
        add("volume-down", lambda: b.set_volume((b.active_player.volume_level or 0) - 5) if b.active_player else None,
            ["<Ctrl>Down"])
        add("toggle-sheet", lambda: self.window.toggle_now_playing(), ["<Ctrl>n"])
        add("close-window", lambda: self.window.close(), ["<Ctrl>w"])

    def _show_prefs(self) -> None:
        from .ui.dialogs import PreferencesDialog

        PreferencesDialog(self).present(self.window)

    def _show_about(self) -> None:
        Adw.AboutDialog(
            application_name=APP_NAME, application_icon=APP_ID, version=__version__,
            comments="Music Assistant player with matugen theming and Linux desktop integration",
            license_type=7,  # GPL-3.0
        ).present(self.window)

    def _show_shortcuts(self) -> None:
        dlg = Adw.Dialog(title="Keyboard Shortcuts", content_width=380)
        page = Adw.PreferencesPage()
        grp = Adw.PreferencesGroup()
        for keys, what in (("Ctrl+Space", "Play / pause"), ("Ctrl+→ / Ctrl+←", "Next / previous"),
                           ("Ctrl+↑ / Ctrl+↓", "Volume up / down"), ("Ctrl+N", "Toggle now playing"),
                           ("Ctrl+F", "Search"), ("Ctrl+,", "Preferences"), ("Ctrl+W", "Close window"),
                           ("Ctrl+Q", "Quit")):
            row = Adw.ActionRow(title=what)
            row.add_suffix(Gtk_label(keys))
            grp.add(row)
        page.add(grp)
        tv = Adw.ToolbarView(content=page)
        tv.add_top_bar(Adw.HeaderBar())
        dlg.set_child(tv)
        dlg.present(self.window)

    # ------------------------------------------------------------------ notifications

    def _maybe_notify(self, force_silent: bool = False) -> None:
        q = self.backend.active_queue
        key = q.current_item.queue_item_id if q and q.current_item else None
        if key == self._last_track:
            return
        self._last_track = key
        if force_silent or not key or not self.config.notifications:
            return
        if self.window.get_visible() and self.window.is_active():
            return
        title, artist, album, _d, item = track_info(self.backend)
        body = " — ".join(x for x in (artist, album) if x)

        async def send():
            url = art_url(self.backend, item, 256)
            data = await self.backend.fetch_image(url) if url else None
            if self._last_track == key:
                self.notifier.show(title, body, data)

        self.backend.run(send())


def Gtk_label(text: str):  # noqa: N802 - tiny helper to avoid importing Gtk at module top
    from gi.repository import Gtk

    lbl = Gtk.Label(label=text)
    lbl.add_css_class("dim-label")
    return lbl
