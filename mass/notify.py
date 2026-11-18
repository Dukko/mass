"""Track notifications straight to org.freedesktop.Notifications.

GNotification goes through xdg-desktop-portal, which names host apps after their
installed .desktop file (a dev run shows up as "__main__.py") and drops in-memory
cover art. Talking to the notification server directly gives us the app name,
a file-backed cover, actions, and in-place replacement.
"""

from __future__ import annotations

import logging

from gi.repository import Gio, GLib

from . import APP_ID, APP_NAME
from .config import CACHE_DIR

log = logging.getLogger(__name__)

COVER = CACHE_DIR / "notify-cover"


class Notifier:
    def __init__(self, on_action) -> None:
        self.on_action = on_action  # callable(action_key)
        self._bus: Gio.DBusConnection | None = None
        self._last_id = 0
        self._other: dict[int, object] = {}  # notification id -> callback(action_key)
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._bus.signal_subscribe(
                "org.freedesktop.Notifications", "org.freedesktop.Notifications", "ActionInvoked",
                "/org/freedesktop/Notifications", None, Gio.DBusSignalFlags.NONE, self._on_signal)
        except GLib.Error as err:
            log.warning("no session bus for notifications: %s", err.message)

    def _on_signal(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        nid, key = params.unpack()
        if nid == self._last_id:
            self.on_action(key)
        elif cb := self._other.pop(nid, None):
            cb(key)

    def message(self, title: str, body: str, actions: list[tuple[str, str]], on_action) -> None:
        """A persistent app notification (not the track one), e.g. "update ready"."""
        if not self._bus:
            return
        hints = {"desktop-entry": GLib.Variant("s", APP_ID), "urgency": GLib.Variant("y", 1)}
        flat = [x for pair in actions for x in pair]

        def sent(conn, res):
            try:
                (nid,) = conn.call_finish(res).unpack()
                self._other[nid] = on_action
            except GLib.Error as err:
                log.debug("notify failed: %s", err.message)

        self._bus.call(
            "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications", "Notify",
            GLib.Variant("(susssasa{sv}i)", (APP_NAME, 0, APP_ID, title, GLib.markup_escape_text(body),
                                             flat, hints, 0)),
            GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, -1, None, sent)

    def show(self, title: str, body: str, cover: bytes | None) -> None:
        if not self._bus:
            return
        hints = {
            "desktop-entry": GLib.Variant("s", APP_ID),
            "category": GLib.Variant("s", "x-gnome.music"),
            "urgency": GLib.Variant("y", 0),
            "transient": GLib.Variant("b", True),
        }
        icon = APP_ID
        if cover:
            COVER.parent.mkdir(parents=True, exist_ok=True)
            COVER.write_bytes(cover)
            hints["image-path"] = GLib.Variant("s", COVER.as_uri())
            icon = str(COVER)
        actions = ["default", "Open", "previous", "Previous", "next", "Next"]
        self._bus.call(
            "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications", "Notify",
            GLib.Variant("(susssasa{sv}i)", (APP_NAME, self._last_id, icon, title,
                                             GLib.markup_escape_text(body), actions, hints, 6000)),
            GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, -1, None, self._on_sent)

    def _on_sent(self, conn, res) -> None:
        try:
            (self._last_id,) = conn.call_finish(res).unpack()
        except GLib.Error as err:
            log.debug("notify failed: %s", err.message)
