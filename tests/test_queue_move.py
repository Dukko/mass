"""Queue drag-reorder: row maths, drop rules and the server command."""

import asyncio
from types import SimpleNamespace

import pytest

from mass.backend import Backend
from mass.ui.queue import can_drop, can_move, move_target


@pytest.mark.parametrize(
    ("src", "insert_at", "expected"),
    [
        (2, 5, (4, 2)),  # down: the gap left by the row shifts the landing spot up by one
        (5, 2, (2, -3)),  # up
        (3, 3, None),  # dropped just above itself
        (3, 4, None),  # dropped just below itself
        (0, 10, (9, 9)),  # to the end of a 10-row queue
    ],
)
def test_move_target(src, insert_at, expected):
    assert move_target(src, insert_at) == expected


def test_only_upcoming_tracks_move():
    assert can_move(4, current=3, is_dj=False)
    assert not can_move(3, current=3, is_dj=False)  # playing
    assert not can_move(1, current=3, is_dj=False)  # history
    assert not can_move(4, current=3, is_dj=True)  # DJ break
    assert can_move(0, current=None, is_dj=False)


def test_drops_land_after_the_playing_track():
    assert can_drop(4, n_rows=10, current=3)
    assert not can_drop(3, n_rows=10, current=3)  # above the playing row
    assert can_drop(10, n_rows=10, current=3)  # end of the queue
    assert not can_drop(11, n_rows=10, current=3)
    assert not can_drop(-1, n_rows=10, current=None)


class FakeQueues:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def move_item(self, qid, item_id, shift):
        self.calls.append((qid, item_id, shift))
        if self.fail:
            raise RuntimeError("item is buffered")


def make_backend(queues):
    b = Backend.__new__(Backend)
    b.client = SimpleNamespace(player_queues=queues)
    b._tasks = set()
    b._qid = lambda: "q1"
    return b


def run_move(b, shift, failures):
    async def go():
        b.move_queue_item("item", shift, failures.append)
        await asyncio.gather(*b._tasks)

    asyncio.run(go())


def test_zero_shift_sends_nothing():
    # pos_shift=0 would tell the server "play next"
    queues = FakeQueues()
    run_move(make_backend(queues), 0, [])
    assert queues.calls == []


def test_move_sends_shift_and_reports_refusal():
    queues, failures = FakeQueues(fail=True), []
    run_move(make_backend(queues), -2, failures)
    assert queues.calls == [("q1", "item", -2)]
    assert len(failures) == 1
