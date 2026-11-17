"""Sign-in and preferences dialogs."""

from __future__ import annotations

import asyncio
from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from .. import APP_ID, APP_NAME
from ..host import HOST_CONFIG, launch_cmd
from ..discovery import discover

AUTOSTART = HOST_CONFIG / "autostart" / f"{APP_ID}.desktop"


class LoginDialog(Adw.Dialog):
    def __init__(self, backend) -> None:
        super().__init__(title="Connect to Music Assistant", content_width=460, content_height=640)
        self.backend = backend
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        tv.set_content(page)
        self.set_child(tv)

        self.found = Adw.PreferencesGroup(title="Servers on your network")
        self.found_spinner = Adw.Spinner()
        self.found.set_header_suffix(self.found_spinner)
        page.add(self.found)

        grp = Adw.PreferencesGroup(title="Sign in",
                                   description="Mass creates a long-lived token and keeps it in your keyring.")
        self.url = Adw.EntryRow(title="Server URL", text=backend.config.server_url or "")
        self.user = Adw.EntryRow(title="Username", text=backend.config.username or "")
        self.pw = Adw.PasswordEntryRow(title="Password")
        self.pw.connect("entry-activated", lambda *_: self._login())
        for r in (self.url, self.user, self.pw):
            grp.add(r)
        page.add(grp)

        btn_grp = Adw.PreferencesGroup()
        self.error = Gtk.Label(wrap=True, visible=False)
        self.error.add_css_class("error")
        btn_grp.add(self.error)
        self.btn = Gtk.Button(label="Sign in", halign=Gtk.Align.CENTER, margin_top=12)
        self.btn.add_css_class("pill")
        self.btn.add_css_class("suggested-action")
        self.btn.connect("clicked", lambda *_: self._login())
        btn_grp.add(self.btn)
        page.add(btn_grp)
        asyncio.ensure_future(self._discover())

    async def _discover(self) -> None:
        try:
            servers = await discover()
        except Exception:  # noqa: BLE001
            servers = []
        self.found_spinner.set_visible(False)
        if not servers:
            self.found.add(Adw.ActionRow(title="None found", subtitle="Enter the URL manually below"))
        for s in servers:
            row = Adw.ActionRow(title=GLib.markup_escape_text(s.name),
                                subtitle=f"{s.url} · v{s.version}", activatable=True)
            row.add_prefix(Gtk.Image.new_from_icon_name("network-server-symbolic"))
            row.connect("activated", lambda _r, u=s.url: (self.url.set_text(u), self.user.grab_focus()))
            self.found.add(row)
        if len(servers) == 1 and not self.url.get_text():
            self.url.set_text(servers[0].url)

    def _login(self) -> None:
        url, user, pw = self.url.get_text().strip(), self.user.get_text().strip(), self.pw.get_text()
        if not (url and user and pw):
            self._err("Fill in the server URL, username and password.")
            return
        if "://" not in url:
            url = f"http://{url}"
        self.btn.set_sensitive(False)
        self.error.set_visible(False)

        async def go():
            try:
                await self.backend.login(url, user, pw)
                self.close()
            except Exception as err:  # noqa: BLE001
                self._err(str(err) or type(err).__name__)
            finally:
                self.btn.set_sensitive(True)

        asyncio.ensure_future(go())

    def _err(self, msg: str) -> None:
        self.error.set_label(msg)
        self.error.set_visible(True)


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, app) -> None:
        super().__init__(title="Preferences", search_enabled=False)
        self.app = app
        cfg = app.config

        # ------------------------------------------------ general
        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
        self.add(page)

        srv = Adw.PreferencesGroup(title="Server")
        self.server_row = Adw.ActionRow(title=cfg.server_url or "Not connected",
                                        subtitle=app.backend.status_detail)
        signout = Gtk.Button(label="Sign out" if cfg.server_url else "Sign in", valign=Gtk.Align.CENTER)
        signout.connect("clicked", self._signout)
        self.server_row.add_suffix(signout)
        srv.add(self.server_row)
        page.add(srv)

        look = Adw.PreferencesGroup(title="Appearance",
                                    description="Colors come from matugen. “System” follows your wallpaper "
                                                "and live-reloads, through DankMaterialShell or the bundled template.")
        modes = [("system", "System (matugen)"), ("artwork", "Album art (matugen)"), ("adwaita", "Plain Adwaita")]
        self.mode_row = Adw.ComboRow(title="Color source", model=Gtk.StringList.new([m[1] for m in modes]))
        self.mode_row.set_selected(next((i for i, m in enumerate(modes) if m[0] == cfg.theme_mode), 0))
        self.mode_row.connect("notify::selected",
                              lambda r, _p: app.theme.set_mode(modes[r.get_selected()][0]))
        look.add(self.mode_row)
        schemes = ["scheme-tonal-spot", "scheme-content", "scheme-expressive", "scheme-fidelity",
                   "scheme-fruit-salad", "scheme-monochrome", "scheme-neutral", "scheme-rainbow",
                   "scheme-vibrant"]
        sch = Adw.ComboRow(title="Album art scheme",
                           model=Gtk.StringList.new([s.removeprefix("scheme-").replace("-", " ").title()
                                                     for s in schemes]))
        sch.set_selected(schemes.index(cfg.matugen_scheme) if cfg.matugen_scheme in schemes else 0)

        def set_scheme(r, _p):
            cfg.matugen_scheme = schemes[r.get_selected()]
            app.theme.set_mode(cfg.theme_mode)

        sch.connect("notify::selected", set_scheme)
        look.add(sch)
        src = Adw.ActionRow(title="Active palette", subtitle=app.theme.source)
        look.add(src)
        app.theme.connect("palette-changed", lambda t: src.set_subtitle(t.source))
        page.add(look)

        # ------------------------------------------------ local player
        lp = Adw.PreferencesPage(title="This PC", icon_name="audio-speakers-symbolic")
        self.add(lp)
        g = Adw.PreferencesGroup(title="Local player",
                                 description="Registers this computer as a Music Assistant player through "
                                             "Sendspin, so you can play to these speakers and sync with "
                                             "other rooms.")
        en = Adw.SwitchRow(title="Enable local player", active=cfg.endpoint_enabled)
        name = Adw.EntryRow(title="Player name", text=cfg.endpoint_name, show_apply_button=True)
        dev = Adw.EntryRow(title="Audio device", text=cfg.endpoint_audio_device, show_apply_button=True)
        hw = Adw.SwitchRow(title="Control system volume",
                           subtitle="Music Assistant volume changes your PipeWire sink volume",
                           active=cfg.endpoint_hardware_volume)
        self.ep_status = Adw.ActionRow(title="Status")
        for r in (en, name, dev, hw, self.ep_status):
            g.add(r)
        lp.add(g)

        def apply(*_):
            cfg.endpoint_enabled = en.get_active()
            cfg.endpoint_name = name.get_text().strip() or cfg.endpoint_name
            cfg.endpoint_audio_device = dev.get_text().strip() or "pipewire"
            cfg.endpoint_hardware_volume = hw.get_active()
            cfg.save()
            app.endpoint.apply()

        en.connect("notify::active", apply)
        hw.connect("notify::active", apply)
        name.connect("apply", apply)
        dev.connect("apply", apply)
        app.endpoint.connect("state-changed", lambda *_: self._ep_status())
        self._ep_status()

        # ------------------------------------------------ desktop integration
        dp = Adw.PreferencesPage(title="Desktop", icon_name="computer-symbolic")
        self.add(dp)
        d = Adw.PreferencesGroup(title="Desktop integration")
        bg = Adw.SwitchRow(title="Keep running in background",
                           subtitle="Closing the window keeps MPRIS and media keys working",
                           active=cfg.run_in_background)
        notif = Adw.SwitchRow(title="Track change notifications", active=cfg.notifications)
        mpris = Adw.SwitchRow(title="MPRIS media controls",
                              subtitle="playerctl, media keys, shell media widgets", active=cfg.mpris)
        tray = Adw.SwitchRow(title="Tray icon", subtitle="Click to show/hide, middle-click to play/pause, "
                             "scroll for volume", active=cfg.tray)
        auto = Adw.SwitchRow(title="Start on login", subtitle="Starts hidden in the background",
                             active=AUTOSTART.exists())
        for r in (bg, tray, notif, mpris, auto):
            d.add(r)
        dp.add(d)

        def set_attr(attr):
            def cb(r, _p):
                setattr(cfg, attr, r.get_active())
                cfg.save()
                if attr == "mpris":
                    (app.mpris.start if cfg.mpris else app.mpris.stop)()
                if attr == "tray":
                    (app.tray.start if cfg.tray else app.tray.stop)()
            return cb

        bg.connect("notify::active", set_attr("run_in_background"))
        notif.connect("notify::active", set_attr("notifications"))
        mpris.connect("notify::active", set_attr("mpris"))
        tray.connect("notify::active", set_attr("tray"))
        auto.connect("notify::active", lambda r, _p: self._autostart(r.get_active()))

    def _ep_status(self) -> None:
        ep = self.app.endpoint
        how = {"systemd": "systemd user service", "child": "managed by the app"}.get(ep.mode, "")
        txt = ep.state.capitalize() + (f" ({how})" if how else "") + (f": {ep.detail}" if ep.detail else "")
        self.ep_status.set_subtitle(GLib.markup_escape_text(txt))

    def _signout(self, *_):
        async def go():
            if self.app.config.server_url:
                await self.app.backend.logout()
            self.close()
            self.app.window.show_login()

        asyncio.ensure_future(go())

    @staticmethod
    def _autostart(enabled: bool) -> None:
        if not enabled:
            AUTOSTART.unlink(missing_ok=True)
            return
        AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART.write_text(
            "[Desktop Entry]\nType=Application\n"
            f"Name={APP_NAME}\nExec={launch_cmd()} --background\nIcon={APP_ID}\n"
            "X-GNOME-Autostart-enabled=true\nNoDisplay=true\n"
        )
