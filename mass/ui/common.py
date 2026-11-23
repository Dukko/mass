"""Shared widgets and helpers."""

from __future__ import annotations

import asyncio
import weakref
from collections import OrderedDict

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk
from music_assistant_models.enums import MediaType, QueueOption

from ..backend import FAVORITABLE
from ..webapp import item_route
from .expressive import ShapeArt, Sym, sym_button
from .links import open_webapp

PLACEHOLDER_ICONS = {
    MediaType.ARTIST: "avatar-default-symbolic",
    MediaType.PLAYLIST: "view-list-symbolic",
    MediaType.RADIO: "network-wireless-symbolic",
    MediaType.PODCAST: "microphone-sensitivity-high-symbolic",
    MediaType.AUDIOBOOK: "accessories-dictionary-symbolic",
    MediaType.GENRE: "view-grid-symbolic",
    MediaType.PODCAST_EPISODE: "microphone-sensitivity-high-symbolic",
    MediaType.FOLDER: "folder-symbolic",
}

# M3E: vary shapes by content type
SHAPE_FOR = {
    MediaType.ARTIST: "circle",
    MediaType.PLAYLIST: "cookie9",
    MediaType.RADIO: "sunny",
    MediaType.PODCAST: "clover",
    MediaType.AUDIOBOOK: "cookie6",
    MediaType.GENRE: "cookie6",
    MediaType.PODCAST_EPISODE: "clover",
    MediaType.FOLDER: "square",
}


def weak_method(obj: object, name: str):
    """A callback to `obj.<name>` that does not keep `obj` alive.

    A page that hands its own bound methods to widgets it owns, or to longer-lived objects, forms a
    cycle through GTK that Python's collector cannot break, so the popped page would never be freed.
    Once `obj` is gone the callback does nothing and returns None. A widget must be held by
    something in Python for this to find it: see `retain_while_rooted`.
    """
    ref = weakref.ref(obj)

    def call(*args, **kwargs):
        target = ref()
        return getattr(target, name)(*args, **kwargs) if target is not None else None

    return call


def retain_while_rooted(widget: Gtk.Widget) -> None:
    """Keep `widget`'s Python wrapper alive for as long as it sits in a window.

    PyGObject drops a wrapper that nothing in Python refers to, even while GTK still owns the
    widget, and a `weak_method` on that wrapper would then silently stop firing. Retaining the
    wrapper from its creation until it leaves the window lets the widget's own children call back
    into it weakly, so a popped widget is still freed.
    """
    widget._retained = widget
    state = {"rooted": False}

    def on_root(w: Gtk.Widget, _pspec) -> None:
        if w.get_root() is not None:
            state["rooted"] = True
            w._retained = w
        elif state["rooted"]:
            w._retained = None

    widget.connect_after("notify::root", on_root)


def connect_weak(emitter: GObject.Object, signal: str, obj: object, name: str) -> int:
    """`emitter.connect(signal, ...)` to `obj.<name>` without keeping `obj` alive.

    A popped page can be freed before it gets the chance to disconnect, so the handler also drops
    itself the first time the signal fires after `obj` is gone. Returns the handler id.
    """
    ref = weakref.ref(obj)

    def call(*args):
        target = ref()
        if target is None:
            emitter.disconnect(handler_id)
            return None
        return getattr(target, name)(*args)

    handler_id = emitter.connect(signal, call)
    return handler_id


def fmt_time(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def artists_str(item) -> str:
    artists = getattr(item, "artists", None) or []
    return ", ".join(a.name for a in artists)


def label(text: str = "", *classes: str, xalign: float = 0, ellipsize: bool = True, **kw) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=xalign, **kw)
    if ellipsize:
        lbl.set_ellipsize(3)
    for c in classes:
        lbl.add_css_class(c)
    return lbl


# decoded textures shared by every ArtImage (recycled grid cells re-bind instantly)
_TEXTURES: OrderedDict[str, tuple[Gdk.Texture, bytes]] = OrderedDict()
_TEXTURE_CACHE_MAX = 300


