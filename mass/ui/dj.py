"""AI Radio / AI DJ: segment detection, DJ chip, captions, and the AI Radio page."""

from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from .common import label
from .dj_editor import AI_ICON, DJEditor, dj_icon, new_dj_menu  # noqa: F401 (re-exported)
from .expressive import ShapeArt, Sym, sym_button

DJ_SHAPE = "burst"


def dj_segment(item) -> dict | None:
    """If a queue item is an AI DJ segment, return its ai_radio attributes."""
    if item is None:
        return None
    extra = getattr(item, "extra_attributes", None) or {}
    mi = getattr(item, "media_item", None)
    if "ai_radio_host_id" in extra or getattr(mi, "provider", None) == "ai_radio":
        return {
            "name": getattr(mi, "name", None) or item.name,
            "host_id": extra.get("ai_radio_host_id", ""),
            "text": extra.get("ai_radio_rendered_text") or "",
            "session": extra.get("ai_radio_session_id", ""),
            "station": extra.get("ai_radio_station_id", ""),
        }
    return None


def host_name(backend, host_id: str) -> str:
    h = backend.dj_hosts.get(host_id)
    return h["name"] if h else (host_id.replace("_", " ").title() if host_id else "AI DJ")


def _excerpt(text: str, n: int = 160) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


class DJChip(Gtk.MenuButton):
    """Shows the sticky DJ on the active queue; popover switches host or turns it off."""

    def __init__(self, backend) -> None:
        super().__init__(halign=Gtk.Align.START, tooltip_text="AI DJ")
        self.backend = backend
        self.add_css_class("dj-chip")
        box = Gtk.Box(spacing=8)
        self.sym = Sym(AI_ICON, 20, fill=True)
        box.append(self.sym)
        self.lbl = label("AI DJ", max_width_chars=26)
        box.append(self.lbl)
        box.append(Sym("expand_more", 20))
        self.set_child(box)
        self.pop = Gtk.Popover(has_arrow=False)
        self.set_popover(self.pop)
        self.pop.connect("show", lambda *_: self._build())
        for sig in ("ai-changed", "active-player-changed", "queue-updated", "status-changed"):
            backend.connect(sig, lambda *_: self.refresh())
        self.refresh()

    def _qid(self) -> str | None:
        q, p = self.backend.active_queue, self.backend.active_player
        return q.queue_id if q else (p.player_id if p else None)

    def refresh(self) -> None:
        self.set_visible(self.backend.ai_available)
        host = self.backend.dj_for(self._qid())
        self.lbl.set_label(f"DJ · {host['name']}" if host else "Add AI DJ")
        self.sym.set_symbol(dj_icon(self.backend, host["id"]) if host else AI_ICON)
        (self.add_css_class if host else self.remove_css_class)("on")

    def _build(self) -> None:
        cur = self.backend.dj_for(self._qid())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=8, margin_bottom=8,
                      margin_start=8, margin_end=8, width_request=300)
        box.append(label("AI DJ for this player", "title-s", margin_start=8, margin_bottom=6))
        for hid, h in self.backend.dj_hosts.items():
            btn = Gtk.Button()
            btn.add_css_class("flat")
            row = Gtk.Box(spacing=12)
            row.append(Sym("check" if cur and cur["id"] == hid else dj_icon(self.backend, hid), 20,
                           fill=bool(cur and cur["id"] == hid)))
            t = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            t.append(label(h["name"], "label-l"))
            t.append(label(_excerpt(h.get("instructions", ""), 60), "label-m", "on-variant"))
            row.append(t)
            btn.set_child(row)
            btn.connect("clicked", lambda _b, i=hid: (self.pop.popdown(), self.backend.set_dj(i)))
            box.append(btn)
        if cur:
            acts = Gtk.Box(spacing=8, margin_top=6, homogeneous=True)
            edit = Gtk.Button(label=f"Edit {cur['name']}")
            edit.get_child().set_ellipsize(3)
            for c in ("m3", "outlined"):
                edit.add_css_class(c)
            edit.connect("clicked", lambda *_: (self.pop.popdown(), DJEditor(self.backend, self, cur["id"])))
            acts.append(edit)
            off = Gtk.Button(label="Turn DJ off")
            for c in ("m3", "tonal"):
                off.add_css_class(c)
            off.connect("clicked", lambda *_: (self.pop.popdown(), self.backend.set_dj(None)))
            acts.append(off)
            box.append(acts)
        self.pop.set_child(box)


