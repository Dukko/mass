"""Library listing state and the mapping from it to Music Assistant client arguments."""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Any, Literal

from music_assistant_models.api import SortOptionInfo
from music_assistant_models.enums import AlbumType, SortDirection, SortField

LibraryKind = Literal["albums", "artists", "tracks", "playlists", "radios", "podcasts", "genres"]

TYPED_SORT_SCHEMA = 86
RANDOM_SEED_SCHEMA = 98
LISTING_FILTER_SCHEMA = 101

DIRECTIONLESS = frozenset({SortField.RANDOM, SortField.RANDOM_PLAY_COUNT, SortField.ORIGINAL})
RANDOM_FIELDS = frozenset({SortField.RANDOM, SortField.RANDOM_PLAY_COUNT})


@dataclass(frozen=True)
class ListingState:
    search: str = ""
    favorite_only: bool = False
    sort_field: SortField | None = None
    sort_direction: SortDirection | None = None
    genre_ids: tuple[int, ...] = ()
    providers: tuple[str, ...] = ()
    album_types: tuple[AlbumType, ...] = ()
    played_only: bool = False
    random_seed: int | None = None

    def filter_count(self) -> int:
        return (len(self.genre_ids) + len(self.providers) + len(self.album_types)
                + (1 if self.played_only else 0))

    def is_filtered(self) -> bool:
        return bool(self.search.strip()) or self.favorite_only or self.filter_count() > 0

    def cleared(self) -> ListingState:
        return ListingState(sort_field=self.sort_field, sort_direction=self.sort_direction,
                            random_seed=self.random_seed)


_NAME_ASC = (SortField.SORT_NAME, SortDirection.ASC)
DEFAULT_SORT: dict[LibraryKind, tuple[SortField, SortDirection]] = {
    "albums": (SortField.TIMESTAMP_ADDED, SortDirection.DESC),
    "artists": _NAME_ASC,
    "tracks": _NAME_ASC,
    "playlists": _NAME_ASC,
    "radios": _NAME_ASC,
    "podcasts": _NAME_ASC,
    "genres": _NAME_ASC,
}

FILTERS: dict[LibraryKind, frozenset[str]] = {
    "albums": frozenset({"genre", "provider", "album_type", "played"}),
    "artists": frozenset({"genre", "provider", "played"}),
    "tracks": frozenset({"genre", "provider", "played"}),
    "playlists": frozenset({"provider", "played"}),
    "radios": frozenset({"provider", "played"}),
    "podcasts": frozenset({"provider", "played"}),
    "genres": frozenset({"played"}),
}

SORT_LABELS: dict[SortField, str] = {
    SortField.NAME: "Name",
    SortField.SORT_NAME: "Name",
    SortField.TIMESTAMP_ADDED: "Date added",
    SortField.TIMESTAMP_MODIFIED: "Date modified",
    SortField.LAST_PLAYED: "Last played",
    SortField.PLAY_COUNT: "Most played",
    SortField.DURATION: "Duration",
    SortField.YEAR: "Year",
    SortField.POSITION: "Position",
    SortField.ARTIST_NAME: "Artist",
    SortField.RANDOM: "Random",
    SortField.RANDOM_PLAY_COUNT: "Random (weighted by plays)",
    SortField.TRACK_NUMBER: "Track number",
    SortField.ALBUM_NAME: "Album",
    SortField.PROVIDER: "Provider",
    SortField.FAVORITE_TIMESTAMP: "Date liked",
    SortField.ORIGINAL: "Original order",
    SortField.RELEASE_DATE: "Release date",
}

_FALLBACK_SORTS: dict[LibraryKind, list[SortField]] = {
    "albums": [SortField.TIMESTAMP_ADDED, SortField.SORT_NAME, SortField.YEAR, SortField.LAST_PLAYED,
               SortField.PLAY_COUNT],
    "artists": [SortField.SORT_NAME, SortField.TIMESTAMP_ADDED, SortField.LAST_PLAYED, SortField.PLAY_COUNT],
    "tracks": [SortField.SORT_NAME, SortField.TIMESTAMP_ADDED, SortField.LAST_PLAYED, SortField.PLAY_COUNT],
    "playlists": [SortField.SORT_NAME, SortField.TIMESTAMP_ADDED, SortField.LAST_PLAYED],
    "radios": [SortField.SORT_NAME, SortField.TIMESTAMP_ADDED, SortField.LAST_PLAYED],
    "podcasts": [SortField.SORT_NAME, SortField.TIMESTAMP_ADDED, SortField.LAST_PLAYED],
    "genres": [SortField.SORT_NAME, SortField.TIMESTAMP_ADDED],
}


_LEGACY_ALL = frozenset({SortField.NAME, SortField.SORT_NAME, SortField.TIMESTAMP_ADDED,
                         SortField.TIMESTAMP_MODIFIED, SortField.LAST_PLAYED, SortField.PLAY_COUNT})
_LEGACY_RANDOM = {SortField.RANDOM: "random", SortField.RANDOM_PLAY_COUNT: "random_play_count"}
_LEGACY_ONLY: dict[tuple[LibraryKind, SortField], str] = {
    ("albums", SortField.YEAR): "year",
    ("albums", SortField.ARTIST_NAME): "album_artist_name",
    ("tracks", SortField.DURATION): "duration",
    ("tracks", SortField.ARTIST_NAME): "track_artist_name",
}


