"""Drag-to-reorder pieces shared by the queue and playlist pages."""

from __future__ import annotations

from gi.repository import GLib, Gtk

AUTOSCROLL_EDGE = 48  # px from the scroller's top/bottom where a drag starts scrolling


def move_target(src: int, insert_at: int) -> tuple[int, int] | None:
    """(final row, server pos_shift) for moving row `src` to insertion index `insert_at`; None if it stays put."""
    final = insert_at - 1 if insert_at > src else insert_at
    shift = final - src
    return None if shift == 0 else (final, shift)


def playlist_move(positions: list[int], src: int, insert_at: int) -> tuple[int, int, int] | None:
    """(final row, from_position, to_position) for a row drag in a playlist; None if the row stays put.

    `positions` are the server's playlist positions per row, which can have gaps where the server skipped
    unresolvable tracks. The dragged track keeps its own position as the source, and the destination is the
    position held by the row it lands on.
    """
    target = move_target(src, insert_at)
    if target is None:
        return None
    final, _shift = target
    return final, positions[src], positions[final]


def swallow_clicks(handle: Gtk.Widget, drag_source: Gtk.DragSource) -> None:
    """Stop a click on a drag handle from reaching the row's own activation.

    Grouped with the drag source so claiming the press doesn't cancel the drag.
    """
    click = Gtk.GestureClick()
    click.connect("pressed", lambda g, *_: g.set_state(Gtk.EventSequenceState.CLAIMED))
    handle.add_controller(click)
    click.group(drag_source)  # both must already sit on the same widget


class AutoScroll:
    """GTK doesn't scroll a list while something is dragged over its edges; this does."""

    def __init__(self, scroller: Gtk.ScrolledWindow) -> None:
        self.scroller = scroller
        self._y: float | None = None  # pointer y over the scroller during a drag
        self._source = 0
        motion = Gtk.DropControllerMotion()
        motion.connect("motion", lambda _c, _x, y: self.update(y))
        motion.connect("leave", lambda *_: self.update(None))
        scroller.add_controller(motion)

    def update(self, y: float | None) -> None:
        self._y = y
        if y is not None and not self._source:
            self._source = GLib.timeout_add(16, self._tick)

    def _tick(self) -> bool:
        y = self._y
        if y is None:
            self._source = 0
            return False
        h = self.scroller.get_height()
        step = 0.0
        if y < AUTOSCROLL_EDGE:
            step = -(AUTOSCROLL_EDGE - y) / 3
        elif y > h - AUTOSCROLL_EDGE:
            step = (y - (h - AUTOSCROLL_EDGE)) / 3
        if step:
            adj = self.scroller.get_vadjustment()
            adj.set_value(adj.get_value() + step)
        return True
