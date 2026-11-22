"""Backend helpers behind the web-app parity features."""

import asyncio
from types import SimpleNamespace

from music_assistant_models.enums import ListingType, MediaType
from music_assistant_models.errors import InvalidCommand

from mass.backend import Backend


class FakeMusic:
    def __init__(self):
        self.sort_calls = 0
        self.removed = []
        self.added = []
        self.lookups = []
        self.lookup_result = SimpleNamespace(item_id="42", favorite=True)
        self.genre_calls = 0
        self.genre_outcomes = []

    async def get_sort_options(self, listing):
        self.sort_calls += 1
        return ["opt"]

    async def get_library_item_by_prov_id(self, media_type, item_id, provider):
        self.lookups.append((media_type, item_id, provider))
        if self.lookup_result is None:
            raise RuntimeError("not in library")
        return self.lookup_result

    async def remove_item_from_favorites(self, media_type, library_id):
        self.removed.append((media_type, library_id))

    async def get_library_genres(self, limit=None):
        self.genre_calls += 1
        outcome = self.genre_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def add_item_to_favorites(self, uri):
        self.added.append(uri)


class FakePlayers:
    def __init__(self):
        self.calls = []

    async def set_members(self, leader, add, remove):
        self.calls.append((leader, add, remove))


def make_backend(schema=101):
    b = Backend.__new__(Backend)
    b.music = FakeMusic()
    b.players = FakePlayers()
    b.client = SimpleNamespace(music=b.music, players=b.players,
                               server_info=SimpleNamespace(schema_version=schema))
    b._tasks = set()
    b._sort_cache = {}
    b._genres_ok = None
    return b


async def settle(b):
    await asyncio.gather(*list(b._tasks))


def test_sort_options_below_86_makes_no_client_call():
    b = make_backend(schema=65)

    result = asyncio.run(b.sort_options(ListingType.LIBRARY_ALBUMS))

    assert result == []
    assert b.music.sort_calls == 0


def test_sort_options_are_fetched_once_per_listing():
    b = make_backend()

    async def go():
        await b.sort_options(ListingType.LIBRARY_ALBUMS)
        return await b.sort_options(ListingType.LIBRARY_ALBUMS)

    assert asyncio.run(go()) == ["opt"]
    assert b.music.sort_calls == 1


def test_unfavoriting_a_provider_item_resolves_the_library_id():
    b = make_backend()
    item = SimpleNamespace(media_type=MediaType.ALBUM, provider="spotify", item_id="abc",
                           uri="spotify://album/abc")
    done, failed = [], []

    async def go():
        b.toggle_favorite(item, False, done.append, failed.append)
        await settle(b)

    asyncio.run(go())

    assert b.music.lookups == [(MediaType.ALBUM, "abc", "spotify")]
    assert b.music.removed == [(MediaType.ALBUM, "42")]
    assert done == [False] and failed == []


def test_toggle_favorite_rejects_unfavoritable_types():
    b = make_backend()
    item = SimpleNamespace(media_type=MediaType.GENRE, provider="library", item_id="1", uri="u")
    failed = []

    b.toggle_favorite(item, True, lambda on: None, failed.append)

    assert len(failed) == 1 and isinstance(failed[0], ValueError)


def test_set_group_members_with_nothing_to_change_sends_nothing():
    b = make_backend()

    async def go():
        b.set_group_members("leader", [], [], lambda err: None)
        await settle(b)

    asyncio.run(go())

    assert b.players.calls == []


def test_set_group_members_sends_none_for_empty_side():
    b = make_backend()

    async def go():
        b.set_group_members("leader", ["a"], [], lambda err: None)
        await settle(b)

    asyncio.run(go())

    assert b.players.calls == [("leader", ["a"], None)]


def test_unfavoriting_fails_when_the_library_item_cannot_be_found():
    b = make_backend()
    b.music.lookup_result = None
    item = SimpleNamespace(media_type=MediaType.ALBUM, provider="spotify", item_id="abc",
                           uri="spotify://album/abc")
    done, failed = [], []

    async def go():
        b.toggle_favorite(item, False, done.append, failed.append)
        await settle(b)

    asyncio.run(go())

    assert done == [] and len(failed) == 1
    assert b.music.removed == []


def test_favoriting_adds_by_uri_without_a_library_lookup():
    b = make_backend()
    item = SimpleNamespace(media_type=MediaType.ALBUM, provider="spotify", item_id="abc",
                           uri="spotify://album/abc")
    done = []

    async def go():
        b.toggle_favorite(item, True, done.append, lambda err: None)
        await settle(b)

    asyncio.run(go())

    assert b.music.added == ["spotify://album/abc"]
    assert b.music.lookups == []
    assert done == [True]


def test_has_genres_true_is_cached():
    b = make_backend()
    b.music.genre_outcomes = [["rock"]]

    async def go():
        return await b.has_genres(), await b.has_genres()

    assert asyncio.run(go()) == (True, True)
    assert b.music.genre_calls == 1


def test_has_genres_unknown_command_is_cached_as_false():
    b = make_backend()
    b.music.genre_outcomes = [InvalidCommand("no such command")]

    async def go():
        return await b.has_genres(), await b.has_genres()

    assert asyncio.run(go()) == (False, False)
    assert b.music.genre_calls == 1


def test_has_genres_transient_error_is_retried_on_the_next_call():
    b = make_backend()
    b.music.genre_outcomes = [TimeoutError("slow"), ["rock"]]

    async def go():
        return await b.has_genres(), await b.has_genres()

    assert asyncio.run(go()) == (False, True)
    assert b.music.genre_calls == 2


def test_has_genres_empty_library_is_not_cached():
    b = make_backend()
    b.music.genre_outcomes = [[], ["rock"]]

    async def go():
        return await b.has_genres(), await b.has_genres()

    assert asyncio.run(go()) == (False, True)
