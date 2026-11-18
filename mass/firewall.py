"""Best-effort check whether the host firewall drops incoming connections to the Sendspin port.

Sendspin is server-initiated: Music Assistant finds this PC over mDNS and then connects *to* it,
so a default-deny firewall silently keeps the local player from ever appearing.
Supports ufw (rule files are world-readable) and firewalld (unprivileged --query-port).
Reads the host's files via flatpak-spawn when sandboxed.
"""

from __future__ import annotations

import ipaddress
import socket

from gi.repository import Gio, GLib

from .host import host_cmd

SENDSPIN_PORT = 8928


def _run(argv: list[str]) -> tuple[bool, str]:
    try:
        p = Gio.Subprocess.new(host_cmd(argv), Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        _ok, out, _ = p.communicate_utf8(None, None)
        return p.get_successful(), out or ""
    except GLib.Error:
        return False, ""


def _read(path: str) -> str:
    return _run(["cat", path])[1]


def _port_in(spec: str, port: int) -> bool:
    if spec == "any":
        return True
    for part in spec.split(","):
        lo, _, hi = part.partition(":")
        try:
            if int(lo) <= port <= int(hi or lo):
                return True
        except ValueError:
            pass
    return False


def _ufw_allows(rules: str, port: int) -> bool:
    for line in rules.splitlines():
        if not line.startswith("### tuple ###"):
            continue
        f = line.split()[3:]  # action proto dport dst sport src [direction…]
        if len(f) >= 6 and f[0] == "allow" and f[1] in ("tcp", "any") and _port_in(f[2], port):
            direction = f[6] if len(f) > 6 and not f[6].startswith("comment=") else "in"
            if direction == "in":  # not "out", nor limited to one interface ("in_tailscale0")
                return True
    return False


def local_subnet() -> str:
    """This PC's LAN as a /24 (what the fix command allows), e.g. 192.168.4.0/24."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # no packet is sent; just picks the outgoing interface
            ip = s.getsockname()[0]
        return str(ipaddress.ip_network(f"{ip}/24", strict=False))
    except OSError:
        return "192.168.0.0/16"


def check(port: int = SENDSPIN_PORT) -> tuple[str, str] | None:
    """(firewall name, command that opens the port) if it looks blocked, else None."""
    if _run(["systemctl", "is-active", "--quiet", "ufw"])[0] and "ENABLED=yes" in _read("/etc/ufw/ufw.conf"):
        default = _read("/etc/default/ufw")
        if 'DEFAULT_INPUT_POLICY="ACCEPT"' not in default:
            rules = _read("/etc/ufw/user.rules")
            if rules and not _ufw_allows(rules, port):
                return ("ufw", f"sudo ufw allow from {local_subnet()} to any port {port} proto tcp "
                               "comment 'Mass Sendspin player'")
        return None
    ok, state = _run(["firewall-cmd", "--state"])
    if ok and state.strip() == "running":
        if not _run(["firewall-cmd", f"--query-port={port}/tcp"])[0]:
            return ("firewalld", f"sudo firewall-cmd --permanent --add-port={port}/tcp && sudo firewall-cmd --reload")
    return None
