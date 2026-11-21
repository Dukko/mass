"""Music Assistant connection, exposed to the UI as a GObject with signals.

All MA traffic goes through here. Everything runs on the GLib-backed asyncio loop,
so signal handlers can touch widgets directly.
"""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.parse
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import aiohttp
from gi.repository import GLib, GObject
from music_assistant_client import MusicAssistantClient
from music_assistant_client.auth_helpers import login_with_token
from music_assistant_client.exceptions import CannotConnect
from music_assistant_models.errors import AuthenticationFailed, AuthenticationRequired
from music_assistant_models.enums import EventType, MediaType, PlaybackState, RepeatMode
from music_assistant_models.event import MassEvent
from music_assistant_models.media_items import Playlist
from music_assistant_models.player import Player
from music_assistant_models.player_queue import PlayerQueue
from music_assistant_models.queue_item import QueueItem

from . import APP_NAME, secrets

log = logging.getLogger(__name__)


# media types the library can favorite
FAVORITABLE = {MediaType.TRACK, MediaType.ALBUM, MediaType.ARTIST, MediaType.PLAYLIST, MediaType.RADIO,
               MediaType.AUDIOBOOK, MediaType.PODCAST, MediaType.PODCAST_EPISODE}
IMAGE_RETRY_AFTER = 300  # seconds before retrying an image that failed to load
PLAYLIST_PAGE = 500  # library playlists fetched per request
IMAGE_CACHE_MAX = 400 # covers kept decoded in memory; oldest used is dropped first
IMAGE_PROXY_SIZES = (0, 80, 160, 256, 512, 1024)  # the only sizes the server's proxy accepts


def _proxy_size(size: int) -> int:
    """Round a requested cover size up to one the server's image proxy supports."""
    if size <= 0:
        return 0
    return next((s for s in IMAGE_PROXY_SIZES[1:] if s >= size), IMAGE_PROXY_SIZES[-1])


# Server schemas that introduced optional commands (checked against server_info.schema_version).
SLEEP_TIMER_SCHEMA = 34  # players/sleep_timer/*
SOUND_EFFECTS_SCHEMA = 35  # music/sound_effects
OVERLAY_SCHEMA = 35  # player_queues/overlay
ANNOUNCEMENT_SCHEMA = 28  # players/cmd/play_announcement (older than our minimum)

class Status:
    IDLE = "idle"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    AUTH_REQUIRED = "auth-required"


