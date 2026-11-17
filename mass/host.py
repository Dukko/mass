"""Host vs. Flatpak sandbox helpers.

Inside Flatpak the app keeps using the *host's* ~/.config/mass and ~/.cache/dms (granted via
--filesystem) so a Flatpak and a native install share settings, the matugen template target,
and the systemd unit's EnvironmentFile. Host tools (matugen, systemctl) run via flatpak-spawn.
"""

from __future__ import annotations

import os
from pathlib import Path

from gi.repository import GLib

from . import APP_ID

IN_FLATPAK = Path("/.flatpak-info").exists()

if IN_FLATPAK:
    HOST_CONFIG = Path(os.environ.get("HOST_XDG_CONFIG_HOME") or Path.home() / ".config")
    HOST_CACHE = Path(os.environ.get("HOST_XDG_CACHE_HOME") or Path.home() / ".cache")
else:
    HOST_CONFIG = Path(GLib.get_user_config_dir())
    HOST_CACHE = Path(GLib.get_user_cache_dir())


def host_cmd(argv: list[str]) -> list[str]:
    """Run argv on the host (outside the sandbox) when we are a Flatpak."""
    return ["flatpak-spawn", "--host", *argv] if IN_FLATPAK else argv


def launch_cmd() -> str:
    """Command line that starts this app from a .desktop file / systemd on the host."""
    return f"flatpak run {APP_ID}" if IN_FLATPAK else "mass"


def data_file(*parts: str) -> Path | None:
    """Locate a bundled data file: source checkout, then installed prefixes."""
    here = Path(__file__).resolve().parent.parent / "data"
    for base in (here, Path("/app/share/mass"), Path(GLib.get_user_data_dir()) / "mass", Path("/usr/share/mass")):
        if (p := base.joinpath(*parts)).exists():
            return p
    return None
