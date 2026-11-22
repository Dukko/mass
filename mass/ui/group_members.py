"""Choose which speakers play in sync with a leader player."""

from __future__ import annotations

from gi.repository import Adw, GLib
from music_assistant_models.enums import PlayerType

FLUSH_MS = 600
SETTLE_MS = 3000


def group_candidates(leader, players: list) -> list:
    """Players that may join (or already are in) the leader's group, sorted by name."""
    can_group = set(getattr(leader, "can_group_with", None) or ())
    members = set(getattr(leader, "group_members", None) or ())
    out = []
    for p in players:
        if p.player_id == leader.player_id:
            continue
        if p.player_id in members:
            out.append(p)
            continue
        if not p.available or p.type == PlayerType.GROUP:
            continue
        if p.player_id not in can_group and p.provider not in can_group:
            continue
        active_group = getattr(p, "active_group", None)
        if active_group and active_group != leader.player_id:
            continue
        out.append(p)
    return sorted(out, key=lambda p: p.name.casefold())


def is_locked(leader, pid: str) -> bool:
    return pid == leader.player_id or pid in (getattr(leader, "static_group_members", None) or ())


def apply_toggle(add: set[str], remove: set[str], pid: str, want: bool, is_member: bool) -> None:
    """Record a switch change; flipping back to the player's current membership cancels it out."""
    add.discard(pid)
    remove.discard(pid)
    if want and not is_member:
        add.add(pid)
    elif not want and is_member:
        remove.add(pid)


class GroupMembersDialog(Adw.Dialog):
    def __init__(self, backend, leader_id: str) -> None:
        leader = backend.get_player(leader_id)
        super().__init__(title=f"Group with {leader.name if leader else 'speaker'}",
                         content_width=480, content_height=560)
        self.backend, self.leader_id = backend, leader_id
        self.syncing = False
        self._add: set[str] = set()
        self._remove: set[str] = set()
        self._flushed: dict[str, bool] = {}
        self._timer = 0
        self._settle_timer = 0
        self._parent = None
        self._rows: dict[str, Adw.SwitchRow] = {}

        self._page = Adw.PreferencesPage()
        self._group = Adw.PreferencesGroup(description="Speakers that play in sync with this one.")
        self._page.add(self._group)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        tv.set_content(self._page)
        self.set_child(tv)

        self._handler = backend.connect("player-updated", lambda *_: self._sync())
        self.connect("closed", self._on_closed)
        self._sync()

    def present(self, parent=None) -> None:
        self._parent = parent
        super().present(parent)

    def _sync(self) -> None:
        leader = self.backend.get_player(self.leader_id)
        if not leader:
            return
        members = set(getattr(leader, "group_members", None) or ())
        for pid in [pid for pid, want in self._flushed.items() if (pid in members) == want]:
            del self._flushed[pid]
        candidates = group_candidates(leader, self.backend.players())
        wanted = [p.player_id for p in candidates]
        self.syncing = True
        try:
            for pid in [pid for pid in self._rows if pid not in wanted]:
                self._group.remove(self._rows.pop(pid))
            for p in candidates:
                pid = p.player_id
                row = self._rows.get(pid)
                if row is None:
                    row = Adw.SwitchRow(title=GLib.markup_escape_text(p.name))
                    row.connect("notify::active", self._on_toggle, pid)
                    self._rows[pid] = row
                    self._group.add(row)
                row.set_title(GLib.markup_escape_text(p.name))
                row.set_active(self._shown_state(pid, pid in members))
                row.set_sensitive(not is_locked(leader, pid))
        finally:
            self.syncing = False

    def _shown_state(self, pid: str, current: bool) -> bool:
        if pid in self._add:
            return True
        if pid in self._remove:
            return False
        return self._flushed.get(pid, current)

    def _on_toggle(self, row: Adw.SwitchRow, _pspec, pid: str) -> None:
        if self.syncing:
            return
        leader = self.backend.get_player(self.leader_id)
        is_member = bool(leader) and pid in (getattr(leader, "group_members", None) or ())
        self._flushed.pop(pid, None)
        apply_toggle(self._add, self._remove, pid, row.get_active(), is_member)
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(FLUSH_MS, self._on_timer)

    def _on_timer(self) -> bool:
        self._timer = 0
        self._flush()
        return False

    def _flush(self) -> None:
        add, remove = sorted(self._add), sorted(self._remove)
        self._add.clear()
        self._remove.clear()
        if not add and not remove:
            return
        self._flushed.update({pid: True for pid in add})
        self._flushed.update({pid: False for pid in remove})
        if self._settle_timer:
            GLib.source_remove(self._settle_timer)
        self._settle_timer = GLib.timeout_add(SETTLE_MS, self._on_settled)
        self.backend.set_group_members(self.leader_id, add, remove, self._on_fail)

    def _on_settled(self) -> bool:
        self._settle_timer = 0
        self._flushed.clear()
        if self._handler:
            self._sync()
        return False

    def _on_fail(self, err: Exception) -> None:
        GLib.idle_add(self._report_fail, err)

    def _report_fail(self, err: Exception) -> bool:
        self._flushed.clear()
        text = f"Couldn't change group: {err}"
        if self._handler:
            self.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))
            self._sync()
        elif hasattr(self._parent, "toast"):
            self._parent.toast(text)
        return False

    def _on_closed(self, *_) -> None:
        self.backend.disconnect(self._handler)
        self._handler = 0
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        if self._settle_timer:
            GLib.source_remove(self._settle_timer)
            self._settle_timer = 0
        self._flush()
        if self._settle_timer:
            GLib.source_remove(self._settle_timer)
            self._settle_timer = 0
