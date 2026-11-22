"""Which navigation page opens for a media item."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw
from music_assistant_models.enums import MediaType

from .browse import BrowsePage
from .detail import DetailPage
from .genre import GenrePage
from .podcast import PodcastPage

PAGES: dict[MediaType, Callable[..., Adw.NavigationPage]] = {
    MediaType.FOLDER: BrowsePage,
    MediaType.GENRE: GenrePage,
    MediaType.PODCAST: PodcastPage,
}


def page_for(backend, item, open_item) -> Adw.NavigationPage:
    return PAGES.get(item.media_type, DetailPage)(backend, item, open_item)
