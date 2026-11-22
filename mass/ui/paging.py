"""GTK-free paging state shared by lazily loaded lists."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Coroutine

PAGE = 100
RETRY_COOLDOWN = 5.0


class Pager:
    """Walks `fetch(offset, limit)` page by page, dropping answers that predate a `reset()`."""

    def __init__(
        self,
        fetch: Callable[[int, int], Awaitable[list]],
        on_items: Callable[[list, bool], None],
        run: Callable[[Coroutine], None],
        page: int = PAGE,
        retry_cooldown: float = RETRY_COOLDOWN,
    ) -> None:
        self._fetch, self._on_items, self._run = fetch, on_items, run
        self._page, self._retry_cooldown = page, retry_cooldown
        self._offset = 0
        self._exhausted = False
        self._loading = False
        self._generation = 0
        self._retry_after = 0.0
        self.loaded_once = False

    def reset(self) -> None:
        self._generation += 1
        self._offset, self._exhausted, self.loaded_once = 0, False, False
        self._loading = False
        self._retry_after = 0.0

    def load_more(self) -> None:
        if self._loading or self._exhausted:
            return
        if time.monotonic() < self._retry_after:
            return
        self._loading = True
        self.loaded_once = True
        generation = self._generation
        offset = self._offset

        async def go() -> None:
            try:
                items = await self._fetch(offset, self._page)
            except Exception:
                if generation == self._generation:
                    self._loading = False
                    self._retry_after = time.monotonic() + self._retry_cooldown
                raise
            if generation != self._generation:
                return
            self._loading = False
            self._offset += len(items)
            self._exhausted = len(items) < self._page
            self._on_items(items, offset == 0)

        self._run(go())
