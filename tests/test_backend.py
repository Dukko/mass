"""Pure queue selection and elapsed-time logic (no server, no widgets)."""

from types import SimpleNamespace

import mass.backend as backend
from mass.backend import Backend
from music_assistant_models.enums import PlaybackState


class Lookup:
    def __init__(self, items):
        self.items = items

    def get(self, key):
        return self.items.get(key)


def make_backend(players=(), queues=()):
    b = Backend.__new__(Backend)
    b.client = SimpleNamespace(players=Lookup(dict(players)), player_queues=Lookup(dict(queues)))
    return b


def player(pid, **kw):
    attrs = dict(player_id=pid, active_group=None, synced_to=None, current_media=None)
    attrs.update(kw)
    return SimpleNamespace(**attrs)


def queue(qid):
    q = SimpleNamespace(queue_id=qid, current_item=None)
    return q


def test_supports_compares_server_schema():
    b = make_backend()
    assert not b.supports(34)  # client without server_info yet
    b.client.server_info = SimpleNamespace(schema_version=35)
    assert b.supports(34)
    assert b.supports(35)
    assert not b.supports(36)
    b.client = None
    assert not b.supports(28)


def test_queue_for_uses_leader_source_or_player_id():
    leader = player("leader", active_source="q-source")
    q_source = queue("q-source")
    b = make_backend(players={"leader": leader}, queues={"q-source": q_source})
    follower = player("follower", synced_to="leader")
    assert b.queue_for(follower) is q_source

    leader.active_source = None
    q_leader = queue("leader")
    b = make_backend(players={"leader": leader}, queues={"leader": q_leader})
    assert b.queue_for(follower) is q_leader


def test_queue_for_returns_none_without_client_or_player():
    b = Backend.__new__(Backend)
    b.client = None
    assert b.queue_for(player("p")) is None
    b = make_backend(players={"p": player("p")}, queues={"p": queue("p")})
    assert b.queue_for(None) is None


def test_queue_for_rejects_stale_queue_for_external_playback():
    leader = player("leader", active_source="q")
    leader.current_media = SimpleNamespace(queue_item_id=None, uri="spotify://track/1")
    q = queue("q")
    q.current_item = SimpleNamespace(uri="library://track/2")
    b = make_backend(players={"leader": leader}, queues={"q": q})
    assert b.queue_for(leader) is None

    q.current_item = SimpleNamespace(uri="spotify://track/1")
    assert b.queue_for(leader) is q


def test_elapsed_interpolates_and_clamps(monkeypatch):
    monkeypatch.setattr(backend.time, "time", lambda: 1005.0)
    b = Backend.__new__(Backend)
    q = SimpleNamespace(elapsed_time=10.0, elapsed_time_last_updated=1000.0,
                        state=PlaybackState.PLAYING, playback_speed=1.0,
                        current_item=SimpleNamespace(duration=100.0))
    assert b.elapsed(q) == 15.0

    q.elapsed_time = 95.0
    assert b.elapsed(q) == 100.0


def test_elapsed_paused_does_not_interpolate(monkeypatch):
    monkeypatch.setattr(backend.time, "time", lambda: 1005.0)
    b = Backend.__new__(Backend)
    q = SimpleNamespace(elapsed_time=10.0, elapsed_time_last_updated=1000.0,
                        state=PlaybackState.PAUSED, playback_speed=1.0,
                        current_item=SimpleNamespace(duration=None))
    assert b.elapsed(q) == 10.0


def test_elapsed_external_source(monkeypatch):
    monkeypatch.setattr(backend.time, "time", lambda: 1005.0)
    b = Backend.__new__(Backend)
    b.client = None
    p = SimpleNamespace(elapsed_time=5.0, elapsed_time_last_updated=1000.0,
                        playback_state=PlaybackState.PLAYING,
                        current_media=SimpleNamespace(duration=10.0))
    monkeypatch.setattr(Backend, "active_player", property(lambda _self: p))
    assert b.elapsed() == 10.0


def test_dj_for_handles_string_and_dict_status():
    b = Backend.__new__(Backend)
    b.dj_hosts = {"host1": {"id": "host1", "name": "DJ Alice"}}

    # Legacy schema: queue_id -> host_id string
    b.dj_map = {"q1": "host1"}
    assert b.dj_for("q1") == {"id": "host1", "name": "DJ Alice"}

    # New schema: queue_id -> dict with host_id and station_id
    b.dj_map = {"q1": {"host_id": "host1", "station_id": ""}}
    assert b.dj_for("q1") == {"id": "host1", "name": "DJ Alice"}

    # Unknown or unassigned
    assert b.dj_for("unknown") is None
    assert b.dj_for(None) is None


def test_same_origin_requires_scheme_host_and_port_match():
    same = backend._same_origin
    assert same("https://ma.lan/imageproxy/x", "https://ma.lan")
    assert same("https://ma.lan:443/x", "https://ma.lan")
    assert same("http://ma.lan:8095/x", "http://ma.lan:8095")

    assert not same("http://ma.lan/x", "https://ma.lan")
    assert not same("https://ma.lan:8443/x", "https://ma.lan")
    # prefix tricks must not pass
    assert not same("https://ma.lan@evil.example/x", "https://ma.lan")
    assert not same("https://ma.lan.evil.example/x", "https://ma.lan")
    # malformed / missing parts
    assert not same("https://ma.lan:notaport/x", "https://ma.lan")
    assert not same("/relative/x", "https://ma.lan")
