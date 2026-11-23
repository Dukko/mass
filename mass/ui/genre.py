"""Genre page: compact hero, a collapsible albums row, then a virtualized track list."""

from __future__ import annotations

import logging

from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk
from music_assistant_models.enums import QueueOption, SortDirection, SortField

from .common import ArtImage, label, retain_while_rooted, retry_page, status_page, weak_method
from .expressive import Sym
from .home import Carousel
from .library import library_sort
from .media_list import MediaList
from .motion import spring_params

log = logging.getLogger(__name__)

ALBUMS_SHOWN = 50
ALBUM_TILE = 120


class FoldBin(Gtk.Widget):
    """Clips one child and slides it up by `offset` px, giving the freed height to its siblings."""

    __gtype_name__ = "MassFoldBin"

    def __init__(self, child: Gtk.Widget) -> None:
        super().__init__(overflow=Gtk.Overflow.HIDDEN)
        self.child, self._offset, self.folded, self._width = child, 0.0, False, -1
        child.set_parent(self)

    def full_height(self) -> int:
        return self.child.measure(Gtk.Orientation.VERTICAL, self._width)[1]

    @property
    def offset(self) -> float:
        """Folded px; a fully folded header stays fully folded when its content grows."""
        full = float(self.full_height())
        return full if self.folded else min(self._offset, full)

    def set_offset(self, value: float) -> None:
        full = float(self.full_height())
        self._offset = max(0.0, min(value, full))
        self.folded = self._offset >= full
        self.child.set_can_focus(not self.folded)
        self.child.set_can_target(not self.folded)
        self.queue_resize()

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            mn, nat, _, _ = self.child.measure(orientation, -1)
            return mn, nat, -1, -1
        if for_size >= 0:
            self._width = for_size
        height = max(0, self.full_height() - round(self.offset))
        return height, height, -1, -1

    def do_size_allocate(self, width, height, baseline):
        self._width = width
        shift = Graphene.Point().init(0, -round(self.offset))
        self.child.allocate(width, self.full_height(), -1, Gsk.Transform().translate(shift))

    def do_dispose(self) -> None:
        if self.child is not None:
            self.child.unparent()
            self.child = None


