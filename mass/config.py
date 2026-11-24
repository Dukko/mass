"""Persistent settings stored as JSON in $XDG_CONFIG_HOME/mass/config.json.

The auth token is NOT stored here; it lives in the Secret Service (see secrets.py).
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from gi.repository import GLib

from .host import HOST_CONFIG

CONFIG_DIR = HOST_CONFIG / "mass"  # host path, also inside Flatpak
CACHE_DIR = Path(GLib.get_user_cache_dir()) / "mass"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class Config:
    server_url: str = ""
    username: str = ""
    # last selected MA player
    active_player_id: str = ""
    # theme: "system" (matugen/DMS palette, live reload) | "artwork" (now-playing cover)
    # | "adwaita" (no custom colors)
    theme_mode: str = "system"
    matugen_scheme: str = "scheme-tonal-spot"
    # window surfaces, 0 (opaque) .. 90 percent see-through
    transparency: int = 0
    # ask the compositor to blur what's behind (ext-background-effect-v1)
    blur: bool = True
    # hidden classic-Winamp skin, toggled with the Konami code
    winamp: bool = False
    # local playback endpoint (sendspin daemon)
    endpoint_enabled: bool = True
    endpoint_name: str = field(default_factory=lambda: f"{socket.gethostname()} (Mass)")
    endpoint_audio_device: str = "pipewire"
    endpoint_hardware_volume: bool = False
    # desktop integration
    run_in_background: bool = True
    notifications: bool = True
    mpris: bool = True
    tray: bool = True
    # flatpak: download updates in the background (restart still asks)
    auto_update: bool = True
    # now playing
    np_tab: str = "lyrics"  # lyrics | queue | dj | about
    wavy_progress: bool = True
    # AI DJ host id -> Material Symbols name shown for that host (default: AI_ICON)
    dj_icons: dict[str, str] = field(default_factory=dict)
    # library tab name -> ListingState as plain JSON (see listing.state_to_json)
    library_listings: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls) -> Config:
        try:
            data = json.loads(CONFIG_FILE.read_text())
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        config = cls(**{k: v for k, v in data.items() if k in known})
        if not isinstance(config.library_listings, dict):
            config.library_listings = {}
        return config

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        os.replace(tmp, CONFIG_FILE)
