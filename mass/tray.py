"""System tray icon (StatusNotifierItem + com.canonical.dbusmenu) for DMS / Waybar / KDE bars.

- left click: show / hide the window
- middle click: play / pause
- scroll: volume of the active player
- right click: menu with now playing, transport, show/hide, quit
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

from . import APP_ID, APP_NAME  # noqa: E402
from .host import IN_FLATPAK, data_file  # noqa: E402

log = logging.getLogger(__name__)

ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
ICON_FILE = data_file("icons", f"{APP_ID}.svg") or Path(f"/app/share/icons/hicolor/scalable/apps/{APP_ID}.svg")

SNI_XML = """
<node><interface name="org.kde.StatusNotifierItem">
  <property name="Category" type="s" access="read"/>
  <property name="Id" type="s" access="read"/>
  <property name="Title" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="WindowId" type="i" access="read"/>
  <property name="IconName" type="s" access="read"/>
  <property name="IconPixmap" type="a(iiay)" access="read"/>
  <property name="IconThemePath" type="s" access="read"/>
  <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
  <property name="ItemIsMenu" type="b" access="read"/>
  <property name="Menu" type="o" access="read"/>
  <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
  <signal name="NewTitle"/><signal name="NewIcon"/><signal name="NewToolTip"/>
  <signal name="NewStatus"><arg name="status" type="s"/></signal>
</interface></node>"""

MENU_XML = """
<node><interface name="com.canonical.dbusmenu">
  <property name="Version" type="u" access="read"/>
  <property name="TextDirection" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="IconThemePath" type="as" access="read"/>
  <method name="GetLayout">
    <arg type="i" name="parentId" direction="in"/><arg type="i" name="recursionDepth" direction="in"/>
    <arg type="as" name="propertyNames" direction="in"/>
    <arg type="u" name="revision" direction="out"/><arg type="(ia{sv}av)" name="layout" direction="out"/>
  </method>
  <method name="GetGroupProperties">
    <arg type="ai" name="ids" direction="in"/><arg type="as" name="propertyNames" direction="in"/>
    <arg type="a(ia{sv})" name="properties" direction="out"/>
  </method>
  <method name="GetProperty">
    <arg type="i" name="id" direction="in"/><arg type="s" name="name" direction="in"/>
    <arg type="v" name="value" direction="out"/>
  </method>
  <method name="Event">
    <arg type="i" name="id" direction="in"/><arg type="s" name="eventId" direction="in"/>
    <arg type="v" name="data" direction="in"/><arg type="u" name="timestamp" direction="in"/>
  </method>
  <method name="EventGroup">
    <arg type="a(isvu)" name="events" direction="in"/><arg type="ai" name="idErrors" direction="out"/>
  </method>
  <method name="AboutToShow">
    <arg type="i" name="id" direction="in"/><arg type="b" name="needUpdate" direction="out"/>
  </method>
  <method name="AboutToShowGroup">
    <arg type="ai" name="ids" direction="in"/>
    <arg type="ai" name="updatesNeeded" direction="out"/><arg type="ai" name="idErrors" direction="out"/>
  </method>
  <signal name="ItemsPropertiesUpdated">
    <arg type="a(ia{sv})" name="updatedProps"/><arg type="a(ias)" name="removedProps"/>
  </signal>
  <signal name="LayoutUpdated"><arg type="u" name="revision"/><arg type="i" name="parent"/></signal>
