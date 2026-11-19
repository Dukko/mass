"""MPRIS2 server mirroring the *active* Music Assistant player.

This is what makes media keys, playerctl, DMS/Quickshell media widgets, KDE Connect,
lock screens etc. control whichever MA player is selected in the app.
"""

from __future__ import annotations

import logging
from pathlib import Path

from gi.repository import Gio, GLib
from music_assistant_models.enums import PlaybackState, PlayerFeature, RepeatMode

from . import APP_ID, APP_NAME
from .config import CACHE_DIR

log = logging.getLogger(__name__)

BUS_NAME = "org.mpris.MediaPlayer2.mass"
OBJ_PATH = "/org/mpris/MediaPlayer2"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
COVER = CACHE_DIR / "mpris-cover"

XML = """
<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/><method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Next"/><method name="Previous"/><method name="Pause"/>
    <method name="PlayPause"/><method name="Stop"/><method name="Play"/>
    <method name="Seek"><arg direction="in" name="Offset" type="x"/></method>
    <method name="SetPosition">
      <arg direction="in" name="TrackId" type="o"/><arg direction="in" name="Position" type="x"/>
    </method>
    <method name="OpenUri"><arg direction="in" name="Uri" type="s"/></method>
    <signal name="Seeked"><arg name="Position" type="x"/></signal>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="LoopStatus" type="s" access="readwrite"/>
    <property name="Rate" type="d" access="readwrite"/>
    <property name="Shuffle" type="b" access="readwrite"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>
"""

ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"


