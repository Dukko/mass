"""Add to playlist: which playlists qualify, album expansion, cache invalidation."""

import asyncio
from types import SimpleNamespace

from music_assistant_models.enums import EventType, MediaType
from music_assistant_models.event import MassEvent

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


def test_removal_sends_the_given_positions_for_the_playlist():
    music = FakeMusic()
    removed = []

    async def remove_playlist_tracks(playlist_id, positions):
        removed.append((playlist_id, positions))

    music.remove_playlist_tracks = remove_playlist_tracks
    b = make_backend(music)
    done = []

    async def go():
        b.remove_from_playlist(playlist("7", "Mine"), [2, 5], lambda: done.append(True), done.append)
        await asyncio.gather(*b._tasks)

    asyncio.run(go())

    assert removed == [("7", (2, 5))]
    assert done == [True]


def test_failed_removal_reports_the_error():
    music = FakeMusic()

    async def boom(*_):
        raise RuntimeError("refused")

    music.remove_playlist_tracks = boom
    b = make_backend(music)
    results = []

    async def go():
        b.remove_from_playlist(playlist("7", "Mine"), [1], lambda: results.append("done"), results.append)
        await asyncio.gather(*b._tasks)

    asyncio.run(go())

    assert [str(e) for e in results] == ["refused"]


def test_delete_removes_the_playlist_from_the_library():
    music = FakeMusic()
    calls = []

    async def remove_item_from_library(media_type, library_item_id, **kwargs):
        calls.append((media_type, library_item_id, kwargs))

    music.remove_item_from_library = remove_item_from_library
    b = make_backend(music)
    done = []

    async def go():
        b.delete_playlist(playlist("7", "Mine"), lambda: done.append(True), done.append)
        await asyncio.gather(*b._tasks)

    asyncio.run(go())

    assert calls == [(MediaType.PLAYLIST, "7", {})]
    assert done == [True]


def test_failed_delete_reports_the_error():
    music = FakeMusic()

    async def boom(*_, **__):
        raise RuntimeError("no such playlist")

    music.remove_item_from_library = boom
    b = make_backend(music)
    results = []

    async def go():
        b.delete_playlist(playlist("7", "Mine"), lambda: results.append("done"), results.append)
        await asyncio.gather(*b._tasks)

    asyncio.run(go())

    assert [str(e) for e in results] == ["no such playlist"]


def test_media_item_events_are_forwarded_with_their_type_and_object_id():
    b = Backend(SimpleNamespace())
    seen = []
    b.connect("media-item-changed", lambda _b, event, uri: seen.append((event, uri)))

    for event_type in (EventType.MEDIA_ITEM_ADDED, EventType.MEDIA_ITEM_UPDATED, EventType.MEDIA_ITEM_DELETED):
        b._on_event(MassEvent(event=event_type, object_id="library://playlist/12"))

    assert seen == [("media_item_added", "library://playlist/12"),
                    ("media_item_updated", "library://playlist/12"),
                    ("media_item_deleted", "library://playlist/12")]


def test_playlist_tracks_carry_the_servers_positions(monkeypatch):
    music = FakeMusic()
    b = make_backend(music)
    sent = []

    async def send_command(command, **kwargs):
        sent.append((command, kwargs["item_id"], kwargs["provider_instance_id_or_domain"]))
        return [{"position": 1, "name": "a"}, {"position": 3, "name": "c"}, {"name": "no position"}]

    b.client.send_command = send_command
    monkeypatch.setattr("mass.backend.media_from_dict", lambda obj: obj["name"])

    pairs = asyncio.run(b.playlist_tracks(SimpleNamespace(item_id="7", provider="library")))

    assert sent == [("music/playlists/playlist_tracks", "7", "library")]
    assert pairs == [("a", 1), ("c", 3), ("no position", None)]
