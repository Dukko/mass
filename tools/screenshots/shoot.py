"""Drive Mass through a series of views and save a PNG of each.

Run from the repo root against a connected server, with something in the queue:
    .venv/bin/python tools/screenshots/shoot.py /tmp/raw 1400 900 [name,name...]
    python tools/screenshots/frame.py /tmp/raw docs/screenshots
On niri the window is floated and resized to WxH automatically.
"""
import json, os, subprocess, sys, logging

import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.events import GLibEventLoopPolicy
import asyncio
asyncio.set_event_loop_policy(GLibEventLoopPolicy())
from gi.repository import GLib, Gtk, Graphene

os.environ["MASS_SCREENSHOT"] = "1"   # separate instance, no services
sys.path.insert(0, os.getcwd())
from mass import APP_ID
from mass.app import MassApp

OUT = sys.argv[1]
W, H = int(sys.argv[2]), int(sys.argv[3])
ONLY = sys.argv[4].split(",") if len(sys.argv) > 4 else None
logging.basicConfig(level="INFO")
log = logging.getLogger("shoot")


def niri_float():
    try:
        wins = json.loads(subprocess.check_output(["niri", "msg", "--json", "windows"]))
    except Exception:
        return True
    for w in wins:
        if w.get("pid") == os.getpid():
            i = str(w["id"])
            if not w.get("is_floating"):
                subprocess.run(["niri", "msg", "action", "move-window-to-floating", "--id", i])
            subprocess.run(["niri", "msg", "action", "set-window-width", "--id", i, str(W)])
            subprocess.run(["niri", "msg", "action", "set-window-height", "--id", i, str(H)])
            return False
    return True


def snap(win, name):
    p = Gtk.WidgetPaintable.new(win)
    s = Gtk.Snapshot()
    p.snapshot(s, win.get_width(), win.get_height())
    node = s.to_node()
    tex = win.get_native().get_renderer().render_texture(
        node, Graphene.Rect().init(0, 0, win.get_width(), win.get_height()))
    tex.save_to_png(os.path.join(OUT, name + ".png"))
    log.info("saved %s (%dx%d)", name, win.get_width(), win.get_height())


class Shooter(MassApp):
    def do_activate(self):
        self.window.set_default_size(W, H)
        self.window.present()
        GLib.timeout_add(300, niri_float)
        GLib.timeout_add_seconds(14, lambda: (self.backend.run(self.script()), False)[1])

    async def script(self):
        w, b = self.window, self.backend
        m = b.client.music

        async def shot(name, settle=2.5):
            if ONLY and name not in ONLY:
                return
            await asyncio.sleep(settle)
            snap(w, name)

        async def page(name, settle=2.5):
            w.show_page(name)
            await shot(name, settle)

        try:
            await page("home", 1)
            await page("library")
            await page("queue")
            if b.ai_available:
                await page("airadio")
            w.show_page("nowplaying")
            orig = b.config.np_tab
            for tab in ("lyrics", "queue", "about", "dj"):
                if tab == "dj" and not b.ai_available:
                    continue
                w.nowplaying.tabs[tab].set_active(True)
                w.nowplaying.refresh()
                await shot("np-" + tab, 4)
            b.config.np_tab = orig
            b.config.save()
            artists = await m.get_library_artists(search=os.environ.get("ARTIST", "Mitski"), limit=1)
            if artists:
                w.show_page("home")
                w.open_item(await m.get_artist(artists[0].item_id, artists[0].provider))
                await shot("artist", 4)
            cur = b.active_queue.current_item.media_item
            albums = [await m.get_album(cur.album.item_id, cur.album.provider)] if cur and cur.album else []
            if albums:
                w.nav.pop_to_tag("root")
                w.open_item(albums[0])
                await shot("album", 4)
            w.nav.pop_to_tag("root")
            w.show_page("home")
            w.show_players()
            await shot("players", 2.5)
        except Exception:
            log.exception("script failed")
        self.quit()


sys.exit(Shooter().run([sys.argv[0]]))
