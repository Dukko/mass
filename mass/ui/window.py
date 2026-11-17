"""Main window: navigation rail + raised content surface + floating mini player."""

from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from .. import APP_NAME
from ..backend import Status
from .dialogs import LoginDialog
from .expressive import Sym
from .dj import AIRadioView
from .home import HomeView
from .library import DetailPage, LibraryView
from .nowplaying import MiniPlayer, NowPlayingPage, is_playing, schedule_refresh
from .players import PlayersDialog
from .queue import QueuePage
from .search import SearchView

MENU_XML = """
<interface><menu id="m">
  <section>
    <item><attribute name="label">Preferences</attribute><attribute name="action">app.preferences</attribute></item>
    <item><attribute name="label">Keyboard Shortcuts</attribute><attribute name="action">app.shortcuts</attribute></item>
    <item><attribute name="label">About Mass</attribute><attribute name="action">app.about</attribute></item>
  </section>
  <section>
    <item><attribute name="label">Quit</attribute><attribute name="action">app.quit</attribute></item>
  </section>
</menu></interface>"""

NAV = (
    ("home", "Home", "home"),
    ("library", "Library", "library_music"),
    ("search", "Search", "search"),
    ("queue", "Queue", "queue_music"),
    ("airadio", "AI Radio", "auto_awesome"),
)


