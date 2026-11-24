"""The sendspin environment file is double-parsed (systemd, then `eval` in the unit)."""

import re
import shlex
from types import SimpleNamespace

import mass.endpoint as endpoint


def test_write_env_round_trips_quoting(tmp_path, monkeypatch):
    env = tmp_path / "sendspin.env"
    monkeypatch.setattr(endpoint, "ENV_FILE", env)
    monkeypatch.setattr(endpoint, "CONFIG_DIR", tmp_path)
    ep = endpoint.Endpoint(SimpleNamespace(
        endpoint_name='Kitchen "Main" $HOME `x`',
        endpoint_audio_device="pw: sink with spaces",
        endpoint_hardware_volume=True,
    ))

    ep._write_env()

    line = env.read_text().strip()
    prefix = 'SENDSPIN_ARGS="'
    assert line.startswith(prefix)
    assert line.endswith('"')
    raw = line[len(prefix):-1]
    # systemd removes one level of backslash escaping before the unit's shell sees the value
    assert shlex.split(re.sub(r"\\(.)", r"\1", raw)) == ep.args()
