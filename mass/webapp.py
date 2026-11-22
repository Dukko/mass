"""Deep links into the Music Assistant web app. Never put credentials in these URLs."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from music_assistant_models.enums import MediaType

ROUTE_SETTINGS = "settings"
ROUTE_PROVIDERS = "settings/providers"
ROUTE_USERS = "settings/users"
ROUTE_STATISTICS = "statistics"
ROUTE_PARTY = "party"

_SEGMENTS = {
    MediaType.ALBUM: "albums",
    MediaType.ARTIST: "artists",
    MediaType.TRACK: "tracks",
    MediaType.PLAYLIST: "playlists",
    MediaType.PODCAST: "podcasts",
    MediaType.AUDIOBOOK: "audiobooks",
    MediaType.RADIO: "radios",
    MediaType.GENRE: "genres",
}


def webapp_url(base: str, route: str = "") -> str:
    return f"{base.rstrip('/')}/#/{route.lstrip('/')}"


def item_route(item: Any) -> str | None:
    segment = _SEGMENTS.get(getattr(item, "media_type", None))
    if segment is None:
        return None
    return f"{segment}/{quote(str(item.provider), safe='')}/{quote(str(item.item_id), safe='')}"


def player_route(player_id: str, dsp: bool = False) -> str:
    route = f"settings/editplayer/{quote(player_id, safe='')}"
    return f"{route}/dsp" if dsp else route