class DJCaption(Gtk.Box):
    """'On air' caption card with what the DJ is saying right now."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("dj-caption")
        head = Gtk.Box(spacing=8)
        head.append(Sym("graphic_eq", 20, fill=True))
        self.head = label("ON AIR", "overline", ellipsize=False, hexpand=True)
        head.append(self.head)
        self.append(head)
        self.text = label("", "dj-caption-text", ellipsize=False, wrap=True)
        self.text.set_wrap_mode(2)
        self.append(self.text)
        self.set_visible(False)

    def show_segment(self, seg: dict | None, host: str = "") -> None:
        if not seg:
            self.set_visible(False)
            return
        self.head.set_label(f"ON AIR · {host.upper()}" if host else "ON AIR")
        self.text.set_label(f"“{seg['text']}”" if seg["text"] else "Preparing the next break…")
        self.set_visible(True)


class DJTranscript(Gtk.ListBox):
    """Recent and upcoming DJ breaks of the active queue, with what the host says."""

    def __init__(self, backend) -> None:
        super().__init__(selection_mode=Gtk.SelectionMode.NONE)
        self.add_css_class("m3-list")
        self.backend = backend

    def load(self) -> None:
        q = self.backend.active_queue
        if not q or not self.backend.client:
            self._fill([], None)
            return

        async def go():
            items = await self.backend.client.player_queues.get_queue_items(q.queue_id, limit=500)
            q2 = self.backend.active_queue  # q is a stale snapshot by now: the client replaces it per update
            if not q2 or q2.queue_id != q.queue_id:
                return
            cur = q2.current_item.queue_item_id if q2.current_item else None
            ids = [i.queue_item_id for i in items]
            self._fill(items, ids.index(cur) if cur in ids else q2.current_index)

        self.backend.run(go())

    def _fill(self, items, current_index) -> None:
        while r := self.get_row_at_index(0):
            self.remove(r)
        segs = []
        for i, it in enumerate(items):
            if (seg := dj_segment(it)) and seg["text"]:
                segs.append((i, seg))
        # most recent first: the ones at/before the current position, then upcoming
        cur = current_index if current_index is not None else len(items)
        past = [s for s in segs if s[0] <= cur][-6:][::-1]
        upcoming = [s for s in segs if s[0] > cur][:2]
        if not past and not upcoming:
            row = Gtk.ListBoxRow(activatable=False)
            row.set_child(label("No DJ breaks in this queue yet.", "body-m", "on-variant", margin_start=16,
                                margin_top=16, margin_bottom=16))
            self.append(row)
            return
        for idx, seg in upcoming[::-1] + past:
            row = Gtk.ListBoxRow(activatable=False)
            box = Gtk.Box(spacing=14, margin_start=14, margin_end=14, margin_top=12, margin_bottom=12)
            box.append(Sym(dj_icon(self.backend, seg["host_id"]), 22, fill=idx == cur))
            t = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
            when = "ON AIR" if idx == cur else ("UP NEXT" if idx > cur else "EARLIER")
            t.append(label(f"{when} · {seg['name'].upper()} · {host_name(self.backend, seg['host_id']).upper()}",
                           "overline", "on-variant"))
            txt = label(seg["text"], "body-m", ellipsize=False, wrap=True)
            txt.set_wrap_mode(2)
            t.append(txt)
            box.append(t)
            row.set_child(box)
            if idx == cur:
                row.add_css_class("current")
            self.append(row)


class AIRadioView(Gtk.ScrolledWindow):
    """Stations, hosts, and the live DJ transcript for the active queue."""

    def __init__(self, backend) -> None:
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.backend = backend
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, margin_start=28, margin_end=28,
                      margin_top=56, margin_bottom=120)
        self.set_child(col)
        col.append(label("AI Radio", "display-m", ellipsize=False))
        col.append(label("DJ hosts and stations from your Music Assistant server.", "title-m", "on-variant",
                         ellipsize=False, wrap=True))

        # -------- current player DJ hero
        hero = Gtk.Box(spacing=24)
        hero.add_css_class("ai-hero")
        self.hero_art = ShapeArt(120, DJ_SHAPE, "sym:" + AI_ICON)
        hero.append(self.hero_art)
        ht = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER, hexpand=True)
        self.hero_over = label("", "overline")
        self.hero_title = label("", "headline-m", max_width_chars=1, hexpand=True)
        self.hero_sub = label("", "body-m", "on-variant", ellipsize=False, wrap=True, lines=2)
        for w in (self.hero_over, self.hero_title, self.hero_sub):
            ht.append(w)
        hero.append(ht)
        self.hero_chip = DJChip(backend)
        self.hero_chip.set_valign(Gtk.Align.CENTER)
        hero.append(self.hero_chip)
        col.append(hero)

        # -------- live transcript
        col.append(label("Recent DJ breaks", "section-title", margin_top=8))
        self.transcript = DJTranscript(backend)
        col.append(self.transcript)

        # -------- stations
        col.append(label("Stations", "section-title", margin_top=8))
        self.stations = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3,
                                    min_children_per_line=1, column_spacing=12, row_spacing=12, homogeneous=True)
        col.append(self.stations)

        # -------- hosts
        hosts_head = Gtk.Box(spacing=12, margin_top=8)
        hosts_head.append(label("Hosts", "section-title", hexpand=True))
        hosts_head.append(new_dj_menu(backend, self))
        col.append(hosts_head)
        self.hosts = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3,
                                 min_children_per_line=1, column_spacing=12, row_spacing=12, homogeneous=True)
        col.append(self.hosts)

        for sig in ("ai-changed", "active-player-changed"):
            backend.connect(sig, lambda *_: self.get_mapped() and self.reload())
        backend.connect("queue-updated", lambda _b, qid: self.get_mapped() and self.backend.is_relevant(qid)
                        and self.transcript.load())
        self.connect("map", lambda *_: self.reload())

    # ------------------------------------------------------------------ data

    def reload(self) -> None:
        if not self.backend.ai_available:
            return
        self._hero()
        self.transcript.load()

        async def go():
            stations = await self.backend.ai("stations/list")
            self._fill_stations(stations or [])

        self.backend.run(go())
        self._fill_hosts()

    def _hero(self) -> None:
        b = self.backend
        p, q = b.active_player, b.active_queue
        host = b.dj_for(q.queue_id if q else (p.player_id if p else None))
        self.hero_over.set_label(f"AI DJ · {p.name.upper()}" if p else "AI DJ")
        self.hero_title.set_label(host["name"] if host else "No DJ on this player")
        self.hero_sub.set_label(_excerpt(host.get("instructions", ""), 200) if host else
                                "Pick a host to talk between your songs: transitions, artist facts, news.")
        self.hero_art.set_icon("sym:" + (dj_icon(b, host["id"]) if host else AI_ICON))
        self.hero_art.set_spinning(0.4 if host else 0.0)

    def _fill_stations(self, stations) -> None:
        self.stations.remove_all()
        if not stations:
            self.stations.append(label("No stations yet. Create them in the Music Assistant web UI.",
                                       "body-m", "on-variant", ellipsize=False))
            return
        for st in stations:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            card.add_css_class("ai-card")
            top = Gtk.Box(spacing=12)
            art = ShapeArt(56, "cookie9", "network-wireless-symbolic")
            top.append(art)
            tt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
            tt.append(label(st.get("name", st["id"]), "title-m", max_width_chars=1, hexpand=True))
            tt.append(label(f"Hosted by {host_name(self.backend, st.get('host_id', ''))}", "label-m",
                            "on-variant", max_width_chars=1))
            top.append(tt)
            card.append(top)
            btns = Gtk.Box(spacing=8, margin_top=6)
            start = Gtk.Button()
            for c in ("m3", "filled"):
                start.add_css_class(c)
            sb = Gtk.Box(spacing=6)
            sb.append(Sym("play_arrow", 20, fill=True))
            sb.append(Gtk.Label(label="Start here"))
            start.set_child(sb)
            start.connect("clicked", lambda _b, s=st: self._start(s))
            btns.append(start)
            btns.append(sym_button("stop", "Stop station", ("m3", "icon", "tonal"),
                                   cb=lambda s=st: self._stop(s)))
            card.append(btns)
            self.stations.append(card)

    def _fill_hosts(self) -> None:
        self.hosts.remove_all()
        p, q = self.backend.active_player, self.backend.active_queue
        cur = self.backend.dj_for(q.queue_id if q else (p.player_id if p else None))
        for hid, h in self.backend.dj_hosts.items():
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            card.add_css_class("ai-card")
            if cur and cur["id"] == hid:
                card.add_css_class("on")
            top = Gtk.Box(spacing=12)
            top.append(ShapeArt(48, "circle" if not (cur and cur["id"] == hid) else DJ_SHAPE,
                                "sym:" + dj_icon(self.backend, hid)))
            tt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
            tt.append(label(h["name"], "title-m", max_width_chars=1, hexpand=True))
            voice = (h.get("options") or {}).get("voice") or h.get("tts_engine") or "default voice"
            tt.append(label(f"{len(h.get('section_ids') or [])} segments · {voice}", "label-m", "on-variant",
                            max_width_chars=1))
            top.append(tt)
            card.append(top)
            desc = label(_excerpt(h.get("instructions", ""), 180), "body-m", "on-variant", ellipsize=False,
                         wrap=True, lines=4)
            desc.set_wrap_mode(2)
            card.append(desc)
            use = Gtk.Button(label="Current DJ" if cur and cur["id"] == hid else "Use on this player",
                             halign=Gtk.Align.START, sensitive=not (cur and cur["id"] == hid))
            for c in ("m3", "tonal"):
                use.add_css_class(c)
            use.connect("clicked", lambda _b, i=hid: self.backend.set_dj(i))
            acts = Gtk.Box(spacing=8)
            acts.append(use)
            acts.append(sym_button("edit", "Edit DJ", ("m3", "icon", "tonal"),
                                   cb=lambda i=hid: DJEditor(self.backend, self, i)))
            card.append(acts)
            self.hosts.append(card)

    def _start(self, station) -> None:
        p = self.backend.active_player
        kwargs = {"station_id": station["id"]}
        if p:
            kwargs["player_id_override"] = p.player_id

        async def go():
            await self.backend.ai("start", **kwargs)
            GLib.timeout_add_seconds(2, lambda: (self.backend.run(self.backend.refresh_ai()), False)[1])

        self.backend.run(go())

    def _stop(self, station) -> None:
        self.backend.run(self.backend.ai("stop", station_id=station["id"]))


_ = Adw  # imported for side effects in some callers
