"""Material 3 Expressive motion for GTK: spring-driven enter transitions.

Springs come from DankMaterialShell's Theme.springSpecs ([stiffness, damping], unit mass, see
expressive.SPRINGS) and run on Adw.SpringAnimation, so they follow the system's "reduce
animations" setting automatically.

- MotionBin: wraps one child and plays an enter transition on it (fade + rise/slide + scale,
  with the spring's overshoot giving the M3E bounce).
- spring_stack(): a Gtk.Stack whose page switches spring in instead of crossfading.
"""

from __future__ import annotations

import math

from gi.repository import Adw, GLib, Graphene, Gtk

from .expressive import SPRINGS

# Non-spring timings (ms), shared by CSS and Adw animations.
COLOR_FADE_MS = 300
LYRIC_SCROLL_MS = 450
SPLASH_MORPH_MS = 750
SPLASH_FADE_MS = 320


def spring_params(preset: str = "default") -> Adw.SpringParams:
    k, c = SPRINGS[preset]
    return Adw.SpringParams.new(c / (2 * math.sqrt(k)), 1.0, k)  # damping ratio, mass, stiffness


class MotionBin(Gtk.Widget):
    """A single-child container that can spring its child in.

    `dy`/`dx` are the start offsets in px, `scale` the start scale; progress overshoots 1.0
    with bouncy springs, so the child settles past its resting place and back.
    """

    __gtype_name__ = "MassMotionBin"

    def __init__(self, child: Gtk.Widget | None = None, dy: float = 24.0, dx: float = 0.0,
                 scale: float = 0.96, preset: str = "expressive", **kw) -> None:
        super().__init__(**kw)
        self.set_layout_manager(Gtk.BinLayout())
        self.dy, self.dx, self.scale0, self.preset = dy, dx, scale, preset
        self.progress = 1.0
        self._delay_src = 0
        self._anim = Adw.SpringAnimation.new(self, 0.0, 1.0, spring_params(preset),
                                             Adw.CallbackAnimationTarget.new(self._on_value))
        self._anim.set_clamp(False)
        self.child = None
        if child is not None:
            self.set_child(child)

    def set_child(self, child: Gtk.Widget | None) -> None:
        if self.child is not None:
            self.child.unparent()
        self.child = child
        if child is not None:
            child.set_parent(self)

    def do_dispose(self) -> None:
        if self._delay_src:
            GLib.source_remove(self._delay_src)
            self._delay_src = 0
        if self.child is not None:
            self.child.unparent()
            self.child = None

    def _on_value(self, value: float) -> None:
        self.progress = value
        self.queue_draw()

    def hold(self, dy: float | None = None, dx: float | None = None) -> None:
        """Show the start pose (progress 0) without animating yet."""
        if dy is not None:
            self.dy = dy
        if dx is not None:
            self.dx = dx
        self._anim.reset()
        self._on_value(0.0)

    def play(self, delay_ms: int = 0, dy: float | None = None, dx: float | None = None) -> None:
        """Spring the child in from its start offset (optionally after a stagger delay)."""
        if dy is not None:
            self.dy = dy
        if dx is not None:
            self.dx = dx
        if self._delay_src:
            GLib.source_remove(self._delay_src)
            self._delay_src = 0
        settings = Gtk.Settings.get_default()
        if settings is not None and not settings.get_property("gtk-enable-animations"):
            self._on_value(1.0)
            return
        self._anim.reset()
        self._on_value(0.0)
        if delay_ms > 0:
            def go():
                self._delay_src = 0
                self._anim.play()
                return False

            self._delay_src = GLib.timeout_add(delay_ms, go)
        else:
            self._anim.play()

    def do_snapshot(self, snapshot) -> None:
        if self.child is None:
            return
        p = self.progress
        if p >= 0.999 and p <= 1.001:
            self.snapshot_child(self.child, snapshot)
            return
        w, h = self.get_width(), self.get_height()
        # effects (opacity) must not overshoot; spatial values (offset, scale) may
        opacity = max(0.0, min(1.0, p * 1.6))
        s = self.scale0 + (1.0 - self.scale0) * p
        # an opacity layer renders the whole subtree offscreen, so only use one while still fading
        fading = opacity < 0.999
        if fading:
            snapshot.push_opacity(opacity)
        snapshot.save()
        snapshot.translate(Graphene.Point().init(w / 2 + self.dx * (1 - p), h / 2 + self.dy * (1 - p)))
        snapshot.scale(s, s)
        snapshot.translate(Graphene.Point().init(-w / 2, -h / 2))
        self.snapshot_child(self.child, snapshot)
        snapshot.restore()
        if fading:
            snapshot.pop()