</interface></node>"""


def _pixmaps() -> list[tuple[int, int, bytes]]:
    """ARGB32 (network byte order) pixmaps of the app icon at a few sizes."""
    out = []
    for size in (22, 32, 48, 64):
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(str(ICON_FILE), size, size)
        except GLib.Error:
            return []
        if not pb.get_has_alpha():
            pb = pb.add_alpha(False, 0, 0, 0)
        w, h, stride = pb.get_width(), pb.get_height(), pb.get_rowstride()
        src = pb.get_pixels()
        argb = bytearray(w * h * 4)
        for y in range(h):
            row = src[y * stride: y * stride + w * 4]
            for x in range(w):
                r, g, b, a = row[x * 4: x * 4 + 4]
                i = (y * w + x) * 4
                argb[i: i + 4] = bytes((a, r, g, b))
        out.append((w, h, bytes(argb)))
    return out


class Tray:
    def __init__(self, app, backend) -> None:
        self.app, self.backend = app, backend
        self._conn: Gio.DBusConnection | None = None
        self._owner = 0
        self._regs: list[int] = []
        self._revision = 1
        self._pixmaps = _pixmaps()
        self._sni = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        self._menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self._bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._watch = 0
        for sig in ("player-updated", "queue-updated", "active-player-changed", "status-changed"):
            backend.connect(sig, lambda *_: self._changed())
        self._pending = False

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._owner or self._conn:
            return
        if IN_FLATPAK:
            # the sandbox can't own org.kde.StatusNotifierItem-<pid>-1; hosts accept the unique name
            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._bus_name = conn.get_unique_name()
            self._on_acquired(conn, self._bus_name)
            return
        self._owner = Gio.bus_own_name(Gio.BusType.SESSION, self._bus_name, Gio.BusNameOwnerFlags.NONE,
                                       self._on_acquired, None, None)

    def stop(self) -> None:
        if self._conn:
            for r in self._regs:
                self._conn.unregister_object(r)
        self._regs.clear()
        if self._watch:
            Gio.bus_unwatch_name(self._watch)
            self._watch = 0
        if self._owner:
            Gio.bus_unown_name(self._owner)
        self._owner = 0
        self._conn = None

    def _on_acquired(self, conn, _name) -> None:
        self._conn = conn
        self._regs.append(conn.register_object(ITEM_PATH, self._sni, self._sni_call, self._sni_get, None))
        self._regs.append(conn.register_object(MENU_PATH, self._menu, self._menu_call, self._menu_get, None))
        # (re)register whenever a watcher appears, e.g. after the shell restarts
        self._watch = Gio.bus_watch_name(Gio.BusType.SESSION, "org.kde.StatusNotifierWatcher",
                                         Gio.BusNameWatcherFlags.NONE, self._register, None)

    def _register(self, conn, _name, _owner) -> None:
        conn.call("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher", "org.kde.StatusNotifierWatcher",
                  "RegisterStatusNotifierItem", GLib.Variant("(s)", (self._bus_name,)), None,
                  Gio.DBusCallFlags.NONE, -1, None, self._registered)

    def _registered(self, conn, res) -> None:
        try:
            conn.call_finish(res)
        except GLib.Error as err:
            log.warning("tray registration failed: %s", err.message)

    # ------------------------------------------------------------------ state

    def _track(self) -> tuple[str, str, bool]:
        from .ui.nowplaying import is_playing, track_info

        title, artist, _al, _d, _s = track_info(self.backend)
        return title, artist, is_playing(self.backend)

    def _changed(self) -> None:
        if self._pending or not self._conn:
            return
        self._pending = True

        def emit():
            self._pending = False
            if not self._conn:
                return False
            self._conn.emit_signal(None, ITEM_PATH, "org.kde.StatusNotifierItem", "NewToolTip", None)
            self._revision += 1
            self._conn.emit_signal(None, MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                                   GLib.Variant("(ui)", (self._revision, 0)))
            return False

        GLib.timeout_add(300, emit)

    def _sni_get(self, _c, _s, _p, _i, prop):
        title, artist, _playing = self._track()
        p = self.backend.active_player
        tip_body = " — ".join(x for x in (artist, p.name if p else "") if x)
        return {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "mass"),
            "Title": GLib.Variant("s", APP_NAME),
            "Status": GLib.Variant("s", "Active"),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", APP_ID if not self._pixmaps else ""),
            "IconPixmap": GLib.Variant("a(iiay)", self._pixmaps),
            "IconThemePath": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [], title, tip_body)),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
        }.get(prop)

    def _sni_call(self, _c, _s, _p, _i, method, params, inv) -> None:
        b = self.backend
        if method == "Activate":
            self.toggle_window()
        elif method == "SecondaryActivate":
            b.play_pause()
        elif method == "Scroll":
            delta, orientation = params.unpack()
            if orientation.lower() == "vertical" and (p := b.active_player):
                step = 5 if delta < 0 else -5  # wheel up -> negative delta on most hosts
                b.set_volume((p.volume_level or 0) + step)
        inv.return_value(None)

    def toggle_window(self) -> None:
        w = self.app.window
        if w.get_visible() and w.is_active():
            w.set_visible(False)
        else:
            w.present()

    # ------------------------------------------------------------------ dbusmenu

    def _items(self) -> list[tuple[int, dict]]:
        title, artist, playing = self._track()
        w = self.app.window
        now = f"{title} — {artist}" if artist else title
        return [
            (1, {"label": now[:60], "enabled": False}),
            (2, {"type": "separator"}),
            (3, {"label": "Pause" if playing else "Play", "icon-name": "media-playback-pause-symbolic"
                 if playing else "media-playback-start-symbolic"}),
            (4, {"label": "Next", "icon-name": "media-skip-forward-symbolic"}),
            (5, {"label": "Previous", "icon-name": "media-skip-backward-symbolic"}),
            (6, {"type": "separator"}),
            (7, {"label": "Hide Mass" if w.get_visible() else "Show Mass"}),
            (8, {"label": "Quit", "icon-name": "application-exit-symbolic"}),
        ]

    @staticmethod
    def _props(d: dict) -> dict[str, GLib.Variant]:
        out = {}
        for k, v in d.items():
            out[k] = GLib.Variant("b", v) if isinstance(v, bool) else GLib.Variant("s", v)
        return out

    def _layout(self):
        children = [GLib.Variant("(ia{sv}av)", (i, self._props(p), [])) for i, p in self._items()]
        return (0, {"children-display": GLib.Variant("s", "submenu")}, children)

    def _menu_get(self, _c, _s, _p, _i, prop):
        return {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }.get(prop)

    def _menu_call(self, _c, _s, _p, _i, method, params, inv) -> None:
        if method == "GetLayout":
            inv.return_value(GLib.Variant("(u(ia{sv}av))", (self._revision, self._layout())))
        elif method == "GetGroupProperties":
            ids, _names = params.unpack()
            items = dict(self._items())
            res = [(i, self._props(items[i])) for i in ids if i in items]
            inv.return_value(GLib.Variant("(a(ia{sv}))", (res,)))
        elif method == "GetProperty":
            i, name = params.unpack()
            val = dict(self._items()).get(i, {}).get(name, "")
            inv.return_value(GLib.Variant("(v)", (GLib.Variant("b", val) if isinstance(val, bool)
                                                  else GLib.Variant("s", str(val)),)))
        elif method == "Event":
            i, event, _data, _ts = params.unpack()
            if event == "clicked":
                GLib.idle_add(lambda: (self._clicked(i), False)[1])
            inv.return_value(None)
        elif method == "EventGroup":
            (events,) = params.unpack()
            for i, event, _d, _t in events:
                if event == "clicked":
                    GLib.idle_add(lambda i=i: (self._clicked(i), False)[1])
            inv.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            inv.return_value(GLib.Variant("(b)", (True,)))
        elif method == "AboutToShowGroup":
            inv.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            inv.return_value(None)

    def _clicked(self, i: int) -> None:
        b = self.backend
        {3: b.play_pause, 4: b.next, 5: b.previous, 7: self.toggle_window, 8: self.app.request_quit}.get(i, lambda: None)()
        self._changed()
