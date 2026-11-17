"""Global search across all providers (M3 Expressive search bar + segmented results)."""

from __future__ import annotations

from gi.repository import Adw, GLib, Gtk
from music_assistant_models.enums import MediaType

from .common import ArtImage, artists_str, label, m3_list, media_row
from .expressive import Sym
from .library import subtitle_for

SECTIONS = (
    ("tracks", "Tracks"),
    ("albums", "Albums"),
    ("artists", "Artists"),
    ("playlists", "Playlists"),
    ("radio", "Radio"),
    ("podcasts", "Podcasts"),
)


class SearchView(Gtk.Box):
    def __init__(self, backend, open_item) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.backend, self.open_item = backend, open_item
        self._timer = 0
        self._seq = 0

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_start=28, margin_end=28,
                       margin_top=56, margin_bottom=8)
        head.append(label("Search", "display-m", ellipsize=False))
        bar = Gtk.Box(spacing=12)
        bar.add_css_class("search-bar")
        bar.append(Sym("search", 24))
        self.entry = Gtk.Text(placeholder_text="Artists, albums, songs, radio…", hexpand=True)
        bar.append(self.entry)
        self.lib_only = Gtk.ToggleButton(label="Library only", valign=Gtk.Align.CENTER)
        self.lib_only.add_css_class("chip")
        self.lib_only.connect("toggled", lambda *_: self._schedule(0))
        bar.append(self.lib_only)
        clamp = Adw.Clamp(maximum_size=760, child=bar, halign=Gtk.Align.FILL)
        head.append(clamp)
        self.append(head)

        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(Adw.StatusPage(icon_name="system-search-symbolic", title="Find something to play",
                                            description="Results come from every connected provider."), "idle")
        self.stack.add_named(Adw.StatusPage(icon_name="edit-find-symbolic", title="No results"), "none")
        self.stack.add_named(Adw.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                         width_request=48, height_request=48), "loading")
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        rc = Adw.Clamp(maximum_size=980, margin_start=20, margin_end=20, margin_bottom=120)
        self.results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        rc.set_child(self.results)
        scroller.set_child(rc)
        self.stack.add_named(scroller, "results")
        self.append(self.stack)

        self.entry.connect("changed", lambda *_: self._schedule(250))
        self.entry.connect("activate", lambda *_: self._schedule(0))

    def focus(self) -> None:
        self.entry.grab_focus()

    def _schedule(self, delay: int) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(max(delay, 1), self._run)

    def _run(self) -> bool:
        self._timer = 0
        q = self.entry.get_text().strip()
        if not q:
            self.stack.set_visible_child_name("idle")
            return False
        if not self.backend.connected:
            return False
        self._seq += 1
        seq = self._seq
        self.stack.set_visible_child_name("loading")

        async def go():
            res = await self.backend.client.music.search(q, limit=12, library_only=self.lib_only.get_active())
            if seq == self._seq:
                self._show(res)

        self.backend.run(go())
        return False

    def _show(self, res) -> None:
        while child := self.results.get_first_child():
            self.results.remove(child)
        any_ = False
        for attr, title in SECTIONS:
            items = getattr(res, attr, None) or []
            if not items:
                continue
            any_ = True
            self.results.append(label(title, "section-title", margin_top=12))
            if attr in ("artists", "albums", "playlists", "radio", "podcasts"):
                self.results.append(self._carousel(items))
                continue
            lb = m3_list()
            for it in items:
                album = getattr(it, "album", None)
                sub = " · ".join(x for x in (artists_str(it), album.name if album else "") if x)
                lb.append(media_row(self.backend, it, sub, on_activate=lambda t: self.backend.play_media(t)))
            self.results.append(lb)
        self.stack.set_visible_child_name("results" if any_ else "none")

    def _carousel(self, items) -> Gtk.Widget:
        sw = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER)
        row = Gtk.Box(spacing=4)
        for it in items:
            btn = Gtk.Button()
            btn.add_css_class("flat")
            btn.add_css_class("tile")
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            art = ArtImage(self.backend, 140)
            art.set_item(it)
            col.append(art)
            xa = 0.5 if it.media_type in (MediaType.ARTIST, MediaType.RADIO) else 0
            col.append(label(it.name, "title-s", xalign=xa, max_width_chars=1, margin_top=6))
            col.append(label(subtitle_for(it), "label-m", "on-variant", xalign=xa, max_width_chars=1))
            col.set_size_request(140, -1)
            btn.set_child(col)
            if it.media_type in (MediaType.ALBUM, MediaType.ARTIST, MediaType.PLAYLIST):
                btn.connect("clicked", lambda _b, i=it: self.open_item(i))
            else:
                btn.connect("clicked", lambda _b, i=it: self.backend.play_media(i))
            row.append(btn)
        sw.set_child(row)
        return sw