class MprisServer:
    def __init__(self, app, backend) -> None:
        self.app = app
        self.backend = backend
        self._conn: Gio.DBusConnection | None = None
        self._reg_ids: list[int] = []
        self._owner_id = 0
        self._last: dict[str, GLib.Variant] = {}
        self._last_pos_us = 0
        self._last_pos_mono = 0.0
        self._cover_url: str | None = None
        self._cover_path: str | None = None
        self._node = Gio.DBusNodeInfo.new_for_xml(XML)

        for sig in ("player-updated", "queue-updated", "queue-time-updated", "active-player-changed",
                    "status-changed"):
            backend.connect(sig, lambda *_: self.refresh())

    # ------------------------------------------------------------------ bus lifecycle

    def start(self) -> None:
        if self._owner_id:
            return
        self._owner_id = Gio.bus_own_name(
            Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired, None, lambda *_: log.warning("lost MPRIS bus name"),
        )

    def stop(self) -> None:
        if self._conn:
            for rid in self._reg_ids:
                self._conn.unregister_object(rid)
        self._reg_ids.clear()
        if self._owner_id:
            Gio.bus_unown_name(self._owner_id)
        self._owner_id = 0
        self._conn = None

    def _on_bus_acquired(self, conn, _name) -> None:
        self._conn = conn
        for iface in self._node.interfaces:
            self._reg_ids.append(
                conn.register_object(OBJ_PATH, iface, self._on_call, self._on_get, self._on_set)
            )
        self.refresh()

    # ------------------------------------------------------------------ properties

    def _props(self) -> dict[str, dict[str, GLib.Variant]]:
        b = self.backend
        player, queue = b.active_player, b.active_queue
        connected = b.connected and player is not None
        state = (queue.state if queue else player.playback_state) if player else PlaybackState.IDLE
        status = {PlaybackState.PLAYING: "Playing", PlaybackState.PAUSED: "Paused"}.get(state, "Stopped")
        loop = "None"
        if queue:
            loop = {RepeatMode.ONE: "Track", RepeatMode.ALL: "Playlist"}.get(queue.repeat_mode, "None")
        has_item = bool(queue and queue.current_item) or bool(player and player.current_media)

        def has_feature(f: PlayerFeature) -> bool:
            return bool(player and f in (player.supported_features or ()))

        if queue:
            can_seek = bool(queue.current_item and queue.current_item.duration)
        else:
            can_seek = has_feature(PlayerFeature.SEEK) and bool(player and player.current_media and player.current_media.duration)
        return {
            ROOT_IFACE: {
                "CanQuit": GLib.Variant("b", True),
                "CanRaise": GLib.Variant("b", True),
                "HasTrackList": GLib.Variant("b", False),
                "Identity": GLib.Variant("s", f"{APP_NAME}: {player.name}" if player else APP_NAME),
                "DesktopEntry": GLib.Variant("s", APP_ID),
                "SupportedUriSchemes": GLib.Variant("as", ["http", "https", "library", "spotify", "tidal"]),
                "SupportedMimeTypes": GLib.Variant("as", []),
            },
            PLAYER_IFACE: {
                "PlaybackStatus": GLib.Variant("s", status),
                "LoopStatus": GLib.Variant("s", loop),
                "Rate": GLib.Variant("d", 1.0),
                "Shuffle": GLib.Variant("b", bool(queue and queue.shuffle_enabled)),
                "Metadata": GLib.Variant("a{sv}", self._metadata()),
                "Volume": GLib.Variant("d", ((player.volume_level or 0) / 100) if player else 0.0),
                "Position": GLib.Variant("x", int(b.elapsed(queue) * 1_000_000) if player else 0),
                "MinimumRate": GLib.Variant("d", 1.0),
                "MaximumRate": GLib.Variant("d", 1.0),
                "CanGoNext": GLib.Variant("b", connected and (queue is not None or has_feature(PlayerFeature.NEXT_PREVIOUS))),
                "CanGoPrevious": GLib.Variant("b", connected and (queue is not None or has_feature(PlayerFeature.NEXT_PREVIOUS))),
                "CanPlay": GLib.Variant("b", connected and has_item),
                "CanPause": GLib.Variant("b", connected and has_item),
                "CanSeek": GLib.Variant("b", connected and can_seek),
                "CanControl": GLib.Variant("b", connected),
            },
        }

    def _metadata(self) -> dict[str, GLib.Variant]:
        b = self.backend
        player, queue = b.active_player, b.active_queue
        item = queue.current_item if queue else None
        md: dict[str, GLib.Variant] = {}
        if item:
            tid = "/io/github/mass/track/" + "".join(c if c.isalnum() else "_" for c in item.queue_item_id)
            md["mpris:trackid"] = GLib.Variant("o", tid)
            if item.duration:
                md["mpris:length"] = GLib.Variant("x", int(item.duration * 1_000_000))
            mi = item.media_item
            title = getattr(mi, "name", None) or item.name
            md["xesam:title"] = GLib.Variant("s", title)
            artists = [a.name for a in getattr(mi, "artists", None) or []]
            if artists:
                md["xesam:artist"] = GLib.Variant("as", artists)
            if (album := getattr(mi, "album", None)) is not None:
                md["xesam:album"] = GLib.Variant("s", album.name)
            md["xesam:url"] = GLib.Variant("s", getattr(mi, "uri", "") or "")
            url = b.image_url(item, 512)
        elif player and player.current_media:
            cm = player.current_media
            md["mpris:trackid"] = GLib.Variant("o", "/io/github/mass/track/external")
            md["xesam:title"] = GLib.Variant("s", cm.title or cm.uri)
            if cm.artist:
                md["xesam:artist"] = GLib.Variant("as", [cm.artist])
            if cm.album:
                md["xesam:album"] = GLib.Variant("s", cm.album)
            if cm.duration:
                md["mpris:length"] = GLib.Variant("x", int(cm.duration * 1_000_000))
            url = cm.image_url
        else:
            md["mpris:trackid"] = GLib.Variant("o", NO_TRACK)
            url = None
        if url:
            self._ensure_cover(url)
            if self._cover_url == url and self._cover_path:
                md["mpris:artUrl"] = GLib.Variant("s", Path(self._cover_path).as_uri())
        return md

    def _ensure_cover(self, url: str) -> None:
        """Download the cover to a local file: imageproxy URLs need auth, and many
        MPRIS consumers only handle file:// art reliably."""
        if url == self._cover_url:
            return
        self._cover_url, self._cover_path = url, None

        async def fetch():
            data = await self.backend.fetch_image(url)
            if data and self._cover_url == url:
                COVER.parent.mkdir(parents=True, exist_ok=True)
                # unique name so consumers notice the change
                path = COVER.with_name(f"mpris-cover-{abs(hash(url)) % 10**8}")
                for old in COVER.parent.glob("mpris-cover-*"):
                    if old != path:
                        old.unlink(missing_ok=True)
                path.write_bytes(data)
                self._cover_path = str(path)
                self.refresh()

        self.backend.run(fetch())

    def refresh(self) -> None:
        if not self._conn:
            return
        props = self._props()
        for iface, values in props.items():
            changed = {}
            for k, v in values.items():
                if k == "Position":
                    continue
                key = f"{iface}.{k}"
                if self._last.get(key) is None or not self._last[key].equal(v):
                    changed[k] = v
                    self._last[key] = v
            if changed:
                self._conn.emit_signal(
                    None, OBJ_PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                    GLib.Variant("(sa{sv}as)", (iface, changed, [])),
                )
        # Seeked detection: position jumped relative to linear progression
        pos = props[PLAYER_IFACE]["Position"].unpack()
        now = GLib.get_monotonic_time()
        playing = props[PLAYER_IFACE]["PlaybackStatus"].unpack() == "Playing"
        expected = self._last_pos_us + ((now - self._last_pos_mono) if playing else 0)
        if self._last_pos_mono and abs(pos - expected) > 2_000_000:
            self._conn.emit_signal(None, OBJ_PATH, PLAYER_IFACE, "Seeked", GLib.Variant("(x)", (pos,)))
        self._last_pos_us, self._last_pos_mono = pos, now

    # ------------------------------------------------------------------ handlers

    def _on_get(self, _conn, _sender, _path, iface, prop):
        return self._props().get(iface, {}).get(prop)

    def _on_set(self, _conn, _sender, _path, iface, prop, value):
        b = self.backend
        v = value.unpack()
        if prop == "Volume":
            b.set_volume(round(max(0.0, min(1.0, v)) * 100))
        elif prop == "Shuffle":
            b.set_shuffle(bool(v))
        elif prop == "LoopStatus":
            b.set_repeat({"Track": RepeatMode.ONE, "Playlist": RepeatMode.ALL}.get(v, RepeatMode.OFF))
        return True

    def _on_call(self, _conn, _sender, _path, iface, method, params, invocation):
        b = self.backend
        args = params.unpack()
        match method:
            case "Raise":
                self.app.activate()
            case "Quit":
                self.app.quit()
            case "Next":
                b.next()
            case "Previous":
                b.previous()
            case "Pause":
                b.pause()
            case "Play":
                b.play()
            case "PlayPause":
                b.play_pause()
            case "Stop":
                b.stop_playback()
            case "Seek":
                b.seek(max(0.0, b.elapsed() + args[0] / 1_000_000))
            case "SetPosition":
                b.seek(max(0.0, args[1] / 1_000_000))
            case "OpenUri":
                b.play_media(args[0])
        invocation.return_value(None)