def spring_stack(stack: Gtk.Stack, dy: float = 28.0, scale: float = 1.0,
                 preset: str = "expressive", directional: list[str] | None = None) -> MotionBin:
    """Make `stack` switch pages with an M3E spring instead of a crossfade.

    Returns the MotionBin to put in the layout where the stack used to go. With `directional`
    (the page names in visual order, e.g. tabs), pages slide sideways toward the new one.
    """
    stack.set_transition_type(Gtk.StackTransitionType.NONE)
    bin_ = MotionBin(stack, dy=dy, scale=scale, preset=preset, hexpand=stack.get_hexpand(),
                     vexpand=stack.get_vexpand())
    state = {"prev": stack.get_visible_child_name()}
    bin_.silent = False  # True: page switches happen without a transition (see prewarm)

    def changed(*_):
        name, prev = stack.get_visible_child_name(), state["prev"]
        state["prev"] = name
        if not bin_.get_mapped() or name == prev or bin_.silent:
            return
        if directional and name in directional and prev in directional:
            step = 1 if directional.index(name) > directional.index(prev) else -1
            kw = {"dy": 0.0, "dx": 48.0 * step}
        else:
            kw = {"dy": dy, "dx": 0.0}
        # hold the new page at its start pose for one frame, then spring: a page shown for the first
        # time does its expensive first layout in that frame instead of in the middle of the motion
        bin_.hold(**kw)
        bin_.add_tick_callback(lambda *_: (bin_.play(**kw), False)[1])

    stack.connect("notify::visible-child", changed)
    return bin_


def pop_in(widget: Gtk.Widget, delay_ms: int = 0, dy: float = 16.0, scale: float = 0.94,
           preset: str = "bouncy") -> MotionBin:
    """Wrap `widget` and spring it in once it's mapped (for content that just loaded)."""
    bin_ = MotionBin(widget, dy=dy, scale=scale, preset=preset, hexpand=widget.get_hexpand(),
                     vexpand=widget.get_vexpand(), halign=widget.get_halign(),
                     valign=widget.get_valign())
    bin_.progress = 0.0

    def on_map(*_):
        if handler[0]:
            bin_.disconnect(handler[0])
            handler[0] = 0
        bin_.play(delay_ms)

    handler = [0]
    if bin_.get_mapped():
        on_map()
    else:
        handler[0] = bin_.connect("map", on_map)  # once: don't replay when the page is revisited
    return bin_


def _after_anim(anim, widget: Gtk.Widget, fn) -> None:
    # "notify::state", not "done": play()/hold() reset() a running spring, which goes
    # back to IDLE without ever emitting "done", and fn would be lost for good
    def changed(*_):
        if anim.get_state() == Adw.AnimationState.PLAYING:
            return
        anim.disconnect(handler)
        after_motion(widget, fn)  # an outer one may still be running

    handler = anim.connect("notify::state", changed)


def after_motion(widget: Gtk.Widget, fn) -> None:
    """Run fn now, or once the enclosing spring transition settles if one is playing.

    Laying out freshly loaded content is the expensive part of a frame; doing it mid-spring
    makes the motion stutter, so content that arrives during a transition waits for its end.
    """
    w = widget.get_parent()
    while w is not None:  # any enclosing transition counts (a tab switch inside a page switch…)
        if isinstance(w, MotionBin) and w._anim.get_state() == Adw.AnimationState.PLAYING:
            _after_anim(w._anim, widget, fn)
            return
        w = w.get_parent()
    fn()
