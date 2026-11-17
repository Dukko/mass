"""Player picker sheet: select, power, per-player volume, move playback."""

from __future__ import annotations

from gi.repository import Adw, GLib, Gtk
from music_assistant_models.enums import PlaybackState, PlayerType

from .common import label
from .expressive import Sym, sym_button


def player_symbol(p) -> str:
    if p.type in (PlayerType.GROUP, PlayerType.STEREO_PAIR):
        return "speaker_group"
    model = (p.device_info.model or "").lower() if p.device_info else ""
    if any(k in model for k in ("tv", "chromecast", "shield")):
        return "tv"
    if any(k in model for k in ("phone", "android", "iphone")):
        return "smartphone"
    return "speaker"


class PlayersDialog(Adw.Dialog):
    def __init__(self, backend, endpoint) -> None:
        super().__init__(title="Players", content_width=520, content_height=620)
        self.backend, self.endpoint = backend, endpoint
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar(show_title=False)
        hb.add_css_class("flat-header")
        tv.add_top_bar(hb)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=20, margin_end=20,
                      margin_bottom=20)
        col.append(label("Players", "display-s", ellipsize=False))
        col.append(label("Choose what Mass controls, or move playback to another speaker.", "body-m",
                         "on-variant", ellipsize=False, wrap=True))
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_top=8)
        self.list.add_css_class("m3-list")
        self.list.add_css_class("players-list")
        self.list.connect("row-activated", self._on_activate)
        col.append(self.list)
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        sw.set_child(col)
        tv.set_content(sw)
        self.set_child(tv)
        self._h = [backend.connect("players-changed", lambda *_: self.rebuild()),
                   backend.connect("player-updated", lambda *_: self._update())]
        self.connect("closed", lambda *_: [backend.disconnect(h) for h in self._h])
        self._rows: dict[str, Gtk.ListBoxRow] = {}
        self.rebuild()

    def rebuild(self) -> None:
        while row := self.list.get_row_at_index(0):
            self.list.remove(row)
        self._rows.clear()
        local_id = self.endpoint.find_player_id(self.backend)
        active = self.backend.active_player
        for p in self.backend.players():
            row = Gtk.ListBoxRow(activatable=p.available)
            row.player_id = p.player_id
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=14, margin_end=10,
                            margin_top=10, margin_bottom=10, spacing=6)
            top = Gtk.Box(spacing=14)
            top.append(Sym(player_symbol(p), 28, fill=bool(active and active.player_id == p.player_id)))
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
            name = Gtk.Box(spacing=8)
            name.append(label(p.name, "title-m"))
            if p.player_id == local_id:
                badge = label("THIS PC", "badge", ellipsize=False, valign=Gtk.Align.CENTER)
                name.append(badge)
            text.append(name)
            row.sub = label("", "body-m", "on-variant")
            text.append(row.sub)
            top.append(text)
            if active and active.player_id != p.player_id and p.available:
                top.append(sym_button("cast", f"Move playback to {p.name}", ("m3", "icon", "tonal"),
                                      cb=lambda pid=p.player_id: (self.backend.transfer_to(pid), self.close())))
            if p.powered is not None:
                sw = Gtk.Switch(active=bool(p.powered), valign=Gtk.Align.CENTER, tooltip_text="Power")
                sw.connect("notify::active",
                           lambda s, _pp, pid=p.player_id: self.backend.set_power(pid, s.get_active()))
                top.append(sw)
            outer.append(top)
            if p.volume_level is not None and p.available:
                vol = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
                vol.set_draw_value(False)
                vol.add_css_class("m3-slider")
                vol.set_value(p.volume_level)
                vol.set_margin_start(42)
                row.vol = vol
                row.vol_timer = 0

                def changed(scale, pid=p.player_id, r=row):
                    if r.vol_timer:
                        GLib.source_remove(r.vol_timer)

                    def send():
                        r.vol_timer = 0
                        self.backend.set_volume(int(scale.get_value()), pid)
                        return False

                    r.vol_timer = GLib.timeout_add(120, send)

                vol.connect("value-changed", changed)
                outer.append(vol)
            row.set_child(outer)
            if active and active.player_id == p.player_id:
                row.add_css_class("current")
            self._rows[p.player_id] = row
            self.list.append(row)
        self._update()

    def _update(self) -> None:
        for pid, row in self._rows.items():
            p = self.backend.get_player(pid)
            if not p:
                continue
            q = self.backend.queue_for(p)
            state = q.state if q else p.playback_state
            bits = [{PlaybackState.PLAYING: "Playing", PlaybackState.PAUSED: "Paused"}.get(
                state, "Unavailable" if not p.available else "Off" if p.powered is False else "Idle")]
            if q and q.current_item:
                bits.append(q.current_item.name)
            elif p.current_media and p.current_media.title:
                bits.append(p.current_media.title)
            if p.synced_to and (leader := self.backend.get_player(p.synced_to)):
                bits.append(f"synced to {leader.name}")
            row.sub.set_label(" · ".join(bits))

    def _on_activate(self, _l, row) -> None:
        self.backend.set_active_player(row.player_id)
        self.close()
