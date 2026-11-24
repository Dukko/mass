from mass.ui.dialogs import validated_server_url


def test_server_url_requires_an_http_scheme():
    assert validated_server_url("https://ma.lan:8095") == "https://ma.lan:8095"
    assert validated_server_url(" http://ma.lan ") == "http://ma.lan"
    assert validated_server_url("ma.lan") is None
    assert validated_server_url("ftp://ma.lan") is None
    assert validated_server_url("") is None
