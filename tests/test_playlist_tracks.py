"""WholeList feeding a Pager, and playlist_tracks request arguments."""

import asyncio
from types import SimpleNamespace

from mass.backend import Backend
from mass.ui.paging import Pager
from mass.ui.playlist_tracks import WholeList


def test_whole_list_delivers_everything_in_one_page_then_ends():
    calls = []

    async def load():
        calls.append("load")
        return list(range(250))

    received, tasks = [], []
    pager = Pager(WholeList(load), lambda items, first: received.append((len(items), first)),
                  lambda coro: tasks.append(asyncio.ensure_future(coro)), page=100)

    async def run():
        pager.load_more()
        await asyncio.gather(*tasks)
        tasks.clear()
        pager.load_more()
        await asyncio.gather(*tasks)

    asyncio.run(run())

    assert received == [(250, True), (0, False)]
    assert calls == ["load"]


def test_whole_list_uses_a_primed_list_once_then_reloads():
    async def load():
        return ["fresh"]

    whole = WholeList(load, primed=["primed"])

    first = asyncio.run(whole(0, 100))
    second = asyncio.run(whole(0, 100))

    assert (first, second) == (["primed"], ["fresh"])


def test_playlist_tracks_sends_paging_only_when_asked():
    b = Backend.__new__(Backend)
    sent = []

    async def send_command(command, **kwargs):
        sent.append(kwargs)
        return []

    b.client = SimpleNamespace(send_command=send_command)
    playlist = SimpleNamespace(item_id="7", provider="library")

    asyncio.run(b.playlist_tracks(playlist, force_refresh=False))
    asyncio.run(b.playlist_tracks(playlist, limit=200, offset=400, search="abba"))

    assert sent[0] == {"item_id": "7", "provider_instance_id_or_domain": "library", "force_refresh": False,
                       "require_schema": None}
    assert sent[1] == {"item_id": "7", "provider_instance_id_or_domain": "library", "force_refresh": True,
                       "limit": 200, "offset": 400, "search": "abba", "require_schema": 93}
