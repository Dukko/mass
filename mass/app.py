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

log = logging.getLogger(__name__)


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
            self.endpoint.apply()
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
        try:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(self.backend.stop()) if not loop.is_running() else None
        except Exception:  # noqa: BLE001
            pass
        Adw.Application.do_shutdown(self)

    def _on_close(self, win) -> bool:
        if self.config.run_in_background:
            win.set_visible(False)
            return True  # keep the window object; app.hold() keeps us alive
        self.quit()
        return False

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

        add("quit", self.quit, ["<Ctrl>q"])
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
