"""Shared widgets and helpers."""

from __future__ import annotations

import asyncio
from collections import OrderedDict

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk
from music_assistant_models.enums import MediaType, QueueOption

from .expressive import ShapeArt, Sym, sym_button

PLACEHOLDER_ICONS = {
    MediaType.ARTIST: "avatar-default-symbolic",
    MediaType.PLAYLIST: "view-list-symbolic",
    MediaType.RADIO: "network-wireless-symbolic",
    MediaType.PODCAST: "microphone-sensitivity-high-symbolic",
    MediaType.AUDIOBOOK: "accessories-dictionary-symbolic",
}

# M3E: vary shapes by content type
SHAPE_FOR = {
    MediaType.ARTIST: "circle",
    MediaType.PLAYLIST: "cookie9",
    MediaType.RADIO: "sunny",
    MediaType.PODCAST: "clover",
    MediaType.AUDIOBOOK: "cookie6",
}


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
            if url != self._url or not data:
                return
            try:  # decode off the main thread: a big JPEG takes long enough to drop frames
                tex = await asyncio.to_thread(Gdk.Texture.new_from_bytes, GLib.Bytes.new(data))
            except GLib.Error:
                return
            _TEXTURES[url] = (tex, data)
            while len(_TEXTURES) > _TEXTURE_CACHE_MAX:
                _TEXTURES.popitem(last=False)
            if url != self._url:
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


def attach_media_actions(widget: Gtk.Widget, backend, get_item) -> None:
    """Install `media.*` actions on a widget, operating on get_item()."""
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
    widget.insert_action_group("media", group)


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
    menu.append_section(None, sec2)
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
