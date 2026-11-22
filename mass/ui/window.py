"""Main window: navigation rail + raised content surface + floating mini player."""

from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from .. import APP_NAME
from ..backend import Status
from .dialogs import LoginDialog
from .expressive import Sym
from .dj import AIRadioView
from .home import HomeView
from .library import LibraryView
from .nowplaying import TABS as NP_TABS
from .nowplaying import MiniPlayer, NowPlayingPage, is_playing, schedule_refresh
from .motion import MotionBin, spring_stack
from .pages import page_for
from .splash import Splash
from .players import PlayersView
from .queue import QueuePage
from .search import SearchView

SPLASH_TIMEOUT_S = 12  # never keep the splash up longer than this (slow or unreachable server)
WARM_GAP_FRAMES = 3  # smooth frames to leave between two warm-up layouts, so the splash keeps moving
WARM_MAX_WAIT_FRAMES = 12  # ...but never wait longer than this for the frame rate to recover
WARM_SLOW_FRAME_US = 25_000  # a frame longer than this means the last layout hasn't settled yet

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
        self.players = PlayersView(self.backend, app.endpoint)
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
                        ("queue", self.queue), ("airadio", self.airadio), ("players", self.players),
                        ("nowplaying", self.nowplaying), ("status", self.status_page)):
            self.stack.add_named(w, name)

        header = Adw.HeaderBar(show_title=False)
        header.add_css_class("flat-header")
        self.banner = Adw.Banner()
        self.page_motion = spring_stack(self.stack)
        self._warmed = False
        self._warming = False
        root = Adw.ToolbarView(content=self.page_motion, top_bar_style=Adw.ToolbarStyle.FLAT)
        root.add_top_bar(header)
        root.add_top_bar(self.banner)
        self.update_banner = Adw.Banner(button_label="Restart")
        self.update_banner.connect("button-clicked", self._on_update_banner)
        root.add_top_bar(self.update_banner)
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
        self.rail_items["players"] = players
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
        # the pill springs up from the bottom edge whenever it (re)appears
        self.mini_motion = MotionBin(self.mini, dy=56, scale=0.9, preset="bouncy",
                                     halign=Gtk.Align.CENTER, valign=Gtk.Align.END)
        self.mini.connect("notify::visible", lambda m, _p: m.get_visible() and self.mini_motion.play())
        overlay = Gtk.Overlay(child=body)
        overlay.add_overlay(self.mini_motion)
        # startup splash over everything while we connect and warm the pages up behind it
        self.splash = Splash()
        overlay.add_overlay(self.splash)
        if not self.backend.config.server_url:
            self.splash.set_visible(False)  # first run: go straight to sign-in
        else:
            self.splash.hold_pages()  # the pages behind it don't need to animate until it's gone
            GLib.timeout_add_seconds(SPLASH_TIMEOUT_S, self._splash_timeout)
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
        self.show_page("players")

    def _warm_up(self) -> None:
        """Behind the splash, show every page (and every Now Playing tab) for a couple of frames.

        GTK styles and lays a page out the first time it's shown, and for the bigger pages that
        takes long enough to stutter a transition. Doing it all here, hidden by the splash, makes
        every first visit as cheap as a later one.
        """
        if self._warmed or not self.backend.connected or not self.splash.get_visible():
            return self._end_splash()
        if not self.get_mapped():  # started in the background: warm up when the window first shows
            hid = self.connect("map", lambda *_: (self.disconnect(hid), self._warm_up()))
            return
        self._warmed = True
        self._warming = True
        self.splash.set_status("Getting things ready…")
        pages = [n for n in ("library", "search", "queue", "airadio", "players", "nowplaying")
                 if n != "airadio" or self.backend.ai_available]
        np = self.nowplaying
        tabs = [n for n, _t in NP_TABS if n != "dj" or self.backend.ai_available]
        steps = [("page", n) for n in pages] + [("tab", t) for t in tabs]
        orig_page, orig_tab = self.stack.get_visible_child_name(), np.stack.get_visible_child_name()
        self.page_motion.silent = np.tab_motion.silent = True

        def show(step):
            kind, name = step
            if kind == "page":
                self.stack.set_visible_child_name(name)
            else:
                self.stack.set_visible_child_name("nowplaying")
                np.stack.set_visible_child_name(name)

        it = iter(steps)
        state = {"since": 0, "last": 0}

        def tick(_w, clock):
            # A page's first layout makes its frame long, and the splash misses that frame. Leave a
            # few smooth frames between layouts (and wait for the frame rate to recover) so the
            # splash keeps moving instead of hitching on every other frame.
            now = clock.get_frame_time()
            slow = state["last"] and now - state["last"] > WARM_SLOW_FRAME_US
            state["last"] = now
            state["since"] += 1
            if state["since"] < WARM_GAP_FRAMES or (slow and state["since"] < WARM_MAX_WAIT_FRAMES):
                return True
            state["since"] = 0
            step = next(it, None)
            if step is not None:
                show(step)
                return True
            np.stack.set_visible_child_name(orig_tab)
            self.stack.set_visible_child_name(self._page if self._page != "status" else orig_page)
            self.page_motion.silent = np.tab_motion.silent = False
            self._warming = False
            GLib.timeout_add(120, lambda: (self._end_splash(), False)[1])  # let the last layout land
            return False

        show(next(it))
        self.add_tick_callback(tick)

    def _splash_timeout(self) -> bool:
        # a warm-up in progress dismisses the splash itself once the pages are back in place;
        # fading out now would show them flipping by
        if not self._warming:
            self._end_splash()
        return False

    def _end_splash(self) -> None:
        self.splash.dismiss()

    def _on_status(self, _b, status: str, detail: str) -> None:
        if status == Status.CONNECTED:
            self.banner.set_revealed(False)
            if self._page == "status" or self.stack.get_visible_child_name() == "status":
                self.show_page("home")
            self._sync_mini()
            # give the first data a moment to arrive, so the pages warm up with real content
            GLib.timeout_add(700, lambda: (self._warm_up(), False)[1])
            return
        if status == Status.AUTH_REQUIRED:
            self._end_splash()
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

    def show_update(self, up) -> None:
        if up.state == "ready":
            self.update_banner.set_title("A new version of Mass is installed")
            self.update_banner.set_button_label("Restart")
        elif up.state == "available" and not self.app.config.auto_update:
            self.update_banner.set_title("A Mass update is available")
            self.update_banner.set_button_label("Update")
        elif up.state == "downloading" and self.update_banner.get_revealed():
            self.update_banner.set_title("Downloading update…")
            self.update_banner.set_button_label(None)
            return
        else:
            self.update_banner.set_revealed(False)
            return
        self.update_banner.set_revealed(True)

    def _on_update_banner(self, _b) -> None:
        if self.app.updater.state == "ready":
            self.app.restart_for_update()
        else:
            self.app.updater.install()

    def show_login(self) -> None:
        if self._login_open:
            return
        self._login_open = True
        dlg = LoginDialog(self.backend)
        dlg.connect("closed", lambda *_: setattr(self, "_login_open", False))
        dlg.present(self)

    def open_item(self, item) -> None:
        self.nav.push(page_for(self.backend, item, self.open_item))

    def focus_search(self) -> None:
        self.show_page("search")
        self.search.focus()

    def toggle_now_playing(self) -> None:
        self.show_page("home" if self._page == "nowplaying" else "nowplaying")

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))
