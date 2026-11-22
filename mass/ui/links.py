"""Opening the Music Assistant web app from the UI."""

from __future__ import annotations

from gi.repository import Gio, GLib, Gtk


def _toast(widget: Gtk.Widget, message: str) -> None:
    root = widget.get_root()
    if hasattr(root, "toast"):
        root.toast(message)


def open_webapp(widget: Gtk.Widget, backend, route: str = "") -> None:
    url = backend.webapp_url(route)
    if url is None:
        _toast(widget, "Not connected to a server")
        return

    def done(launcher: Gtk.UriLauncher, result: Gio.AsyncResult) -> None:
        try:
            launcher.launch_finish(result)
        except GLib.Error as err:
            if not err.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                _toast(widget, "Couldn't open the web app")

    Gtk.UriLauncher.new(url).launch(widget.get_root(), None, done)
