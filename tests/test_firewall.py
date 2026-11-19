"""Firewall rule parsing: the host firewall is only read, never changed."""

from mass.firewall import _port_in, _ufw_allows


def test_port_in_single_and_range():
    assert _port_in("8928", 8928)
    assert not _port_in("8928", 8929)
    assert _port_in("8000:9000", 8928)
    assert not _port_in("9000:9500", 8928)


def test_port_in_list_and_any():
    assert _port_in("80,8000:9000", 8928)
    assert _port_in("any", 1)
    assert not _port_in("", 8928)
    assert not _port_in("nonsense", 8928)


def test_ufw_allows_incoming_tcp():
    rules = "### tuple ### allow tcp 8928 0.0.0.0/0 any 0.0.0.0/0 in\n"
    assert _ufw_allows(rules, 8928)


def test_ufw_port_range_and_any_protocol():
    assert _ufw_allows("### tuple ### allow any 8000:9000 0.0.0.0/0 any 0.0.0.0/0 in\n", 8928)
    assert _ufw_allows("### tuple ### allow tcp 80,8928 0.0.0.0/0 any 0.0.0.0/0 in\n", 8928)


def test_ufw_ignores_deny_out_and_interface_rules():
    assert not _ufw_allows("### tuple ### deny tcp 8928 0.0.0.0/0 any 0.0.0.0/0 in\n", 8928)
    assert not _ufw_allows("### tuple ### allow udp 8928 0.0.0.0/0 any 0.0.0.0/0 in\n", 8928)
    assert not _ufw_allows("### tuple ### allow tcp 8928 0.0.0.0/0 any 0.0.0.0/0 out\n", 8928)
    assert not _ufw_allows(
        "### tuple ### allow tcp 8928 0.0.0.0/0 any 0.0.0.0/0 in_tailscale0\n", 8928)


def test_ufw_ignores_unrelated_lines():
    assert not _ufw_allows("### tuple ### allow tcp 22 0.0.0.0/0 any 0.0.0.0/0 in\n", 8928)
    assert not _ufw_allows("# a comment\n", 8928)
