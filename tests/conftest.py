"""Set typelib versions before any test imports a module that pulls in GTK."""

import gi

for _ns, _version in (("Gtk", "4.0"), ("Gdk", "4.0"), ("Adw", "1"), ("Secret", "1")):
    gi.require_version(_ns, _version)