class Backend(GObject.Object):
    __gsignals__ = {
        "status-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "players-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "player-updated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "queue-updated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "queue-items-updated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "queue-time-updated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "active-player-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "ai-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, config) -> None:
        super().__init__()
        self.config = config
        self.client: MusicAssistantClient | None = None
        self.status = Status.IDLE
        self.status_detail = ""
        self._session: aiohttp.ClientSession | None = None
        self._runner: asyncio.Task | None = None
        self._tasks: set[asyncio.Future] = set()
        self._token: str | None = None
        self._image_cache: OrderedDict[str, bytes] = OrderedDict()
        self._image_failed: dict[str, float] = {}  # url -> monotonic time to retry after
        self.dj_hosts: dict[str, dict] = {}
        self._lyrics: dict[str, tuple[str | None, str | None]] = {}
        self._sound_effects: list[dict] | None = None
        self.dj_map: dict[str, str] = {}

    # ------------------------------------------------------------------ lifecycle

    def _set_status(self, status: str, detail: str = "") -> None:
        self.status, self.status_detail = status, detail
        self.emit("status-changed", status, detail)

    def start(self) -> None:
        """(Re)start the connection loop using the configured server + stored token."""
        if self._runner and not self._runner.done():
            self._runner.cancel()
        if not self.config.server_url:
            self._set_status(Status.AUTH_REQUIRED, "No server configured")
            return
        self._token = secrets.load_token(self.config.server_url)
        if not self._token:
            self._set_status(Status.AUTH_REQUIRED, "Sign in to your Music Assistant server")
            return
        self._runner = asyncio.ensure_future(self._run())

    async def stop(self) -> None:
        if self._runner:
            self._runner.cancel()
            await asyncio.gather(self._runner, return_exceptions=True)
        if self._session:
            await self._session.close()

    async def login(self, server_url: str, username: str, password: str) -> None:
        """Exchange credentials for a long-lived token and persist it. Raises on failure."""
        server_url = server_url.rstrip("/")
        session = await self._get_session()
        _user, token = await login_with_token(
            server_url, username, password, token_name=f"{APP_NAME} ({GLib.get_host_name()})",
            aiohttp_session=session,
        )
        if not secrets.store_token(server_url, token):
            raise RuntimeError("Could not store the token in the Secret Service (is a keyring running?)")
        self.config.server_url = server_url
        self.config.username = username
        self.config.save()
        self.start()

    async def logout(self) -> None:
        if self._runner:
            self._runner.cancel()
        if self.client:
            await self.client.disconnect()
            self.client = None
        secrets.clear_token(self.config.server_url)
        self._set_status(Status.AUTH_REQUIRED, "Signed out")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # few, reused connections: every open socket costs time on each main-loop pass
            # (PyGObject's asyncio bridge polls them all), and covers come from one server anyway
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit_per_host=10, keepalive_timeout=15))
        return self._session

    async def _run(self) -> None:
        delay = 1.0
        while True:
            session = await self._get_session()
            self._set_status(Status.CONNECTING, self.config.server_url)
            client = MusicAssistantClient(self.config.server_url, session, token=self._token)
            ready = asyncio.Event()
            listen = asyncio.ensure_future(client.start_listening(ready))
            ready_wait = asyncio.ensure_future(ready.wait())
            try:
                try:
                    done, _ = await asyncio.wait(
                        {listen, ready_wait},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    ready_wait.cancel()
                if listen in done:
                    listen.result()  # raises the connect error
                    raise CannotConnect(Exception("connection closed"))
                self.client = client
                client.subscribe(self._on_event)
                delay = 1.0
                info = client.server_info
                self._set_status(Status.CONNECTED, f"Music Assistant {info.server_version}")
                self.emit("players-changed")
                self.emit("active-player-changed")
                self.run(self.refresh_ai())
                await listen  # returns when the socket closes
                raise CannotConnect(Exception("connection lost"))
            except asyncio.CancelledError:
                listen.cancel()
                await self._disconnect(client)
                raise
            except (AuthenticationFailed, AuthenticationRequired) as err:
                log.warning("Auth failed: %s", err)
                self._drop(client)
                self._set_status(Status.AUTH_REQUIRED, "Token rejected, sign in again")
                return
            except Exception as err:  # noqa: BLE001 - keep reconnecting on anything
                log.info("Disconnected (%s), retrying in %.0fs", err, delay)
                self._drop(client)
                self._set_status(Status.DISCONNECTED, str(err) or type(err).__name__)
                listen.cancel()
                await self._disconnect(client)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    def _drop(self, client: MusicAssistantClient) -> None:
        if self.client is client:
            self.client = None

    async def _disconnect(self, client: MusicAssistantClient) -> None:
        self._drop(client)
        try:
            await client.disconnect()
        except Exception as err:  # noqa: BLE001 - best-effort cleanup of a dead connection
            log.debug("client disconnect failed: %s", err)

    # ------------------------------------------------------------------ events

    def _on_event(self, event: MassEvent) -> None:
        et, oid = event.event, event.object_id or ""
        if et in (EventType.PLAYER_ADDED, EventType.PLAYER_REMOVED):
            self.emit("players-changed")
        elif et == EventType.PLAYER_UPDATED:
            self.emit("player-updated", oid)
        elif et in (EventType.QUEUE_ADDED, EventType.QUEUE_UPDATED):
            self.emit("queue-updated", oid)
        elif et == EventType.QUEUE_ITEMS_UPDATED:
            self.emit("queue-items-updated", oid)
        elif et == EventType.QUEUE_TIME_UPDATED:
            self.emit("queue-time-updated", oid)

    # ------------------------------------------------------------------ state helpers

    @property
    def connected(self) -> bool:
        return self.client is not None and self.status == Status.CONNECTED

    def players(self) -> list[Player]:
        if not self.client:
            return []
        items = [p for p in self.client.players.players if p.enabled and not p.hide_in_ui]
        return sorted(items, key=lambda p: (not p.available, p.name.lower()))

    def get_player(self, player_id: str | None = None) -> Player | None:
        if not self.client:
            return None
        return self.client.players.get(player_id or self.config.active_player_id)

    @property
    def active_player(self) -> Player | None:
        player = self.get_player()
        if player is None and (players := self.players()):
            # fall back to whatever is playing, else the first available player
            player = next((p for p in players if p.playback_state == PlaybackState.PLAYING), players[0])
            self.config.active_player_id = player.player_id
        return player

    def set_active_player(self, player_id: str) -> None:
        if player_id == self.config.active_player_id:
            return
        self.config.active_player_id = player_id
        self.config.save()
        self.emit("active-player-changed")

    def queue_for(self, player: Player | None) -> PlayerQueue | None:
        if not self.client or not player:
            return None
        queues = self.client.player_queues
        # a grouped/synced player plays from its leader's queue
        leader_id = player.active_group or player.synced_to or player.player_id
        leader = self.client.players.get(leader_id) or player
        q = None
        for qid in (leader.active_source, leader_id, player.player_id):
            if qid and (q := queues.get(qid)):
                break
        if q is None:
            return None
        cm = leader.current_media
        if cm and q.current_item and cm.queue_item_id is None and cm.uri != getattr(q.current_item, "uri", None):
            # playing something MA didn't queue (Spotify Connect, AirPlay, the device's own app):
            # the MA queue is stale, so callers fall back to player.current_media.
            # (Some devices, e.g. WiiM, report their own id as source even while MA streams.)
            return None
        return q

    @property
    def active_queue(self) -> PlayerQueue | None:
        return self.queue_for(self.active_player)

    def supports(self, schema: int) -> bool:
        """Whether the connected server is new enough for an optional command."""
        info = getattr(self.client, "server_info", None) if self.client else None
        return info is not None and info.schema_version >= schema

    def is_relevant(self, object_id: str) -> bool:
        """Whether an event object id affects the active player/queue."""
        player = self.active_player
        if not player:
            return False
        q = self.active_queue
        return object_id in {
            player.player_id,
            player.active_group,
            player.synced_to,
            q.queue_id if q else None,
        }

    def elapsed(self, queue: PlayerQueue | None = None) -> float:
        """Interpolated elapsed time of the active queue."""
        q = queue or self.active_queue
        if not q:
            # external source: interpolate from the player itself
            p = self.active_player
            if not p or p.elapsed_time is None:
                return 0.0
            base = p.elapsed_time
            if p.playback_state == PlaybackState.PLAYING and p.elapsed_time_last_updated:
                base += time.time() - p.elapsed_time_last_updated
            dur = p.current_media.duration if p.current_media else None
            return min(base, dur) if dur else base
        base = q.elapsed_time or 0.0
        if q.state == PlaybackState.PLAYING and q.elapsed_time_last_updated:
            base += (time.time() - q.elapsed_time_last_updated) * (q.playback_speed or 1.0)
        dur = q.current_item.duration if q.current_item else None
        return min(base, dur) if dur else base

    # ------------------------------------------------------------------ images

    def image_url(self, item: Any, size: int = 0) -> str | None:
        """Resolve an image url for a media item / queue item / item mapping.

        Unlike the client helper this never routes through third-party thumbnailers.
        """
        if not self.client or item is None:
            return None
        img = None
        if isinstance(item, QueueItem):
            img = item.image or (
                self.client.music.get_media_item_image(item.media_item) if item.media_item else None
            )
        else:
            img = self.client.music.get_media_item_image(item)
        if img is None:
            return None
        # server_info.base_url can omit the webserver port; the configured URL has it
        base = (self.config.server_url or self.client.server_info.base_url).rstrip("/")
        size = _proxy_size(size)
        if img.proxy_id:
            # Prefer the server's proxy: it has the image cached, honours size, and is one
            # authenticated connection instead of many slow third-party CDN requests.
            return f"{base}/imageproxy/{img.proxy_id}?size={size}"
        if img.remotely_accessible:
            return img.path
        enc = urllib.parse.quote(urllib.parse.quote(img.path))
        return f"{base}/imageproxy?path={enc}&provider={img.provider}&size={size}"

    async def fetch_image(self, url: str) -> bytes | None:
        if not url:
            return None
        if (data := self._image_cache.get(url)) is not None:
            self._image_cache.move_to_end(url)
            return data
        if self._image_failed.get(url, 0) > time.monotonic():
            return None  # failed recently; don't stall another 15 s on it
        session = await self._get_session()
        headers = {}
        if self._token and self.config.server_url and url.startswith(
            (self.config.server_url, self.client.server_info.base_url if self.client else "\0")
        ):
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    self._image_failed_later(url)
                    return None
                data = await r.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            log.debug("image fetch failed %s: %r", url, err)
            self._image_failed_later(url)
            return None
        self._image_cache[url] = data
        self._image_cache.move_to_end(url)
        while len(self._image_cache) > IMAGE_CACHE_MAX:
            self._image_cache.popitem(last=False)
        return data

    def _image_failed_later(self, url: str) -> None:
        """Remember a failed cover briefly, and forget failures that have expired."""
        now = time.monotonic()
        self._image_failed[url] = now + IMAGE_RETRY_AFTER
        if len(self._image_failed) > 2 * IMAGE_CACHE_MAX:
            self._image_failed = {u: t for u, t in self._image_failed.items() if t > now}

    # ------------------------------------------------------------------ commands

    def run(self, coro) -> None:
        """Fire-and-forget a command, logging failures."""
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)

        def done(t: asyncio.Task) -> None:
            self._tasks.discard(t)
            if not t.cancelled() and (err := t.exception()):
                log.error("command failed: %s", err)

        task.add_done_callback(done)

    def _qid(self) -> str | None:
        q = self.active_queue
        return q.queue_id if q else None

    def play_pause(self) -> None:
        if (qid := self._qid()) and self.client:
            self.run(self.client.player_queues.play_pause(qid))
        elif (p := self.active_player) and self.client:
            self.run(self.client.players.play_pause(p.player_id))

    def _player_id(self) -> str | None:
        p = self.active_player
        return p.player_id if p else None

    def play(self) -> None:
        if (qid := self._qid()) and self.client:
            self.run(self.client.player_queues.play(qid))
        elif (pid := self._player_id()) and self.client:
            self.run(self.client.players.play(pid))

    def pause(self) -> None:
        if (qid := self._qid()) and self.client:
            self.run(self.client.player_queues.pause(qid))
        elif (pid := self._player_id()) and self.client:
            self.run(self.client.players.pause(pid))

    def stop_playback(self) -> None:
        if (qid := self._qid()) and self.client:
            self.run(self.client.player_queues.stop(qid))
        elif (pid := self._player_id()) and self.client:
            self.run(self.client.players.stop(pid))

    def next(self) -> None:
        if (qid := self._qid()) and self.client:
            self.run(self.client.player_queues.next(qid))
        elif (pid := self._player_id()) and self.client:
            self.run(self.client.players.next_track(pid))

    def previous(self) -> None:
        if (qid := self._qid()) and self.client:
            self.run(self.client.player_queues.previous(qid))
        elif (pid := self._player_id()) and self.client:
            self.run(self.client.players.previous_track(pid))

    def seek(self, seconds: float) -> None:
        if (qid := self._qid()) and self.client:
            self.run(self.client.player_queues.seek(qid, int(seconds)))
        elif (pid := self._player_id()) and self.client:
            self.run(self.client.players.seek(pid, int(seconds)))

    def set_shuffle(self, enabled: bool) -> None:
        if (qid := self._qid()) and self.client:
            self.run(self.client.player_queues.shuffle(qid, enabled))

    def cycle_repeat(self) -> None:
        q = self.active_queue
        if q and self.client:
            order = [RepeatMode.OFF, RepeatMode.ALL, RepeatMode.ONE]
            nxt = order[(order.index(q.repeat_mode) + 1) % 3] if q.repeat_mode in order else RepeatMode.OFF
            self.run(self.client.player_queues.repeat(q.queue_id, nxt))

    def set_repeat(self, mode: RepeatMode) -> None:
        if (qid := self._qid()) and self.client:
            self.run(self.client.player_queues.repeat(qid, mode))

    def set_volume(self, level: int, player_id: str | None = None) -> None:
        p = self.get_player(player_id) if player_id else self.active_player
        if not (p and self.client):
            return
        level = max(0, min(100, int(level)))
        if p.group_members and len(p.group_members) > 1:
            self.run(self.client.players.group_volume(p.player_id, level))
        else:
            self.run(self.client.players.volume_set(p.player_id, level))

    def set_mute(self, muted: bool) -> None:
        if (p := self.active_player) and self.client:
            self.run(self.client.players.volume_mute(p.player_id, muted))

    def set_power(self, player_id: str, powered: bool) -> None:
        if self.client:
            self.run(self.client.players.power(player_id, powered))

    def play_index(self, index: int | str) -> None:
        if (qid := self._qid()) and self.client:
            self.run(self.client.player_queues.play_index(qid, index))

    def play_media(self, media, option=None, start_item: str | None = None) -> None:
        if (qid := self._qid() or (self.active_player and self.active_player.player_id)) and self.client:
            self.run(self.client.player_queues.play_media(qid, media, option=option, start_item=start_item))

    def transfer_to(self, target_player_id: str) -> None:
        """Move the active queue to another player and make that player active."""
        if (qid := self._qid()) and self.client:
            self.run(self.client.player_queues.transfer(qid, target_player_id))
        self.set_active_player(target_player_id)

    def delete_queue_item(self, item_id: str) -> None:
        if (qid := self._qid()) and self.client:
            self.run(self.client.player_queues.delete_item(qid, item_id))

    def move_queue_item_next(self, item_id: str) -> None:
        if (qid := self._qid()) and self.client:
            self.run(self.client.player_queues.move_next(qid, item_id))

    def move_queue_item(self, item_id: str, shift: int, on_fail: Callable[[Exception], None]) -> None:
        """Move a queue item `shift` rows (down if positive); on_fail runs if the server refuses."""
        if not shift:  # pos_shift=0 means "play next" to the server, not "stay put"
            return
        if not (qid := self._qid()) or not self.client:
            return

        async def go() -> None:
            try:
                await self.client.player_queues.move_item(qid, item_id, shift)
            except Exception as err:  # noqa: BLE001 - any failure means the optimistic move must roll back
                log.error("moving queue item failed: %s", err)
                on_fail(err)

        self.run(go())

    def clear_queue(self) -> None:
        if (qid := self._qid()) and self.client:
            self.run(self.client.player_queues.clear(qid))

    # ------------------------------------------------------------------ queue options (MASS ⋮ menu)

    def queue_cmd(self, command: str, **kwargs) -> None:
        """Fire a player_queues/<command> on the active queue."""
        if (qid := self._qid()) and self.client:
            self.run(self.client.send_command(f"player_queues/{command}", queue_id=qid, **kwargs))

    def set_autoplay(self, on: bool) -> None:
        self.queue_cmd("autoplay", autoplay_enabled=on)

    def set_crossfade(self, on: bool) -> None:
        self.queue_cmd("crossfade", crossfade_enabled=on)

    def set_overlay(self, source: str | None) -> None:
        if not self.supports(OVERLAY_SCHEMA):
            return
        if source:
            self.queue_cmd("overlay", enabled=True, source=source)
        else:
            self.queue_cmd("overlay", enabled=False)

    def save_queue_as_playlist(self, name: str) -> None:
        self.queue_cmd("save_as_playlist", name=name)

    async def editable_playlists(self) -> list[Playlist]:
        """Library playlists tracks can be added to, fetched fresh on every call."""
        found: list[Playlist] = []
        while True:
            page = await self.client.music.get_library_playlists(limit=PLAYLIST_PAGE, offset=len(found))
            found.extend(page)
            if len(page) < PLAYLIST_PAGE:
                break
        return [p for p in found if p.is_editable and not p.is_dynamic]

    async def create_playlist(self, name: str) -> Playlist:
        return await self.client.music.create_playlist(name)

    def add_to_playlist(self, playlist: Playlist, items: list, on_done: Callable[[], None],
                        on_fail: Callable[[Exception], None]) -> None:
        """Append tracks (albums are expanded to theirs) to a library playlist."""

        async def go() -> None:
            try:
                music = self.client.music
                uris: list[str] = []
                for item in items:
                    if item.media_type == MediaType.ALBUM:
                        tracks = await music.get_album_tracks(item.item_id, item.provider)
                        uris.extend(t.uri for t in tracks)
                    else:
                        uris.append(item.uri)
                await music.add_playlist_tracks(playlist.item_id, uris)
            except Exception as err:  # noqa: BLE001 - surfaced to the user by on_fail
                log.error("adding to playlist failed: %s", err)
                on_fail(err)
                return
            on_done()

        self.run(go())

    async def sound_effects(self) -> list[dict]:
        if (self._sound_effects is None and self.client
                and self.supports(SOUND_EFFECTS_SCHEMA)):
            try:
                self._sound_effects = await self.client.send_command(
                    "music/sound_effects", require_schema=SOUND_EFFECTS_SCHEMA) or []
            except Exception as err:  # noqa: BLE001
                log.debug("no sound effects: %s", err)
                self._sound_effects = []
        return self._sound_effects or []

    async def sleep_timer(self) -> float | None:
        """Unix timestamp the active player's sleep timer fires at, if any."""
        p = self.active_player
        if not (p and self.client and self.supports(SLEEP_TIMER_SCHEMA)):
            return None
        try:
            return await self.client.send_command(
                "players/sleep_timer/get", require_schema=SLEEP_TIMER_SCHEMA, player_id=p.player_id)
        except Exception:  # noqa: BLE001
            return None

    def set_sleep_timer(self, minutes: int) -> None:
        if not ((p := self.active_player) and self.client and self.supports(SLEEP_TIMER_SCHEMA)):
            return
        if minutes > 0:
            self.run(self.client.send_command(
                "players/sleep_timer/set", require_schema=SLEEP_TIMER_SCHEMA,
                player_id=p.player_id, seconds=minutes * 60))
        else:
            self.run(self.client.send_command(
                "players/sleep_timer/clear", require_schema=SLEEP_TIMER_SCHEMA, player_id=p.player_id))

    def announce(self, message: str) -> None:
        if ((p := self.active_player) and self.client and message.strip()
                and self.supports(ANNOUNCEMENT_SCHEMA)):
            self.run(self.client.send_command(
                "players/cmd/play_announcement", require_schema=ANNOUNCEMENT_SCHEMA,
                player_id=p.player_id, message=message.strip()))

    async def lyrics_for_current(self) -> tuple[str, str | None, str | None]:
        """(key, plain, lrc) for whatever is playing; key identifies the track."""
        q, p = self.active_queue, self.active_player
        track = None
        if q and q.current_item and (mi := q.current_item.media_item) is not None:
            if getattr(mi, "media_type", None) and mi.media_type.value == "track":
                track = mi
        uri = getattr(track, "uri", None)
        if track is None and p and (cm := p.current_media) and cm.uri and "://track/" in cm.uri:
            uri = cm.uri
        if not uri or not self.client:
            return "", None, None
        if uri in self._lyrics:
            return (uri, *self._lyrics[uri])
        try:
            if track is None:
                track = await self.client.music.get_item_by_uri(uri)
            md = getattr(track, "metadata", None)
            if md and (md.lrc_lyrics or md.lyrics):
                res = (md.lyrics, md.lrc_lyrics)
            else:
                r = await self.client.send_command("metadata/get_track_lyrics", track=track.to_dict()) or [None, None]
                res = (r[0], r[1])
        except Exception as err:  # noqa: BLE001
            log.debug("lyrics lookup failed for %s: %s", uri, err)
            res = (None, None)
        self._lyrics[uri] = res
        if len(self._lyrics) > 200:
            self._lyrics.pop(next(iter(self._lyrics)))
        return (uri, *res)

    # ------------------------------------------------------------------ AI Radio / DJ

    @property
    def ai_available(self) -> bool:
        return bool(self.client and any(p.domain == "ai_radio" for p in self.client.providers))

    async def ai(self, command: str, **kwargs):
        return await self.client.send_command(f"ai_radio/{command}", **kwargs)

    async def refresh_ai(self) -> None:
        """Reload hosts + queue→DJ mapping; emits ai-changed."""
        if not self.ai_available:
            self.dj_hosts, self.dj_map = {}, {}
        else:
            try:
                hosts = await self.ai("hosts/list")
                self.dj_hosts = {h["id"]: h for h in hosts}
                self.dj_map = await self.ai("queue_dj/status") or {}
            except Exception as err:  # noqa: BLE001
                log.warning("AI Radio unavailable: %s", err)
                self.dj_hosts, self.dj_map = {}, {}
        self.emit("ai-changed")

    def dj_for(self, queue_id: str | None) -> dict | None:
        host_id = self.dj_map.get(queue_id or "")
        return self.dj_hosts.get(host_id) if host_id else None

    def set_dj(self, host_id: str | None) -> None:
        q, p = self.active_queue, self.active_player
        qid = q.queue_id if q else (p.player_id if p else None)
        if not qid or not self.client:
            return

        async def go():
            await self.ai("queue_dj/set", queue_id=qid, host_id=host_id)
            await self.refresh_ai()

        self.run(go())

    def toggle_favorite_current(self) -> None:
        """Fallback for media we can't resolve (external sources): add only."""
        if (p := self.active_player) and self.client:
            self.run(self.client.players.add_currently_playing_to_favorites(p.player_id))

    async def favorite_state(self, mi) -> tuple[bool, str | None]:
        """(is favorite, library item id) for a queue media item.

        Queue items carry a snapshot, often of the provider item (not the library one), so look
        the library copy up fresh.
        """
        if mi is None or not self.client or not getattr(mi, "uri", None):
            return False, None
        if getattr(mi, "media_type", None) not in FAVORITABLE:  # sound effects, announcements…
            return False, None
        music = self.client.music
        try:
            if getattr(mi, "provider", None) == "library":
                lib = await music.get_item_by_uri(mi.uri)
            else:
                lib = await music.get_library_item_by_prov_id(mi.media_type, mi.item_id, mi.provider)
        except Exception as err:  # noqa: BLE001 - not in the library / unknown provider
            log.debug("favorite lookup failed: %s", err)
            return False, None
        if lib is None:
            return False, None
        return bool(getattr(lib, "favorite", False)), str(lib.item_id)

    async def set_favorite(self, mi, on: bool, library_id: str | None) -> None:
        music = self.client.music
        if on:
            await music.add_item_to_favorites(mi.uri)  # adds it to the library too if needed
        elif library_id:
            await music.remove_item_from_favorites(mi.media_type, library_id)
