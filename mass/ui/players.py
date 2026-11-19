"""Players page: select, power, per-player volume, move playback."""

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


class PlayersView(Gtk.ScrolledWindow):
    """A rail page like the others: every player, with what it's doing."""

    def __init__(self, backend, endpoint) -> None:
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.backend, self.endpoint = backend, endpoint
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=28, margin_end=28,
                      margin_top=56, margin_bottom=120)
        col.append(label("Players", "display-m", ellipsize=False))
        col.append(label("Choose what Mass controls, or move playback to another speaker.", "title-m",
                         "on-variant", ellipsize=False, wrap=True))
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_top=20)
        self.list.add_css_class("m3-list")
        self.list.add_css_class("players-list")
        self.list.connect("row-activated", self._on_activate)
        col.append(self.list)
        self.set_child(Adw.Clamp(maximum_size=900, child=col))
        backend.connect("players-changed", lambda *_: self.rebuild())
        backend.connect("active-player-changed", lambda *_: self.rebuild())
        backend.connect("status-changed", lambda *_: self.rebuild())
        backend.connect("player-updated", lambda *_: self._update())
        self._rows: dict[str, Gtk.ListBoxRow] = {}
        self.rebuild()

    def rebuild(self) -> None:
        """Sync rows to the player list in place; only add/remove/reorder when the set changes."""
        players = self.backend.players()
        ids = [p.player_id for p in players]
        for pid in [pid for pid in self._rows if pid not in ids]:
            self.list.remove(self._rows.pop(pid))
        for p in players:
            if p.player_id not in self._rows:
                self._rows[p.player_id] = row = self._make_row(p)
                self.list.append(row)
        current = []
        while row := self.list.get_row_at_index(len(current)):
            current.append(row.player_id)
        if current != ids:
            for pid in current:
                self.list.remove(self._rows[pid])
            for pid in ids:
                self.list.append(self._rows[pid])
        local_id = self.endpoint.find_player_id(self.backend)
        active = self.backend.active_player
        for p in players:
            self._sync_row(self._rows[p.player_id], p, local_id, active)
        self._update()

    def _make_row(self, p) -> Gtk.ListBoxRow:
        pid = p.player_id
        row = Gtk.ListBoxRow()
        row.player_id = pid
        row.syncing = False
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=14, margin_end=10,
                        margin_top=10, margin_bottom=10, spacing=6)
        top = Gtk.Box(spacing=14)
        row.icon = Sym(player_symbol(p), 28)
        top.append(row.icon)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        name = Gtk.Box(spacing=8)
        row.name = label(p.name, "title-m")
        name.append(row.name)
        row.badge = label("THIS PC", "badge", ellipsize=False, valign=Gtk.Align.CENTER)
        name.append(row.badge)
        text.append(name)
        row.sub = label("", "body-m", "on-variant")
        text.append(row.sub)
        top.append(text)
        row.cast = sym_button("cast", "", ("m3", "icon", "tonal"), cb=lambda: self.backend.transfer_to(pid))
        top.append(row.cast)
        row.power = Gtk.Switch(valign=Gtk.Align.CENTER, tooltip_text="Power")
        row.power.connect("notify::active", lambda s, _pp: None if row.syncing
                          else self.backend.set_power(pid, s.get_active()))
        top.append(row.power)
        outer.append(top)

        row.pressed = False
        row.vol_timer = 0
        row.vol = vol = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        vol.set_draw_value(False)
        vol.add_css_class("m3-slider")
        vol.set_margin_start(42)
        grab = Gtk.GestureClick()
        grab.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        grab.connect("pressed", lambda *_: setattr(row, "pressed", True))
        grab.connect("released", lambda *_: setattr(row, "pressed", False))
        grab.connect("stopped", lambda *_: setattr(row, "pressed", False))
        vol.add_controller(grab)

        def changed(scale) -> None:
            if row.syncing:
                return
            if row.vol_timer:
                GLib.source_remove(row.vol_timer)

            def send() -> bool:
                row.vol_timer = 0
                self.backend.set_volume(int(scale.get_value()), pid)
                return False

            row.vol_timer = GLib.timeout_add(120, send)

        vol.connect("value-changed", changed)
        outer.append(vol)
        row.set_child(outer)
        return row

    def _sync_row(self, row: Gtk.ListBoxRow, p, local_id: str | None, active) -> None:
        is_active = bool(active and active.player_id == p.player_id)
        row.set_activatable(p.available)
        row.icon.set_symbol(player_symbol(p))
        row.icon.set_fill(is_active)
        (row.add_css_class if is_active else row.remove_css_class)("current")
        row.name.set_label(p.name)
        row.badge.set_visible(p.player_id == local_id)
        row.cast.set_visible(bool(active and not is_active and p.available))
        row.cast.set_tooltip_text(f"Move playback to {p.name}")
        row.syncing = True
        try:
            row.power.set_visible(p.powered is not None)
            row.power.set_active(bool(p.powered))
            has_vol = p.volume_level is not None and p.available
            row.vol.set_visible(has_vol)
            if has_vol and not row.pressed and not row.vol_timer:
                row.vol.set_value(p.volume_level)
        finally:
            row.syncing = False

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
