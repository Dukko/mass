"""Pager: generation drop, exhaustion, retry cooldown, first-page flag."""

import asyncio

import pytest

from mass.ui import paging
from mass.ui.paging import Pager


class Harness:
    def __init__(self, pages, page=2):
        self.pages = list(pages)
        self.calls = []
        self.received = []
        self.tasks = []
        self.gate = None
        self.pager = Pager(self.fetch, lambda items, first: self.received.append((items, first)), self.run, page=page)

    async def fetch(self, offset, limit):
        self.calls.append(offset)
        if self.gate is not None:
            await self.gate.wait()
        result = self.pages.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def run(self, coro):
        self.tasks.append(asyncio.ensure_future(coro))

    async def settle(self):
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()


def test_fetch_begun_before_reset_never_reaches_on_items():
    async def scenario():
        h = Harness([["stale-1", "stale-2"], ["fresh-1", "fresh-2"]])
        h.gate = asyncio.Event()
        h.pager.load_more()
        await asyncio.sleep(0)
        h.pager.reset()
        h.pager.load_more()
        await asyncio.sleep(0)
        h.gate.set()
        await h.settle()
        return h.received

    assert asyncio.run(scenario()) == [(["fresh-1", "fresh-2"], True)]


def test_short_page_stops_further_fetches():
    async def scenario():
        h = Harness([["a", "b"], ["c"], ["never"]])
        for _ in range(4):
            h.pager.load_more()
            await h.settle()
        return h.calls, h.received

    calls, received = asyncio.run(scenario())
    assert calls == [0, 2]
    assert received == [(["a", "b"], True), (["c"], False)]


def test_failed_fetch_blocks_load_more_until_cooldown_passes(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(paging.time, "monotonic", lambda: clock[0])

    async def scenario():
        h = Harness([RuntimeError("boom"), ["a", "b"]])
        h.pager.load_more()
        await h.settle()
        h.pager.load_more()
        await h.settle()
        calls_during_cooldown = list(h.calls)
        clock[0] += paging.RETRY_COOLDOWN + 1
        h.pager.load_more()
        await h.settle()
        return calls_during_cooldown, h.calls, h.received

    during, calls, received = asyncio.run(scenario())
    assert during == [0]
    assert calls == [0, 0]
    assert received == [(["a", "b"], True)]


def test_failed_fetch_error_propagates_to_runner():
    async def scenario():
        h = Harness([RuntimeError("boom")])
        h.pager.load_more()
        await asyncio.gather(*h.tasks, return_exceptions=False)

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(scenario())


def test_first_page_flag_true_only_for_offset_zero():
    async def scenario():
        h = Harness([["a", "b"], ["c", "d"], ["e"]])
        for _ in range(3):
            h.pager.load_more()
            await h.settle()
        return [first for _items, first in h.received]

    assert asyncio.run(scenario()) == [True, False, False]
