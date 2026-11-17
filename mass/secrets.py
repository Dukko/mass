"""Token storage in the freedesktop Secret Service (gnome-keyring, KeePassXC, ...)."""

from __future__ import annotations

import logging

import gi

gi.require_version("Secret", "1")
from gi.repository import Secret  # noqa: E402

log = logging.getLogger(__name__)

_SCHEMA = Secret.Schema.new(
    "io.github.mass_matugen.Token",
    Secret.SchemaFlags.NONE,
    {"server": Secret.SchemaAttributeType.STRING},
)


def load_token(server_url: str) -> str | None:
    # An explicit search, not password_lookup_sync: inside Flatpak (SECRET_BACKEND=service) the
    # convenience lookup can come back empty when the keyring holds stale item paths.
    try:
        svc = Secret.Service.get_sync(Secret.ServiceFlags.NONE, None)
        flags = Secret.SearchFlags.UNLOCK | Secret.SearchFlags.LOAD_SECRETS
        for item in svc.search_sync(_SCHEMA, {"server": server_url}, flags, None) or []:
            if (secret := item.get_secret()) and (text := secret.get_text()):
                return text
    except Exception as err:  # no secret service running, locked, ...
        log.debug("Secret Service search failed: %s", err)
    try:
        return Secret.password_lookup_sync(_SCHEMA, {"server": server_url}, None)
    except Exception as err:
        log.warning("Secret Service lookup failed: %s", err)
        return None


def store_token(server_url: str, token: str) -> bool:
    try:
        return Secret.password_store_sync(
            _SCHEMA,
            {"server": server_url},
            Secret.COLLECTION_DEFAULT,
            f"Music Assistant token ({server_url})",
            token,
            None,
        )
    except Exception as err:
        log.error("Secret Service store failed: %s", err)
        return False


def clear_token(server_url: str) -> None:
    try:
        Secret.password_clear_sync(_SCHEMA, {"server": server_url}, None)
    except Exception as err:
        log.warning("Secret Service clear failed: %s", err)
