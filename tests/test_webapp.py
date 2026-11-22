"""Web app deep links."""

from types import SimpleNamespace

from music_assistant_models.enums import MediaType

from mass.webapp import item_route, player_route, webapp_url


def test_item_id_with_slashes_spaces_and_colons_is_one_segment():
    item = SimpleNamespace(media_type=MediaType.ALBUM, provider="filesystem_local--abc",
                           item_id="/music/My Album:1")

    assert item_route(item) == "albums/filesystem_local--abc/%2Fmusic%2FMy%20Album%3A1"


def test_unsupported_media_type_has_no_route():
    item = SimpleNamespace(media_type=MediaType.PODCAST_EPISODE, provider="p", item_id="1")

    assert item_route(item) is None


def test_trailing_slash_on_base_is_ignored():
    assert webapp_url("http://ma:8095/", "settings") == "http://ma:8095/#/settings"
    assert webapp_url("http://ma:8095") == "http://ma:8095/#/"


def test_player_route_adds_dsp_suffix():
    assert player_route("up 1") == "settings/editplayer/up%201"
    assert player_route("up1", dsp=True) == "settings/editplayer/up1/dsp"
