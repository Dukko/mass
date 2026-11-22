"""Listing state to client arguments, per server schema."""

import json
from types import SimpleNamespace

from music_assistant_models.api import SortOptionInfo
from music_assistant_models.enums import AlbumType, SortDirection, SortField

from mass.listing import (
    ListingState,
    available_filters,
    effective_state,
    legacy_order_by,
    listing_kwargs,
    matches_query,
    state_from_json,
    state_to_json,
    usable_sort_fields,
)


def test_old_server_gets_legacy_order_by_and_no_newer_keys():
    state = ListingState(search="abbey", favorite_only=True, album_types=(AlbumType.LIVE,))

    kwargs = listing_kwargs(state, "albums", 65)

    assert kwargs == {"search": "abbey", "favorite": True, "order_by": "timestamp_added_desc",
                      "album_types": [AlbumType.LIVE]}


def test_random_sort_on_schema_90_has_no_direction_or_seed():
    state = ListingState(sort_field=SortField.RANDOM, random_seed=7)

    assert listing_kwargs(state, "tracks", 90) == {"sort_field": SortField.RANDOM}


def test_schema_101_sends_seed_and_genre_ids():
    state = ListingState(sort_field=SortField.RANDOM, random_seed=7, genre_ids=(3, 5))

    kwargs = listing_kwargs(state, "albums", 101)

    assert kwargs == {"sort_field": SortField.RANDOM, "random_seed": 7, "genre": [3, 5]}


def test_genres_kind_drops_provider_but_keeps_played_only_on_old_server():
    state = ListingState(providers=("spotify",), played_only=True)

    kwargs = listing_kwargs(state, "genres", 65)

    assert kwargs == {"order_by": "sort_name", "played_only": True}


def test_whitespace_search_is_dropped():
    assert "search" not in listing_kwargs(ListingState(search="   "), "artists", 101)


def test_typed_sort_includes_direction_for_named_sorts():
    state = ListingState(sort_field=SortField.YEAR, sort_direction=SortDirection.DESC)

    assert listing_kwargs(state, "albums", 90) == {"sort_field": SortField.YEAR,
                                                   "sort_direction": SortDirection.DESC}


def test_legacy_order_by_matches_existing_strings():
    assert legacy_order_by(SortField.TIMESTAMP_ADDED, SortDirection.DESC, "albums") == "timestamp_added_desc"
    assert legacy_order_by(SortField.SORT_NAME, SortDirection.ASC, "albums") == "sort_name"
    assert legacy_order_by(SortField.RANDOM, None, "albums") == "random"
    assert legacy_order_by(SortField.RANDOM_PLAY_COUNT, None, "tracks") == "random_play_count"


def test_legacy_order_by_uses_kind_default_for_fields_the_server_lacks():
    assert legacy_order_by(SortField.ORIGINAL, None, "albums") == "timestamp_added_desc"
    assert legacy_order_by(SortField.ORIGINAL, None, "artists") == "sort_name"
    assert legacy_order_by(SortField.YEAR, SortDirection.DESC, "artists") == "sort_name"
    assert legacy_order_by(SortField.YEAR, SortDirection.DESC, "albums") == "year_desc"


def test_usable_sort_fields_drops_random_below_98():
    options = [SortOptionInfo(SortField.SORT_NAME, True, SortDirection.ASC),
               SortOptionInfo(SortField.RANDOM, False)]

    assert [o.field for o in usable_sort_fields(options, 90)] == [SortField.SORT_NAME]
    assert [o.field for o in usable_sort_fields(options, 98)] == [SortField.SORT_NAME, SortField.RANDOM]


def test_available_filters_gate_on_schema_and_provider_count():
    assert available_filters("albums", 65, 1) == {"album_type"}
    assert available_filters("albums", 101, 3) == {"genre", "provider", "album_type", "played"}
    assert available_filters("genres", 65, 3) == {"played"}


def test_matches_query_checks_artist_and_album_names_case_insensitively():
    track = SimpleNamespace(name="Come Together", artists=[SimpleNamespace(name="The Beatles")],
                            album=SimpleNamespace(name="Abbey Road"))

    assert matches_query(track, "BEATLES")
    assert matches_query(track, "abbey")
    assert matches_query(track, "")
    assert not matches_query(track, "stones")


def test_state_round_trips_through_json_without_the_random_seed():
    state = ListingState(search="abbey", favorite_only=True, sort_field=SortField.YEAR,
                         sort_direction=SortDirection.DESC, genre_ids=(3, 4), providers=("spotify--x",),
                         album_types=(AlbumType.EP,), played_only=True, random_seed=7)

    restored = state_from_json(json.loads(json.dumps(state_to_json(state))))

    assert restored == ListingState(search="abbey", favorite_only=True, sort_field=SortField.YEAR,
                                    sort_direction=SortDirection.DESC, genre_ids=(3, 4),
                                    providers=("spotify--x",), album_types=(AlbumType.EP,), played_only=True)


def test_state_from_json_drops_unknown_values():
    data = {"sort_field": "bogus", "sort_direction": "asc", "album_types": ["ep", "nope"],
            "genre_ids": [1, "x", True], "providers": [5, "a"], "search": 3, "favorite_only": "yes"}

    assert state_from_json(data) == ListingState(album_types=(AlbumType.EP,), genre_ids=(1,), providers=("a",))
    assert state_from_json("garbage") == ListingState()


def test_state_from_json_rerolls_seed_for_random_sort():
    state = state_from_json({"sort_field": "random"})

    assert state.sort_field == SortField.RANDOM
    assert state.random_seed is not None


def test_effective_state_drops_filters_the_server_cannot_honour_but_keeps_the_rest():
    saved = ListingState(search="x", genre_ids=(1,), providers=("a",), played_only=True,
                         album_types=(AlbumType.EP,))

    effective = effective_state(saved, "albums", 65, 1)

    assert effective == ListingState(search="x", album_types=(AlbumType.EP,))
    assert not effective_state(ListingState(genre_ids=(1,), played_only=True), "radios", 65, 3).is_filtered()
    assert effective_state(saved, "albums", 101, 3) == saved
    assert effective_state(saved, "albums", 101, 3, has_genres=False).genre_ids == ()