class GenrePage(Adw.NavigationPage):
    """Everything in the library mapped to one genre; sections appear only when they have content.

    The track list is the page's only scroller (a list inside another scroller would be laid out in
    full), so the hero and albums row fold away as the list scrolls: wheel and touchpad scrolling
    folds the header before it moves the list, and a scrollbar drag or key press springs it shut.
    """

    def __init__(self, backend, genre, open_item) -> None:
        super().__init__(title=genre.name)
        retain_while_rooted(self)
        self.backend, self.item, self.open_item = backend, genre, open_item
        self._albums_done = False
        self._albums_empty = True
        self._tracks_empty = False

        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar(show_title=False)
        hb.add_css_class("flat-header")
        tv.add_top_bar(hb)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_bottom=12)
        self._fold = FoldBin(self._header)
        self._fold_anim: Adw.SpringAnimation | None = None
        self._fold_target: float | None = None
        self._over_albums = False
        self._adj_value, self._page_size, self._resizing = 0.0, 0.0, False
        body.append(self._fold)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll.connect("scroll", weak_method(self, "_on_wheel"))
        body.add_controller(scroll)
        self._header.append(Adw.Clamp(maximum_size=980, margin_start=20, margin_end=20, child=self._hero(genre)))

        self.albums = Carousel(backend, "", open_item, ALBUM_TILE)
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", weak_method(self, "_set_over_albums"), True)
        hover.connect("leave", weak_method(self, "_set_over_albums"), False)
        self.albums.add_controller(hover)
        self.albums_toggle = Gtk.Button(halign=Gtk.Align.START, margin_start=20, visible=False)
        self.albums_toggle.add_css_class("flat")
        self.albums_toggle.connect("clicked", weak_method(self, "_toggle_albums"))
        self.revealer = Gtk.Revealer(reveal_child=True, child=self.albums)
        self.albums_toggle.set_label("Albums  ▾")
        self._header.append(Adw.Clamp(maximum_size=980, child=self.albums_toggle))
        self._header.append(Adw.Clamp(maximum_size=980, margin_start=20, margin_end=20, child=self.revealer))

        self.tracks = MediaList(backend, weak_method(self, "_fetch_tracks"),
                                lambda t: backend.play_media(t, option=QueueOption.PLAY),
                                on_empty=weak_method(self, "_on_tracks"))
        self.tracks_stack = Gtk.Stack(vexpand=True)
        self.tracks_stack.set_size_request(-1, 240)
        self.tracks_stack.add_named(self.tracks, "list")
        self.tracks_stack.add_named(retry_page("Couldn’t load tracks", weak_method(self, "_retry_tracks")), "error")
        body.append(self.tracks_stack)
        self.empty = status_page("view-grid-symbolic", "Nothing in this genre yet")
        self.empty.set_visible(False)
        self.empty.set_vexpand(True)
        body.append(self.empty)
        tv.set_content(body)
        self.set_child(tv)
        adj = self.tracks.get_vadjustment()
        adj.connect("value-changed", weak_method(self, "_on_list_scroll"))
        adj.connect("changed", weak_method(self, "_viewport_moved"))
        backend.run(self._load_albums())

    def _stop_fold_anim(self) -> None:
        if self._fold_anim is not None:
            self._fold_anim.pause()
            self._fold_anim = self._fold_target = None

    def _on_wheel(self, ctrl: Gtk.EventControllerScroll, _dx: float, dy: float) -> bool:
        """Scrolling folds the header first, then scrolls the list; scrolling back up at the top unfolds it."""
        if ctrl.get_unit() == Gdk.ScrollUnit.WHEEL:
            dy *= 56
        if self._over_albums:  # the carousel turns vertical wheel into sideways scrolling
            return False
        fold = self._fold
        at_top = self.tracks.get_vadjustment().get_value() <= 0
        if (dy > 0 and not fold.folded) or (dy < 0 and fold.offset > 0 and at_top):
            self._stop_fold_anim()
            fold.set_offset(fold.offset + dy)
            return True
        return False

    def _on_list_scroll(self, adj: Gtk.Adjustment) -> None:
        """Scrollbar drags, keys and touch move the list without the wheel; the header follows with a spring."""
        fold = self._fold
        value, prev = adj.get_value(), self._adj_value
        self._adj_value = value
        # Folding or unfolding resizes the viewport, which clamps or re-anchors the value; only a move
        # the user made on a stable viewport says which way the header should go.
        if self._fold_anim is not None or self._viewport_moved(adj):
            return
        if value > prev and value > 0 and not fold.folded:
            self._spring_fold(float(fold.full_height()))
        elif value < prev and value <= 0 and fold.offset > 0:
            self._spring_fold(0.0)

    def _viewport_moved(self, adj: Gtk.Adjustment) -> bool:
        """Whether the viewport height changed since last looked at, or in the current layout pass."""
        if adj.get_page_size() != self._page_size:
            self._page_size = adj.get_page_size()
            self._resizing = True
            GLib.idle_add(self._resize_done)
        return self._resizing

    def _resize_done(self) -> bool:
        self._resizing = False
        return GLib.SOURCE_REMOVE

    def _spring_fold(self, target: float) -> None:
        if self._fold_target == target:
            return
        self._stop_fold_anim()
        self._fold_target = target
        fold = self._fold
        anim = self._fold_anim = Adw.SpringAnimation.new(
            fold, fold.offset, target, spring_params(),
            Adw.CallbackAnimationTarget.new(fold.set_offset))
        anim.set_clamp(True)
        anim.connect("done", weak_method(self, "_fold_settled"))
        anim.play()

    def _set_over_albums(self, *args) -> None:
        self._over_albums = args[-1]

    def _fold_settled(self, *_args) -> None:
        opened = self._fold_target == 0.0
        self._fold_anim = self._fold_target = None
        adj = self.tracks.get_vadjustment()
        if opened and adj.get_value() > 0:  # the list re-anchored under the smaller viewport
            adj.set_value(0)
        self._adj_value = adj.get_value()

    def _hero(self, genre) -> Gtk.Widget:
        head = Gtk.Box(spacing=20)
        art = ArtImage(self.backend, 120, fetch_size=240)
        art.set_item(genre)
        head.append(art)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER, hexpand=True)
        info.append(label("GENRE", "overline", "primary-text"))
        info.append(label(genre.name, "display-s"))
        play = Gtk.Button(margin_top=8, halign=Gtk.Align.START)
        for c in ("m3", "filled", "large"):
            play.add_css_class(c)
        pb = Gtk.Box(spacing=8)
        pb.append(Sym("play_arrow", 24, fill=True))
        pb.append(Gtk.Label(label="Play"))
        play.set_child(pb)
        play.connect("clicked", weak_method(self, "_play"))
        info.append(play)
        head.append(info)
        return head

    def _toast(self, text: str) -> None:
        root = self.get_root()
        if hasattr(root, "toast"):
            root.toast(text)

    def _toggle_albums(self, *_args) -> None:
        show = not self.revealer.get_reveal_child()
        self.revealer.set_reveal_child(show)
        self.albums_toggle.set_label("Albums  ▾" if show else "Albums  ▸")

    def _play(self, *_args) -> None:
        b = self.backend
        qid = b.active_queue.queue_id if b.active_queue else (b.active_player.player_id if b.active_player else None)
        if not qid or not b.client:
            self._toast("No player selected")
            return

        async def go() -> None:
            try:
                await b.client.player_queues.play_media(qid, self.item)
            except Exception as err:  # noqa: BLE001 - the server may refuse to play a genre
                log.warning("playing genre %s failed: %s", self.item.name, err)
                self._toast(f"Couldn’t play {self.item.name}")

        b.run(go())

    async def _load_albums(self) -> None:
        music = self.backend.client.music
        sort = library_sort(self.backend, SortField.SORT_NAME, SortDirection.ASC, "sort_name")
        try:
            albums = await music.get_genre_albums(self.item.item_id, limit=ALBUMS_SHOWN, **sort)
        except Exception as err:  # noqa: BLE001 - the tracks may still load
            log.warning("genre albums failed: %s", err)
            albums = []
        self.albums.set_items(albums)
        self.albums_toggle.set_visible(bool(albums))
        self._albums_done, self._albums_empty = True, not albums
        self._update_empty()

    async def _fetch_tracks(self, offset: int, limit: int) -> list:
        try:
            return await self.backend.client.music.get_genre_tracks(self.item.item_id, limit=limit, offset=offset)
        except Exception:
            if offset == 0:
                self.tracks_stack.set_visible_child_name("error")
            raise

    def _retry_tracks(self) -> None:
        self.tracks.reset()
        self.tracks_stack.set_visible_child_name("list")

    def _on_tracks(self, empty: bool) -> None:
        self._tracks_empty = empty
        self.tracks_stack.set_visible(not empty)
        self._update_empty()

    def _update_empty(self) -> None:
        self.empty.set_visible(self._albums_done and self._albums_empty and self._tracks_empty)