class ArtImage(ShapeArt):
    """Lazily loaded cover art in an M3E shape."""

    __gtype_name__ = "MassArtImage"

    def __init__(self, backend, size: int, shape: str = "square", icon: str = "audio-x-generic-symbolic",
                 fetch_size: int | None = None, keep_stale: bool = False) -> None:
        super().__init__(size, shape, icon)
        self.backend = backend
        self.fetch_size = fetch_size if fetch_size is not None else (size * 2 if size < 300 else 0)
        self.keep_stale = keep_stale
        self._url: str | None = None
        self._load_gen = 0
        self.bytes: bytes | None = None

    def set_item(self, item, shape_by_type: bool = True) -> None:
        mt = getattr(item, "media_type", None) if item is not None else None
        if mt in PLACEHOLDER_ICONS:
            self.set_icon(PLACEHOLDER_ICONS[mt])
        if shape_by_type:
            self.set_shape(SHAPE_FOR.get(mt, "square"), animate=False)
        self.set_url(self.backend.image_url(item, self.fetch_size) if item is not None else None)

    def set_url(self, url: str | None, on_loaded=None) -> None:
        if url == self._url:
            if on_loaded and self.bytes:
                on_loaded(self.bytes)
            return
        self._url = url
        self._load_gen += 1
        gen = self._load_gen
        if not url:
            self.bytes = None
            self.set_texture(None, animate=False)
            return
        if (hit := _TEXTURES.get(url)) is not None:
            _TEXTURES.move_to_end(url)
            self.bytes = hit[1]
            self.set_texture(hit[0], animate=False)
            if on_loaded:
                on_loaded(hit[1])
            return
        if not self.keep_stale:
            self.bytes = None
            self.set_texture(None, animate=False)

        async def load():
            data = await self.backend.fetch_image(url)
            if gen != self._load_gen:
                return
            if not data:
                # Don't pin the URL to a failed fetch: rebinding the same item retries it.
                self._url = None
                return
            try:  # decode off the main thread: a big JPEG takes long enough to drop frames
                tex = await asyncio.to_thread(Gdk.Texture.new_from_bytes, GLib.Bytes.new(data))
            except GLib.Error:
                if gen == self._load_gen:
                    self._url = None
                return
            _TEXTURES[url] = (tex, data)
            while len(_TEXTURES) > _TEXTURE_CACHE_MAX:
                _TEXTURES.popitem(last=False)
            if gen != self._load_gen:
                return
            self.bytes = data
            self.set_texture(tex, animate=True)
            if on_loaded:
                on_loaded(data)

        asyncio.ensure_future(load())


class ItemObject(GObject.Object):
    """Wraps a python MA model for Gio.ListStore."""

    def __init__(self, item) -> None:
        super().__init__()
        self.item = item


PLAYLIST_ADDABLE = (MediaType.TRACK, MediaType.ALBUM)


def ask_text(parent: Gtk.Widget, heading: str, body: str, placeholder: str, verb: str, cb, prefill: bool = False) -> None:
    """Prompt for one line of text; cb(text) runs when confirmed."""
    dlg = Adw.AlertDialog(heading=heading, body=body)
    entry = Gtk.Entry(placeholder_text=placeholder, activates_default=True)
    if prefill:
        entry.set_text(placeholder)
    dlg.set_extra_child(entry)
    dlg.add_response("cancel", "Cancel")
    dlg.add_response("ok", verb)
    dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dlg.set_default_response("ok")
    dlg.connect("response", lambda _d, r: r == "ok" and cb(entry.get_text()))
    dlg.present(parent.get_root())


def open_add_to_playlist(widget: Gtk.Widget, backend, items: list) -> None:
    from .dialogs import AddToPlaylistDialog  # dialogs imports this module

    window = widget.get_root()
    AddToPlaylistDialog(backend, items, window).present(window)


