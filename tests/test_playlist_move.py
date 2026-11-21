"""Playlist drag-reorder: row-to-position mapping and the schema-gated server command."""

import asyncio
from types import SimpleNamespace

import pytest

from mass.backend import Backend
from mass.ui.reorder import playlist_move


@pytest.mark.parametrize(
    ("positions", "src", "insert_at", "expected"),
    [
        ([1, 2, 3, 4], 0, 3, (2, 1, 3)),  # down: lands on the row that was third
        ([1, 2, 3, 4], 3, 1, (1, 4, 2)),  # up
        ([1, 2, 4, 5], 0, 3, (2, 1, 4)),  # gap: destination is the position held by the row, not row + 1
        ([1, 2, 4, 5], 3, 2, (2, 5, 4)),  # gap, moving up into the hole's neighbour
        ([1, 2, 4, 5], 1, 4, (3, 2, 5)),  # to the end
        ([1, 2, 4, 5], 2, 0, (0, 4, 1)),  # to the top
        ([1, 2, 4, 5], 2, 2, None),  # dropped just above itself
        ([1, 2, 4, 5], 2, 3, None),  # dropped just below itself
    ],
)
def test_playlist_move_maps_rows_to_server_positions(positions, src, insert_at, expected):
    assert playlist_move(positions, src, insert_at) == expected


class FakeMusic:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def move_playlist_track(self, playlist_id, from_position, to_position):
        self.calls.append((playlist_id, from_position, to_position))
        if self.fail:
            raise RuntimeError("refused")


def make_backend(music, schema):
    b = Backend.__new__(Backend)
    b.client = SimpleNamespace(music=music, server_info=SimpleNamespace(schema_version=schema))
    b._tasks = set()
    return b


def run_move(b, done, failures):
    async def go():
        b.move_playlist_track(SimpleNamespace(item_id="7"), 2, 5, lambda: done.append(True), failures.append)
        await asyncio.gather(*b._tasks)

    asyncio.run(go())


def test_old_server_makes_no_call_and_reports_failure():
    music = FakeMusic()
    done, failures = [], []

    run_move(make_backend(music, schema=65), done, failures)

    assert music.calls == []
    assert done == []
    assert len(failures) == 1


def test_new_server_moves_between_the_given_positions():
    music = FakeMusic()
    done, failures = [], []

    run_move(make_backend(music, schema=124), done, failures)

    assert music.calls == [("7", 2, 5)]
    assert done == [True]
    assert failures == []


def test_new_server_refusal_is_reported():
    done, failures = [], []

    run_move(make_backend(FakeMusic(fail=True), schema=124), done, failures)

    assert done == []
    assert [str(e) for e in failures] == ["refused"]
