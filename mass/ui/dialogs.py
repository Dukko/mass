"""Sign-in and preferences dialogs."""

from __future__ import annotations

import asyncio
import logging
import urllib.parse

from gi.repository import Adw, GLib, Gtk

from .. import APP_ID, APP_NAME
from ..host import HOST_CONFIG, launch_cmd
from ..discovery import discover
from .. import firewall, webapp
from . import links
from .common import ask_text, label, m3_list, media_row, status_page
from .expressive import Sym

log = logging.getLogger(__name__)

AUTOSTART = HOST_CONFIG / "autostart" / f"{APP_ID}.desktop"


def validated_server_url(text: str) -> str | None:
    """The trimmed URL if it has an http(s) scheme, else None.

    A bare host is rejected instead of silently defaulting to plain-text http.
    """
    url = text.strip()
    if not url:
        return None
    try:
        scheme = urllib.parse.urlsplit(url).scheme.lower()
    except ValueError:
        return None
    return url if scheme in ("http", "https") else None


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
        url = validated_server_url(self.url.get_text())
        user, pw = self.user.get_text().strip(), self.pw.get_text()
        if not (url and user and pw):
            self._err("Fill in the server URL (with http:// or https://), username and password.")
            return
        if url.lower().startswith("http://"):
            self._confirm_plaintext(url, user, pw)
            return
        self._do_login(url, user, pw)

    def _confirm_plaintext(self, url: str, user: str, pw: str) -> None:
        dlg = Adw.AlertDialog(
            heading="Unencrypted connection",
            body="This server URL uses http://. Your password and token will be sent in clear text "
                 "over the network.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("continue", "Continue")
        dlg.set_response_appearance("continue", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.connect("response", lambda _d, r: r == "continue" and self._do_login(url, user, pw))
        dlg.present(self.get_root())

    def _do_login(self, url: str, user: str, pw: str) -> None:
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


class AddToPlaylistDialog(Adw.Dialog):
    """Pick (or create) a library playlist to append tracks / albums to."""

    def __init__(self, backend, items: list, window: Gtk.Widget) -> None:
        super().__init__(title="Add to playlist", content_width=420, content_height=560)
        self.backend, self.items, self.window = backend, items, window
        self._busy = False
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        tv.add_top_bar(header)
        self.search = Gtk.SearchEntry(placeholder_text="Search playlists", margin_start=12, margin_end=12,
                                      margin_bottom=8)
        self.search.connect("search-changed", lambda *_: self.list.invalidate_filter())
        tv.add_top_bar(self.search)

        self.list = m3_list()
        self.list.set_filter_func(self._visible)
        new = Gtk.ListBoxRow(activatable=True)
        new.is_new = True
        new.on_activate = self._new_playlist
        box = Gtk.Box(spacing=14, margin_start=12, margin_end=8, margin_top=8, margin_bottom=8)
        box.append(Sym("playlist_add", 28))
        box.append(label("New playlist…", "title-m", hexpand=True))
        new.set_child(box)
        self.list.append(new)

        self.empty = status_page("view-list-symbolic", "No editable playlists",
                                 "Create a playlist to start collecting tracks.")
        self.empty.set_visible(False)
        self.spinner = Adw.Spinner(height_request=40, margin_top=24)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=12, margin_end=12, margin_bottom=12)
        col.append(self.list)
        col.append(self.empty)
        col.append(self.spinner)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(col)
        tv.set_content(scroll)
        self.set_child(tv)
        self.set_focus(self.search)
        backend.run(self._load())

    def _visible(self, row: Gtk.ListBoxRow) -> bool:
        if getattr(row, "is_new", False):
            return True
        return self.search.get_text().strip().casefold() in (getattr(row.item, "name", "") or "").casefold()

    async def _load(self) -> None:
        try:
            playlists = await self.backend.editable_playlists()
        except Exception as err:  # noqa: BLE001
            log.error("loading playlists failed: %s", err)
            playlists = []
            self._toast("Couldn't load playlists")
        self.spinner.set_visible(False)
        self.empty.set_visible(not playlists)
        for pl in playlists:
            self.list.append(media_row(self.backend, pl, getattr(pl, "owner", "") or "", self._add_to,
                                       play_button=False, menu=False))

    def _toast(self, text: str) -> None:
        if hasattr(self.window, "toast"):
            self.window.toast(text)

    def _new_playlist(self) -> None:
        ask_text(self, "New playlist", "Name the playlist; the selection is added to it", "My playlist", "Create",
                 self._create)

    def _create(self, name: str) -> None:
        name = name.strip()
        if not name or self._busy:
            return
        self._busy = True
        self.list.set_sensitive(False)

        async def go() -> None:
            try:
                playlist = await self.backend.create_playlist(name)
            except Exception as err:  # noqa: BLE001
                log.error("creating playlist failed: %s", err)
                self._toast("Couldn’t create playlist")
                self._busy = False
                self.list.set_sensitive(True)
                return
            self._add_to(playlist, created=True)

        self.backend.run(go())

    def _add_to(self, playlist, created: bool = False) -> None:
        if self._busy and not created:
            return
        self._busy = True
        self.list.set_sensitive(False)
        self.backend.add_to_playlist(
            playlist, self.items,
            on_done=lambda: self._toast(f"Added to {playlist.name}"),
            on_fail=lambda _err: self._toast("Couldn't add to playlist"),
        )
        self.close()


class _TranslucencyRows:
    def __init__(self, app, group: Adw.PreferencesGroup) -> None:
        self.app = app
        self._save_id = 0
        cfg = app.config
        row = Adw.ActionRow(title="Transparency", subtitle="Main window background")
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 90, 5)
        self.scale.set_value(cfg.transparency)
        self.scale.set_hexpand(True)
        self.scale.set_valign(Gtk.Align.CENTER)
        self.scale.set_size_request(200, -1)
        self.scale.set_draw_value(True)
        self.scale.set_value_pos(Gtk.PositionType.RIGHT)
        self.scale.set_format_value_func(lambda _s, v: f"{v:.0f}%")
        self.scale.connect("value-changed", lambda *_: self._changed())
        row.add_suffix(self.scale)
        self.blur = Adw.SwitchRow(title="Blur behind window",
                                  subtitle="Strength is set by the compositor (niri: the blur section)",
                                  active=cfg.blur, sensitive=cfg.transparency > 0)
        self.blur.connect("notify::active", lambda *_: self._changed())
        group.add(row)
        group.add(self.blur)

    def _changed(self) -> None:
        value = round(self.scale.get_value())
        self.blur.set_sensitive(value > 0)
        self.app.theme.set_translucency(value, self.blur.get_active())
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(400, self._save)

    def _save(self) -> bool:
        self._save_id = 0
        self.app.config.save()
        return GLib.SOURCE_REMOVE


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
        if app.backend.connected:
            srv.add(self._webapp_expander())
        page.add(srv)

        look = Adw.PreferencesGroup(title="Appearance",
                                    description="Colors come from matugen or Omarchy. “System” follows your "
                                                "wallpaper or active theme and live-reloads via Omarchy, "
                                                "DankMaterialShell, or the bundled matugen template.")
        modes = [("system", "System (matugen / Omarchy)"), ("artwork", "Album art (matugen)"),
                 ("adwaita", "Plain Adwaita")]
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
        self._translucency = _TranslucencyRows(app, look)
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

        # Sendspin is server-initiated, so a default-deny firewall hides the player silently
        self.fw = Adw.PreferencesGroup(title="Firewall", visible=False)
        self.fw_row = Adw.ActionRow(title="Your firewall is blocking the local player", subtitle_selectable=True)
        self.fw_row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy command",
                          valign=Gtk.Align.CENTER, css_classes=["flat"])
        copy.connect("clicked", self._copy_fw_cmd)
        self.fw_row.add_suffix(copy)
        self.fw.add(self.fw_row)
        lp.add(self.fw)
        self._fw_cmd = ""

        def apply(*_):
            cfg.endpoint_enabled = en.get_active()
            cfg.endpoint_name = name.get_text().strip() or cfg.endpoint_name
            cfg.endpoint_audio_device = dev.get_text().strip() or "pipewire"
            cfg.endpoint_hardware_volume = hw.get_active()
            cfg.save()
            app.backend.run(app.endpoint.apply())

        en.connect("notify::active", apply)
        hw.connect("notify::active", apply)
        name.connect("apply", apply)
        dev.connect("apply", apply)
        handlers = [(app.endpoint, app.endpoint.connect("state-changed", lambda *_: self._ep_changed())),
                    (app.backend, app.backend.connect("players-changed", lambda *_: self._ep_status())),
                    (app.backend, app.backend.connect("player-updated", lambda *_: self._ep_status()))]
        self.connect("closed", lambda *_: [obj.disconnect(h) for obj, h in handlers])
        self._ep_changed()

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

        up = app.updater
        if up.enabled:
            ug = Adw.PreferencesGroup(title="Updates",
                                      description="New versions come from github.com/Dukko/mass, signed and "
                                                  "checked every few hours while Mass is running.")
            au = Adw.SwitchRow(title="Download updates automatically",
                               subtitle="You still choose when to restart", active=cfg.auto_update)

            def set_auto(r, _p):
                cfg.auto_update = r.get_active()
                cfg.save()
                if cfg.auto_update and up.state == "available":
                    up.install()

            au.connect("notify::active", set_auto)
            self.up_row = Adw.ActionRow(title="Mass")
            self.up_btn = Gtk.Button(valign=Gtk.Align.CENTER)
            self.up_btn.connect("clicked", self._update_clicked)
            self.up_row.add_suffix(self.up_btn)
            ug.add(au)
            ug.add(self.up_row)
            dp.add(ug)
            h = up.connect("changed", lambda *_: self._update_status())
            self.connect("closed", lambda *_: up.disconnect(h))
            self._update_status()

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
        if ep.state == "running" and self.app.backend.players():
            pid = ep.find_player_id(self.app.backend)
            p = self.app.backend.get_player(pid) if pid else None
            txt += (" · visible in Music Assistant" if p and p.available
                    else " · waiting for Music Assistant to connect")
        self.ep_status.set_subtitle(GLib.markup_escape_text(txt))

    def _ep_changed(self) -> None:
        self._ep_status()
        if self.app.endpoint.state != "running":
            self.fw.set_visible(False)
            return

        async def go():
            blocked = await asyncio.to_thread(firewall.check)
            self.fw.set_visible(blocked is not None)
            if blocked:
                name, self._fw_cmd = blocked
                self.fw_row.set_subtitle(GLib.markup_escape_text(
                    f"Music Assistant connects to this PC on port {firewall.SENDSPIN_PORT}, which {name} drops. "
                    f"Open it to your local network with:\n{self._fw_cmd}"))

        asyncio.ensure_future(go())

    def _update_status(self) -> None:
        up = self.app.updater
        self.up_row.set_subtitle(GLib.markup_escape_text(up.describe()))
        label, style = {"ready": ("Restart", "suggested-action"), "available": ("Update", "suggested-action"),
                        "checking": ("Checking…", ""), "downloading": ("Downloading…", "")}.get(
            up.state, ("Check now", ""))
        self.up_btn.set_label(label)
        self.up_btn.set_sensitive(up.state not in ("checking", "downloading"))
        for c in ("suggested-action",):
            (self.up_btn.add_css_class if c == style else self.up_btn.remove_css_class)(c)

    def _update_clicked(self, _b) -> None:
        up = self.app.updater
        if up.state == "ready":
            self.close()
            self.app.restart_for_update()
        elif up.state == "available":
            up.install()
        else:
            up.check()

    def _copy_fw_cmd(self, *_):
        self.get_clipboard().set(self._fw_cmd)
        self.add_toast(Adw.Toast(title="Command copied — run it in a terminal"))

    def _webapp_expander(self) -> Adw.ExpanderRow:
        b = self.app.backend
        expander = Adw.ExpanderRow(title="Music Assistant web app")
        entries = [("Open web app", ""), ("Server settings", webapp.ROUTE_SETTINGS),
                   ("Providers", webapp.ROUTE_PROVIDERS), ("Users", webapp.ROUTE_USERS),
                   ("Statistics", webapp.ROUTE_STATISTICS)]
        if b.client and any(p.domain == "party" for p in b.client.providers):
            entries.append(("Party mode", webapp.ROUTE_PARTY))
        for title, route in entries:
            row = Adw.ActionRow(title=title, activatable=True)
            row.add_suffix(Gtk.Image.new_from_icon_name("external-link-symbolic"))
            row.connect("activated", lambda _r, route=route: links.open_webapp(self, b, route))
            expander.add_row(row)
        return expander

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