def attach_media_actions(widget: Gtk.Widget, backend, get_item):
    """Install `media.*` actions on a widget, operating on get_item().

    Returns a refresh callable: call it whenever get_item() starts returning a different item
    so item-dependent actions (add to playlist) enable or disable accordingly.
    """
    # The handlers live on the actions, which live on the widget. Holding the widget or the actions
    # strongly from them would form a cycle through GTK that Python's collector cannot break, so they
    # reach the window through the application and `refresh` through a weak reference.
    group = Gio.SimpleActionGroup()

    def add(name, option, radio=False):
        act = Gio.SimpleAction.new(name, None)

        def go(*_):
            item = get_item()
            if item is None:
                return
            if radio:
                backend.run(_play_radio(backend, item))
            else:
                backend.play_media(item, option=option)

        act.connect("activate", go)
        group.add_action(act)

    add("play", QueueOption.PLAY)
    add("replace", QueueOption.REPLACE)
    add("next", QueueOption.NEXT)
    add("add", QueueOption.ADD)
    add("radio", None, radio=True)

    def add_to_playlist(*_) -> None:
        if (item := get_item()) is not None and (window := app_window()) is not None:
            open_add_to_playlist(window, backend, [item])

    playlist = Gio.SimpleAction.new("playlist", None)
    playlist.connect("activate", add_to_playlist)
    group.add_action(playlist)

    def toast(message: str) -> None:
        window = app_window()
        if hasattr(window, "toast"):
            window.toast(message)

    refresh_ref: weakref.ref

    def set_favorite(on: bool) -> None:
        item = get_item()
        if item is None:
            return

        def done(_ok: bool) -> None:
            item.favorite = on
            if (refresh := refresh_ref()) is not None:
                refresh()
            toast("Added to favorites" if on else "Removed from favorites")

        backend.toggle_favorite(item, on, done, lambda _err: toast("Couldn't update favorites"))

    favorite = Gio.SimpleAction.new("favorite", None)
    favorite.connect("activate", lambda *_: set_favorite(True))
    group.add_action(favorite)
    unfavorite = Gio.SimpleAction.new("unfavorite", None)
    unfavorite.connect("activate", lambda *_: set_favorite(False))
    group.add_action(unfavorite)

    def open_target(pick) -> None:
        item = get_item()
        target = pick(item) if item is not None else None
        window = app_window()
        if target is not None and hasattr(window, "open_item"):
            window.open_item(target)

    album = Gio.SimpleAction.new("album", None)
    album.connect("activate", lambda *_: open_target(_item_album))
    group.add_action(album)
    artist = Gio.SimpleAction.new("artist", None)
    artist.connect("activate", lambda *_: open_target(_item_artist))
    group.add_action(artist)

    def open_in_webapp(*_) -> None:
        if (item := get_item()) is not None and (window := app_window()) is not None:
            open_webapp(window, backend, item_route(item) or "")

    webapp = Gio.SimpleAction.new("webapp", None)
    webapp.connect("activate", open_in_webapp)
    group.add_action(webapp)

    def refresh() -> None:
        item = get_item()
        media_type = getattr(item, "media_type", None)
        favoritable = media_type in FAVORITABLE
        is_fav = bool(getattr(item, "favorite", False))
        playlist.set_enabled(media_type in PLAYLIST_ADDABLE)
        favorite.set_enabled(favoritable and not is_fav)
        unfavorite.set_enabled(favoritable and is_fav)
        album.set_enabled(_item_album(item) is not None)
        artist.set_enabled(_item_artist(item) is not None)
        webapp.set_enabled(item is not None and item_route(item) is not None)

    refresh_ref = weakref.ref(refresh)
    widget.media_actions_refresh = refresh  # the actions only hold it weakly
    refresh()
    widget.insert_action_group("media", group)
    return refresh


def app_window() -> Gtk.Window | None:
    """The application's window, for handlers that must not hold on to the widget that owns them."""
    app = Gio.Application.get_default()
    return app.get_active_window() if isinstance(app, Gtk.Application) else None


def _item_album(item):
    if getattr(item, "media_type", None) != MediaType.TRACK:
        return None
    return getattr(item, "album", None)


def _item_artist(item):
    if getattr(item, "media_type", None) not in (MediaType.TRACK, MediaType.ALBUM):
        return None
    artists = getattr(item, "artists", None)
    return artists[0] if artists else None


