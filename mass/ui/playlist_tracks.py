"""GTK-free helpers for showing long playlists."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

# Above this many tracks a playlist is shown read-only in a virtualized list instead of the ListBox editor.
EDITOR_MAX = 500


class WholeList:
    """Adapts a one-shot "give me everything" loader to the Pager's `fetch(offset, limit)`.

    The first call returns every item (a list that was already loaded can be handed in as `primed` and is
    used once); later offsets are empty, so the Pager is exhausted after that single page.
    """

    def __init__(self, load: Callable[[], Awaitable[list]], primed: list | None = None) -> None:
        self._load = load
        self._primed = primed

    async def __call__(self, offset: int, limit: int) -> list:
        if offset > 0:
            return []
        if self._primed is not None:
            items, self._primed = self._primed, None
            return items
        return await self._load()
