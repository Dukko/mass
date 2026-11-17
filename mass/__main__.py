"""Entry point: GTK + asyncio on one GLib main loop."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import warnings

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("MASS_LOG", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("music_assistant_client").setLevel(logging.WARNING)

    from gi.events import GLibEventLoopPolicy

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(GLibEventLoopPolicy())

    from gi.repository import GLib

    from . import APP_ID, APP_NAME
    from .app import MassApp

    GLib.set_prgname(APP_ID)  # matches the .desktop file / WM class
    GLib.set_application_name(APP_NAME)

    return MassApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