async def _play_radio(backend, item) -> None:
    q, p = backend.active_queue, backend.active_player
    qid = q.queue_id if q else p.player_id
    await backend.client.player_queues.play_media(qid, item, option=QueueOption.REPLACE, radio_mode=True)


def media_menu_model() -> Gio.Menu:
    menu = Gio.Menu()
    sec = Gio.Menu()
    sec.append("Play now", "media.play")
    sec.append("Replace queue", "media.replace")
    sec.append("Play next", "media.next")
    sec.append("Add to queue", "media.add")
    menu.append_section(None, sec)
    sec2 = Gio.Menu()
    sec2.append("Start radio", "media.radio")
    to_playlist = Gio.MenuItem.new("Add to playlist…", "media.playlist")
    to_playlist.set_attribute_value("hidden-when", GLib.Variant("s", "action-disabled"))
    sec2.append_item(to_playlist)
    menu.append_section(None, sec2)
    sec3 = Gio.Menu()
    for text, action in (
        ("Add to favorites", "media.favorite"),
        ("Remove from favorites", "media.unfavorite"),
        ("Go to album", "media.album"),
        ("Go to artist", "media.artist"),
        ("Open in web app", "media.webapp"),
    ):
        entry = Gio.MenuItem.new(text, action)
        entry.set_attribute_value("hidden-when", GLib.Variant("s", "action-disabled"))
        sec3.append_item(entry)
    menu.append_section(None, sec3)
    return menu


def media_menu_button() -> Gtk.MenuButton:
    btn = Gtk.MenuButton(menu_model=media_menu_model(), valign=Gtk.Align.CENTER, tooltip_text="More")
    btn.set_child(Sym("more_vert", 22))
    btn.add_css_class("flat")
    btn.add_css_class("circular")
    return btn


class MediaRow(Gtk.ListBoxRow):
    """Expressive list row: shaped art, two-line text, trailing actions."""

    def __init__(self, backend, item, subtitle: str = "", art: bool = True, leading: Gtk.Widget | None = None,
                 trailing_text: str = "", play_button: bool = True, menu: bool = True) -> None:
        super().__init__(activatable=True)
        self.item = item
        box = Gtk.Box(spacing=14, margin_start=12, margin_end=8, margin_top=8, margin_bottom=8)
        if leading is not None:
            box.append(leading)
        if art:
            self.art = ArtImage(backend, 48)
            self.art.set_item(item)
            box.append(self.art)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True, spacing=2)
        text.append(label(getattr(item, "name", "") or "", "title-m"))
        if subtitle:
            text.append(label(subtitle, "body-m", "on-variant"))
        box.append(text)
        if trailing_text:
            box.append(label(trailing_text, "label-l", "on-variant", "numeric", ellipsize=False))
        if play_button:
            box.append(sym_button("play_arrow", "Play", fill=True, action="media.play"))
        if menu:
            box.append(media_menu_button())
        self.set_child(box)
        attach_media_actions(self, backend, lambda: item)


def m3_list() -> Gtk.ListBox:
    lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    lb.add_css_class("m3-list")
    lb.connect("row-activated", lambda _l, row: getattr(row, "on_activate", lambda: None)())
    return lb


def media_row(backend, item, subtitle: str = "", on_activate=None, **kw) -> MediaRow:
    row = MediaRow(backend, item, subtitle, **kw)
    if on_activate:
        row.on_activate = lambda: on_activate(item)
    return row


def status_page(icon: str, title: str, description: str = "") -> Adw.StatusPage:
    sp = Adw.StatusPage(icon_name=icon, title=title, description=description)
    return sp


def retry_page(title: str, on_retry) -> Adw.StatusPage:
    sp = status_page("dialog-error-symbolic", title, "Check the connection to the server and try again.")
    btn = Gtk.Button(label="Retry", halign=Gtk.Align.CENTER)
    btn.add_css_class("pill")
    btn.add_css_class("suggested-action")
    btn.connect("clicked", lambda *_: on_retry())
    sp.set_child(btn)
    return sp