class RailItem(Gtk.Button):
    def __init__(self, title: str, symbol: str, cb) -> None:
        super().__init__(tooltip_text=title)
        self.add_css_class("rail-item")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER)
        ind = Gtk.Box(halign=Gtk.Align.CENTER)
        ind.add_css_class("indicator")
        self.sym = Sym(symbol, 24)
        self.sym.set_hexpand(True)  # centered in the pill...
        ind.set_hexpand(False)  # ...without propagating expansion to the rail
        ind.append(self.sym)
        box.append(ind)
        self.set_child(box)
        self.connect("clicked", lambda *_: cb())

    def set_active(self, on: bool) -> None:
        (self.add_css_class if on else self.remove_css_class)("active")
        self.sym.set_fill(on)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app) -> None:
        super().__init__(application=app, title=APP_NAME, default_width=1240, default_height=820)
        self.app = app
        self.backend = app.backend
        self.add_css_class("mass")
        self.set_size_request(380, 520)
        self._login_open = False
        self._page = "home"

        # ------------------------------------------------ pages
        self.home = HomeView(self.backend, self.open_item, lambda: self.show_page("nowplaying"))
        self.library = LibraryView(self.backend, self.open_item)
        self.search = SearchView(self.backend, self.open_item)
        self.queue = QueuePage(self.backend)
        self.airadio = AIRadioView(self.backend)
        self.nowplaying = NowPlayingPage(self.backend, app.theme, lambda *_: self.show_players(),
                                         lambda: self.show_page("queue"))
        self.status_page = Adw.StatusPage(icon_name="network-server-symbolic", title="Not connected")
        sbtn = Gtk.Button(label="Connect to a server", halign=Gtk.Align.CENTER)
        for c in ("m3", "filled", "large"):
            sbtn.add_css_class(c)
        sbtn.connect("clicked", lambda *_: self.show_login())
        self.status_page.set_child(sbtn)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=180,
                               vexpand=True)
        for name, w in (("home", self.home), ("library", self.library), ("search", self.search),
                        ("queue", self.queue), ("airadio", self.airadio), ("nowplaying", self.nowplaying), ("status", self.status_page)):
            self.stack.add_named(w, name)

        header = Adw.HeaderBar(show_title=False)
        header.add_css_class("flat-header")
        self.banner = Adw.Banner()
        root = Adw.ToolbarView(content=self.stack, top_bar_style=Adw.ToolbarStyle.FLAT)
        root.add_top_bar(header)
        root.add_top_bar(self.banner)
        root.set_extend_content_to_top_edge(True)  # pages draw under the transparent header
        self.nav = Adw.NavigationView()
        self.nav.add(Adw.NavigationPage(child=root, title=APP_NAME, tag="root"))
        self.nav.connect("notify::visible-page", lambda *_: self._sync_mini())

        self.surface = Gtk.Box(hexpand=True)
        self.surface.add_css_class("content-surface")
        self.surface.set_overflow(Gtk.Overflow.HIDDEN)
        self.surface.append(self.nav)
        self.nav.set_hexpand(True)

        # ------------------------------------------------ rail
        rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, width_request=64)
        rail.add_css_class("rail")
        menu = Gtk.MenuButton(menu_model=Gtk.Builder.new_from_string(MENU_XML, -1).get_object("m"),
                              halign=Gtk.Align.CENTER, primary=True, tooltip_text="Menu")
        menu.set_child(Sym("menu", 24))
        menu.add_css_class("flat")
        menu.add_css_class("circular")
        rail.append(menu)
        self.fab = Gtk.Button(halign=Gtk.Align.CENTER, tooltip_text="Now playing", margin_top=8,
                              margin_bottom=14)
        self.fab.add_css_class("rail-fab")
        self.fab.sym = Sym("graphic_eq", 24, fill=True)
        self.fab.set_child(self.fab.sym)
        self.fab.connect("clicked", lambda *_: self.show_page("nowplaying"))
        rail.append(self.fab)
        self.rail_items: dict[str, RailItem] = {}
        for name, title, symbol in NAV:
            item = RailItem(title, symbol, lambda n=name: self.show_page(n))
            self.rail_items[name] = item
            rail.append(item)
        players = RailItem("Players", "speaker", self.show_players)
        rail.append(players)
        rail.append(Gtk.Box(vexpand=True))
        rail.append(RailItem("Settings", "settings", lambda: app.activate_action("preferences", None)))
        rail.set_hexpand(False)  # never take a share of spare width
        self.rail = rail

        body = Gtk.Box()
        body.append(rail)
        body.append(self.surface)

        # ------------------------------------------------ floating mini player
        self.mini = MiniPlayer(self.backend, lambda: self.show_page("nowplaying"))
        overlay = Gtk.Overlay(child=body)
        overlay.add_overlay(self.mini)
        self.revealer_mini = self.mini
        self.toasts = Adw.ToastOverlay(child=overlay)
        self.set_content(self.toasts)

        # ------------------------------------------------ responsive
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 760sp"))
        bp.connect("apply", lambda *_: self._set_narrow(True))
        bp.connect("unapply", lambda *_: self._set_narrow(False))
        self.add_breakpoint(bp)

        # ------------------------------------------------ signals
        self.backend.connect("status-changed", self._on_status)
        for sig in ("player-updated", "queue-updated", "active-player-changed"):
            self.backend.connect(sig, lambda *_: schedule_refresh(self))
        self.backend.connect("ai-changed", lambda *_: self.rail_items["airadio"].set_visible(
            self.backend.ai_available))
        self.rail_items["airadio"].set_visible(False)
        self._on_status(self.backend, self.backend.status, self.backend.status_detail)
        self.show_page("home")

    # ------------------------------------------------------------------ navigation

    def refresh(self) -> None:
        playing = is_playing(self.backend)
        self.fab.sym.set_symbol("graphic_eq" if playing else "music_note")
        (self.fab.add_css_class if playing else self.fab.remove_css_class)("playing")
        self._sync_mini()

    def show_page(self, name: str) -> None:
        if self.backend.status != Status.CONNECTED and name != "status":
            name = "status" if self.backend.client is None and self.backend.status == Status.AUTH_REQUIRED \
                else name
        self._page = name
        self.nav.pop_to_tag("root")
        self.stack.set_visible_child_name(name)
        for n, item in self.rail_items.items():
            item.set_active(n == name)
        (self.fab.add_css_class if name == "nowplaying" else self.fab.remove_css_class)("suggested")
        self._sync_mini()

    def _sync_mini(self) -> None:
        on_np = self.stack.get_visible_child_name() == "nowplaying" and \
            self.nav.get_visible_page() is not None and self.nav.get_visible_page().get_tag() == "root"
        has = self.backend.connected and self.backend.active_player is not None
        self.mini.set_visible(has and not on_np)

    def _set_narrow(self, narrow: bool) -> None:
        self.rail.set_size_request(56 if narrow else 64, -1)
        (self.surface.add_css_class if narrow else self.surface.remove_css_class)("narrow")
        self.nowplaying.set_narrow(narrow)
        self.library.set_narrow(narrow)
        self.mini.set_narrow(narrow)

    def show_players(self) -> None:
        PlayersDialog(self.backend, self.app.endpoint).present(self)

    def _on_status(self, _b, status: str, detail: str) -> None:
        if status == Status.CONNECTED:
            self.banner.set_revealed(False)
            if self._page == "status" or self.stack.get_visible_child_name() == "status":
                self.show_page("home")
            self._sync_mini()
            return
        if status == Status.AUTH_REQUIRED:
            self.status_page.set_title("Not connected")
            self.status_page.set_description(GLib.markup_escape_text(detail))
            self.stack.set_visible_child_name("status")
            self.banner.set_revealed(False)
            self._sync_mini()
            if self.get_visible():
                GLib.idle_add(lambda: (self.show_login(), False)[1])
            return
        self.banner.set_title(GLib.markup_escape_text(
            "Connecting to Music Assistant…" if status == Status.CONNECTING else f"Disconnected: {detail}"))
        self.banner.set_revealed(True)

    def show_login(self) -> None:
        if self._login_open:
            return
        self._login_open = True
        dlg = LoginDialog(self.backend)
        dlg.connect("closed", lambda *_: setattr(self, "_login_open", False))
        dlg.present(self)

    def open_item(self, item) -> None:
        self.nav.push(DetailPage(self.backend, item, self.open_item))

    def focus_search(self) -> None:
        self.show_page("search")
        self.search.focus()

    def toggle_now_playing(self) -> None:
        self.show_page("home" if self._page == "nowplaying" else "nowplaying")

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))
