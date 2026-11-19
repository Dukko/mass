"""mDNS server URL assembly."""

from mass.discovery import _url_for


def test_url_adds_port_when_base_url_has_none():
    assert _url_for(None, "192.168.1.5", 8095) == "http://192.168.1.5:8095"
    assert _url_for("http://ma.local", "192.168.1.5", 8095) == "http://ma.local:8095"


def test_url_keeps_explicit_port():
    assert _url_for("http://ma.local:8095", "192.168.1.5", 8095) == "http://ma.local:8095"
    assert _url_for("https://ma.example:443", "192.168.1.5", 8095) == "https://ma.example:443"