def legacy_order_by(field: SortField, direction: SortDirection | None, kind: LibraryKind) -> str:
    """The pre-typed-sort `order_by` string; fields the server has no key for use the kind's default."""
    if field in _LEGACY_RANDOM:
        return _LEGACY_RANDOM[field]
    key = field.value if field in _LEGACY_ALL else _LEGACY_ONLY.get((kind, field))
    if key is None:
        field, direction = DEFAULT_SORT[kind]
        key = field.value
    return f"{key}_desc" if direction == SortDirection.DESC else key


def fallback_sort_fields(kind: LibraryKind) -> list[SortField]:
    return list(_FALLBACK_SORTS[kind])


def usable_sort_fields(options: list[SortOptionInfo], schema: int) -> list[SortOptionInfo]:
    if schema >= RANDOM_SEED_SCHEMA:
        return list(options)
    return [o for o in options if o.field not in RANDOM_FIELDS]


def available_filters(kind: LibraryKind, schema: int, provider_count: int) -> frozenset[str]:
    out = set(FILTERS[kind])
    if schema < LISTING_FILTER_SCHEMA:
        out.discard("genre")
        if kind != "genres":
            out.discard("played")
    if provider_count < 2:
        out.discard("provider")
    return frozenset(out)


def effective_state(state: ListingState, kind: LibraryKind, schema: int, provider_count: int,
                    has_genres: bool = True) -> ListingState:
    """The state with saved filters the server or provider set can't honour dropped.

    The original keeps them, so they apply again if the capability returns.
    """
    allowed = available_filters(kind, schema, provider_count)
    if not has_genres:
        allowed -= {"genre"}
    return replace(
        state,
        genre_ids=state.genre_ids if "genre" in allowed else (),
        providers=state.providers if "provider" in allowed else (),
        album_types=state.album_types if "album_type" in allowed else (),
        played_only=state.played_only and "played" in allowed,
    )


def listing_kwargs(state: ListingState, kind: LibraryKind, schema: int) -> dict[str, Any]:
    """Keyword arguments for `get_library_<kind>`, limited to what the server's schema accepts."""
    kwargs: dict[str, Any] = {}
    if search := state.search.strip():
        kwargs["search"] = search
    if state.favorite_only:
        kwargs["favorite"] = True

    field, direction = state.sort_field, state.sort_direction
    if field is None:
        field, default_direction = DEFAULT_SORT[kind]
        direction = direction or default_direction
    if schema >= TYPED_SORT_SCHEMA:
        kwargs["sort_field"] = field
        if field not in DIRECTIONLESS:
            kwargs["sort_direction"] = direction
        if field in RANDOM_FIELDS and schema >= RANDOM_SEED_SCHEMA and state.random_seed is not None:
            kwargs["random_seed"] = state.random_seed
    else:
        kwargs["order_by"] = legacy_order_by(field, direction, kind)

    if state.genre_ids and schema >= LISTING_FILTER_SCHEMA and kind != "genres":
        kwargs["genre"] = list(state.genre_ids)
    if state.played_only and (schema >= LISTING_FILTER_SCHEMA or kind == "genres"):
        kwargs["played_only"] = True
    if state.providers and kind != "genres":
        kwargs["provider"] = list(state.providers)
    if state.album_types and kind == "albums":
        kwargs["album_types"] = list(state.album_types)
    return kwargs


def _enum_or_none(enum_cls: type, value: Any) -> Any:
    try:
        return enum_cls(value)
    except ValueError:
        return None


def state_to_json(state: ListingState) -> dict[str, Any]:
    """Plain-JSON form of a state; the random seed is deliberately left out."""
    data: dict[str, Any] = {}
    if state.search:
        data["search"] = state.search
    if state.favorite_only:
        data["favorite_only"] = True
    if state.sort_field is not None:
        data["sort_field"] = state.sort_field.value
    if state.sort_direction is not None:
        data["sort_direction"] = state.sort_direction.value
    if state.genre_ids:
        data["genre_ids"] = list(state.genre_ids)
    if state.providers:
        data["providers"] = list(state.providers)
    if state.album_types:
        data["album_types"] = [t.value for t in state.album_types]
    if state.played_only:
        data["played_only"] = True
    return data


def _list_of(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    return value if isinstance(value, list) else []


def state_from_json(data: Any) -> ListingState:
    """Inverse of `state_to_json`; anything unknown or malformed is dropped."""
    if not isinstance(data, dict):
        return ListingState()
    search = data.get("search")
    field = _enum_or_none(SortField, data.get("sort_field"))
    direction = _enum_or_none(SortDirection, data.get("sort_direction"))
    album_types = (_enum_or_none(AlbumType, v) for v in _list_of(data, "album_types"))
    return ListingState(
        search=search if isinstance(search, str) else "",
        favorite_only=data.get("favorite_only") is True,
        sort_field=field,
        sort_direction=direction if field is not None else None,
        genre_ids=tuple(v for v in _list_of(data, "genre_ids") if isinstance(v, int) and not isinstance(v, bool)),
        providers=tuple(v for v in _list_of(data, "providers") if isinstance(v, str)),
        album_types=tuple(t for t in album_types if t not in (None, AlbumType.UNKNOWN)),
        played_only=data.get("played_only") is True,
        random_seed=random.randrange(2**31) if field in RANDOM_FIELDS else None,
    )


def matches_query(item: Any, query: str) -> bool:
    """Case-insensitive match on an item's name, artists and album name."""
    needle = query.strip().casefold()
    if not needle:
        return True
    haystack = [getattr(item, "name", None)]
    haystack += [getattr(a, "name", None) for a in getattr(item, "artists", None) or []]
    haystack.append(getattr(getattr(item, "album", None), "name", None))
    return any(needle in text.casefold() for text in haystack if text)
