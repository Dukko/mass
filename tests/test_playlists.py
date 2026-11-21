"""Add to playlist: which playlists qualify, album expansion, cache invalidation."""

import asyncio
from types import SimpleNamespace

from music_assistant_models.enums import MediaType

from mass.backend import PLAYLIST_PAGE, Backend


def playlist(item_id, name, editable=True, dynamic=False):
    return SimpleNamespace(item_id=item_id, name=name, is_editable=editable, is_dynamic=dynamic)


class FakeMusic:
    def __init__(self, playlists=()):
        self.playlists = list(playlists)
        self.added = []
        self.album_tracks = {}

    async def get_library_playlists(self, limit, offset):
        return self.playlists[offset:offset + limit]

    async def create_playlist(self, name):
        made = playlist("new", name)
        self.playlists.append(made)
        return made

    async def get_album_tracks(self, item_id, provider):
        return self.album_tracks[(item_id, provider)]

    async def add_playlist_tracks(self, playlist_id, uris):
        self.added.append((playlist_id, uris))


def make_backend(music):
    b = Backend.__new__(Backend)
    b.client = SimpleNamespace(music=music)
    b._tasks = set()
    return b


def test_only_editable_static_playlists_are_offered():
    music = FakeMusic([
        playlist("1", "Mine"),
        playlist("2", "Spotify Daily Mix", editable=False),
        playlist("3", "Smart", dynamic=True),
    ])
    b = make_backend(music)

    names = [p.name for p in asyncio.run(b.editable_playlists())]

    assert names == ["Mine"]


def test_playlists_beyond_one_page_are_all_listed():
    music = FakeMusic([playlist(str(i), f"List {i}") for i in range(PLAYLIST_PAGE + 3)])
    b = make_backend(music)

    names = [p.name for p in asyncio.run(b.editable_playlists())]

    assert len(names) == PLAYLIST_PAGE + 3
    assert names[-1] == f"List {PLAYLIST_PAGE + 2}"


def test_playlists_are_refetched_on_every_call():
    music = FakeMusic([playlist("1", "Mine")])
    b = make_backend(music)

    async def go():
        await b.editable_playlists()
        music.playlists.append(playlist("2", "Saved elsewhere"))
        return [p.name for p in await b.editable_playlists()]

    assert asyncio.run(go()) == ["Mine", "Saved elsewhere"]


def test_albums_are_added_as_their_tracks():
    music = FakeMusic()
    music.album_tracks[("a1", "spotify")] = [SimpleNamespace(uri="spotify://track/t1"),
                                             SimpleNamespace(uri="spotify://track/t2")]
    b = make_backend(music)
    album = SimpleNamespace(media_type=MediaType.ALBUM, item_id="a1", provider="spotify", uri="spotify://album/a1")
    track = SimpleNamespace(media_type=MediaType.TRACK, item_id="t9", provider="library", uri="library://track/9")
    done = []

    async def go():
        b.add_to_playlist(playlist("7", "Mine"), [album, track], lambda: done.append(True), done.append)
        await asyncio.gather(*b._tasks)

    asyncio.run(go())

    assert music.added == [("7", ["spotify://track/t1", "spotify://track/t2", "library://track/9"])]
    assert done == [True]


def test_failed_add_reports_the_error():
    music = FakeMusic()

    async def boom(*_):
        raise RuntimeError("nope")

    music.add_playlist_tracks = boom
    b = make_backend(music)
    track = SimpleNamespace(media_type=MediaType.TRACK, item_id="t", provider="library", uri="library://track/1")
    errors = []

    async def go():
        b.add_to_playlist(playlist("7", "Mine"), [track], lambda: errors.append("done"), errors.append)
        await asyncio.gather(*b._tasks)

    asyncio.run(go())

    assert [str(e) for e in errors] == ["nope"]
