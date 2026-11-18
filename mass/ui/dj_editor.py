"""Edit AI Radio DJ hosts in-app: personality, voice, segment schedule and segment prompts.

Talks to the server's ai_radio provider: hosts/{get,save,delete,template,presets/list},
sections/{get,save} and engines/tts/list.
"""

from __future__ import annotations

import copy
import logging
import re
import uuid

from gi.repository import Adw, GLib, Gtk

from .expressive import Sym

log = logging.getLogger(__name__)

AI_ICON = "auto_awesome"  # the default icon for an AI DJ host, everywhere one is shown
# icons offered in the editor (Material Symbols names)
DJ_ICONS = (
    "auto_awesome", "smart_toy", "robot_2", "psychology", "neurology", "emoji_objects",
    "graphic_eq", "radio", "headphones", "mic", "record_voice_over", "campaign",
    "music_note", "album", "nightlife", "piano", "face", "sentiment_very_satisfied",
    "pets", "rocket_launch", "bolt", "local_fire_department", "coffee", "favorite",
)


def dj_icon(backend, host_id: str | None) -> str:
    """The Material Symbols name for a DJ host: the user's pick, or AI_ICON."""
    return (backend.config.dj_icons.get(host_id) if host_id else None) or AI_ICON


def set_dj_icon(backend, host_id: str, icon: str) -> None:
    icons = backend.config.dj_icons
    if icon == AI_ICON:
        icons.pop(host_id, None)
    else:
        icons[host_id] = icon
    backend.config.save()


class IconPicker(Gtk.MenuButton):
    """A button showing the chosen symbol; its popover is a grid of DJ_ICONS."""

    def __init__(self, icon: str) -> None:
        super().__init__(valign=Gtk.Align.CENTER, tooltip_text="Icon")
        self.add_css_class("flat")
        self.icon = icon
        self.sym = Sym(icon, 24, fill=True)
        self.set_child(self.sym)
        grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=6,
                           min_children_per_line=6, homogeneous=True, column_spacing=4, row_spacing=4,
                           margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        icons = DJ_ICONS if icon in DJ_ICONS else (icon, *DJ_ICONS)
        for name in icons:
            b = Gtk.Button(tooltip_text=name.replace("_", " ").capitalize())
            b.add_css_class("flat")
            b.add_css_class("circular")
            b.set_child(Sym(name, 24, fill=True))
            b.connect("clicked", lambda _b, n=name: self.set_icon(n))
            grid.append(b)
        self.pop = Gtk.Popover(child=grid)
        self.set_popover(self.pop)

    def set_icon(self, name: str) -> None:
        self.icon = name
        self.sym.set_symbol(name)
        self.pop.popdown()

WHEN_TITLES = {
    "start_of_playlist": "When a show starts",
    "between_songs": "Between songs",
    "end_of_playlist": "When a show ends",
}


def _when_title(when: str) -> str:
    return WHEN_TITLES.get(when, when.replace("_", " ").capitalize())


def _text_area(text: str, placeholder_hint: str = "", min_height: int = 120) -> tuple[Gtk.Widget, Gtk.TextBuffer]:
    buf = Gtk.TextBuffer(text=text or "")
    view = Gtk.TextView(buffer=buf, wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=12, bottom_margin=12,
                        left_margin=14, right_margin=14, accepts_tab=False)
    view.add_css_class("dj-text")
    sw = Gtk.ScrolledWindow(child=view, hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=min_height,
                            max_content_height=360, propagate_natural_height=True)
    sw.add_css_class("card")
    sw.add_css_class("dj-text-card")
    if placeholder_hint:
        sw.set_tooltip_text(placeholder_hint)
    return sw, buf


def _buf_text(buf: Gtk.TextBuffer) -> str:
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)


def _spin(title: str, value: float, lo: float, hi: float, step: float, digits: int = 0,
          subtitle: str = "") -> Adw.SpinRow:
    row = Adw.SpinRow.new_with_range(lo, hi, step)
    row.set_title(title)
    row.set_digits(digits)
    row.set_value(value)
    if subtitle:
        row.set_subtitle(subtitle)
    return row


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_") or "dj"
    return f"{s}_{uuid.uuid4().hex[:6]}"


class DJEditor:
    """Adw.Dialog editing one host (existing, or a new one from a template/preset)."""

    def __init__(self, backend, parent: Gtk.Widget, host_id: str | None = None,
                 template: dict | None = None) -> None:
        self.backend, self.parent = backend, parent
        self.host_id, self.template = host_id, template
        self.is_new = host_id is None
        self.sections: dict[str, dict] = {}
        self.dirty_sections: set[str] = set()
        self.buffers: dict[str, Gtk.TextBuffer] = {}
        backend.run(self._load())

    # ------------------------------------------------------------------ data

    def _toast_parent(self, text: str) -> None:
        if (win := self.parent.get_root()) and hasattr(win, "toast"):
            win.toast(text)

    async def _load(self) -> None:
        b = self.backend
        try:
            if self.is_new:
                base = self.template if self.template is not None else await b.ai("hosts/template")
                self.host = copy.deepcopy(base)
                self.host.pop("_icon", None)  # ours (Duplicate), never sent to the server
                self.host["id"] = _slug(self.host.get("name") or "dj")
            else:
                self.host = await b.ai("hosts/get", host_id=self.host_id)
            self.voices = await b.ai("engines/tts/list") or []
        except Exception as err:  # noqa: BLE001
            self._toast_parent(f"Couldn’t load the DJ: {err}")
            return
        # embedded section definitions (presets) or shared ones on the server
        embedded = {s["id"]: s for s in self.host.pop("sections", []) or [] if isinstance(s, dict) and "id" in s}
        for sid in self.host.get("section_ids") or []:
            try:  # the server's copy wins, so a preset never overwrites edits made since
                self.sections[sid] = await b.ai("sections/get", section_id=sid)
            except Exception:  # noqa: BLE001 - not on the server
                if sid in embedded:
                    self.sections[sid] = embedded[sid]
                    self.dirty_sections.add(sid)  # created on save
                else:
                    log.debug("section %s not found", sid)
        self._build()

    def _shared_with(self, sid: str) -> list[str]:
        me = self.host.get("id")
        return [h["name"] for hid, h in self.backend.dj_hosts.items()
                if hid != me and sid in (h.get("section_ids") or [])]

    # ------------------------------------------------------------------ UI

    def _build(self) -> None:
        h = self.host
        self.dlg = Adw.Dialog(title="New DJ" if self.is_new else f"Edit {h.get('name', 'DJ')}",
                              content_width=640, content_height=760)
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        self.save_btn = Gtk.Button(label="Create" if self.is_new else "Save")
        self.save_btn.add_css_class("suggested-action")
        self.save_btn.connect("clicked", lambda *_: self.backend.run(self._save()))
        hb.pack_end(self.save_btn)
        tv.add_top_bar(hb)
        page = Adw.PreferencesPage()
        tv.set_content(page)
        self.dlg.set_child(tv)

        # -------- identity
        who = Adw.PreferencesGroup(title="Host")
        self.name_row = Adw.EntryRow(title="Name", text=h.get("name", ""))
        who.add(self.name_row)
        start = self.template.get("_icon") if self.template else None
        self.icon_picker = IconPicker(start or dj_icon(self.backend, None if self.is_new else h.get("id")))
        icon_row = Adw.ActionRow(title="Icon", subtitle="Shown for this DJ in Mass")
        icon_row.add_suffix(self.icon_picker)
        icon_row.set_activatable_widget(self.icon_picker)
        who.add(icon_row)
        voice_ids = [""] + [v["uid"] for v in self.voices]
        if h.get("tts_engine") and h["tts_engine"] not in voice_ids:
            voice_ids.append(h["tts_engine"])
        names = ["Server default"] + [v["name"] for v in self.voices]
        names += [h["tts_engine"]] * (len(voice_ids) - len(names))
        self.voice_row = Adw.ComboRow(title="Voice", model=Gtk.StringList.new(names),
                                      enable_search=len(names) > 8)
        self.voice_row.set_selected(voice_ids.index(h.get("tts_engine") or ""))
        self.voice_ids = voice_ids
        who.add(self.voice_row)
        self.lang_row = Adw.EntryRow(title="Language (e.g. en, it — empty for automatic)",
                                     text=h.get("language") or "")
        who.add(self.lang_row)
        page.add(who)

        # -------- personality
        pers = Adw.PreferencesGroup(title="Personality",
                                    description="How the DJ talks. Placeholders such as &lt;current_time&gt; are "
                                                "filled in by the server.")
        area, self.instr_buf = _text_area(h.get("instructions", ""), min_height=160)
        pers.add(area)
        page.add(pers)

        # -------- schedule
        for block in h.get("section_order") or []:
            grp = Adw.PreferencesGroup(title=_when_title(block.get("when", "")))
            for entry in block.get("flow") or []:
                for row in self._flow_rows(entry):
                    grp.add(row)
            page.add(grp)

        # -------- segments
        segs = Adw.PreferencesGroup(title="Segments", description="What the DJ is asked to write for each break")
        merge = h.get("merge_section_id")
        for sid, sec in self.sections.items():
            segs.add(self._section_row(sid, sec, is_merge=sid == merge))
        if not self.sections:
            segs.add(Adw.ActionRow(title="No segments", css_classes=["dim-label"]))
        page.add(segs)

        # -------- manage
        if not self.is_new:
            mng = Adw.PreferencesGroup()
            dup = Adw.ButtonRow(title="Duplicate DJ", start_icon_name="edit-copy-symbolic")
            dup.connect("activated", lambda *_: self._duplicate())
            mng.add(dup)
            rm = Adw.ButtonRow(title="Delete DJ", start_icon_name="user-trash-symbolic")
            rm.add_css_class("destructive-action")
            rm.connect("activated", lambda *_: self._confirm_delete())
            mng.add(rm)
            page.add(mng)

        self.dlg.present(self.parent.get_root())

    def _sec_name(self, sid: str) -> str:
        sec = self.sections.get(sid) or {}
        return sec.get("name") or sid.replace("_", " ").capitalize()

    def _flow_rows(self, entry: dict) -> list[Gtk.Widget]:
        """Rows for one schedule entry: {"MUST": id} | {"OPTIONAL": {...}} | {"ALTERNATIVE": {...}}."""
        rows: list[Gtk.Widget] = []
        if "MUST" in entry:
            sid = entry["MUST"]
            rows.append(Adw.ActionRow(title=self._sec_name(sid), subtitle="Always"))
        elif "OPTIONAL" in entry:
            opt = entry["OPTIONAL"]
            guards = opt.setdefault("guards", {})
            exp = Adw.ExpanderRow(title=self._sec_name(opt.get("section", "")),
                                  subtitle=f"{round(opt.get('chance', 1) * 100)}% chance")
            chance = _spin("Chance", round(opt.get("chance", 1.0) * 100), 0, 100, 5, subtitle="%")

            def on_chance(r, _p):
                opt["chance"] = r.get_value() / 100
                exp.set_subtitle(f"{round(r.get_value())}% chance")

            chance.connect("notify::value", on_chance)
            exp.add_row(chance)
            gap = _spin("At least this many songs apart", guards.get("min_gap_songs", 0), 0, 50, 1)
            gap.connect("notify::value", lambda r, _p: guards.__setitem__("min_gap_songs", int(r.get_value())))
            exp.add_row(gap)
            cap = _spin("At most per hour", guards.get("max_per_60min", 0), 0, 60, 1, subtitle="0 = no limit")
            cap.connect("notify::value", lambda r, _p: guards.__setitem__("max_per_60min", int(r.get_value())))
            exp.add_row(cap)
            rows.append(exp)
        elif "ALTERNATIVE" in entry:
            alt = entry["ALTERNATIVE"]
            choices = alt.get("choices") or []
            exp = Adw.ExpanderRow(title="One of", subtitle=", ".join(self._sec_name(c.get("section", ""))
                                                                     for c in choices))
            if "chance" in alt:
                chance = _spin("Chance", round(alt["chance"] * 100), 0, 100, 5, subtitle="%")
                chance.connect("notify::value", lambda r, _p: alt.__setitem__("chance", r.get_value() / 100))
                exp.add_row(chance)
            for c in choices:
                w = _spin(self._sec_name(c.get("section", "")), c.get("weight", 1), 0, 100, 1,
                          subtitle="weight")
                w.connect("notify::value", lambda r, _p, c=c: c.__setitem__("weight", r.get_value()))
                exp.add_row(w)
            rows.append(exp)
        return rows

    def _section_row(self, sid: str, sec: dict, is_merge: bool) -> Adw.ExpanderRow:
        shared = self._shared_with(sid)
        sub = [str(sec.get("type", "")).replace("_", " ")]
        if is_merge:
            sub.append("smooths consecutive breaks")
        if shared:
            sub.append(f"shared with {', '.join(shared)}")
        exp = Adw.ExpanderRow(title=self._sec_name(sid), subtitle=" · ".join(x for x in sub if x))

        def mark(*_):
            self.dirty_sections.add(sid)

        name = Adw.EntryRow(title="Name", text=sec.get("name", ""))
        name.connect("changed", lambda r: (sec.__setitem__("name", r.get_text()), mark()))
        exp.add_row(name)
        if "prompt" in sec or sec.get("type") == "ai_text":
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=10, margin_bottom=10,
                          margin_start=12, margin_end=12)
            hint = Gtk.Label(label="Prompt — placeholders like <prev_songinfo>, <next_songinfo>",
                             xalign=0, css_classes=["caption", "dim-label"])
            box.append(hint)
            area, buf = _text_area(sec.get("prompt", ""), min_height=110)
            buf.connect("changed", mark)
            self.buffers[sid] = buf
            box.append(area)
            if shared:
                box.append(Gtk.Label(label=f"Changes here also apply to {', '.join(shared)}.", xalign=0,
                                     wrap=True, css_classes=["caption", "warning"]))
            exp.add_row(box)
        cons = sec.setdefault("constraints", {}) if "constraints" in sec or sec.get("type") == "ai_text" else None
        if cons is not None:
            mc = _spin("Maximum length", cons.get("max_chars", 300), 20, 4000, 10, subtitle="characters")
            mc.connect("notify::value", lambda r, _p: (cons.__setitem__("max_chars", int(r.get_value())), mark()))
            exp.add_row(mc)
        return exp

    # ------------------------------------------------------------------ actions

    def _collect(self) -> dict:
        h = self.host
        h["name"] = self.name_row.get_text().strip() or h.get("name") or "DJ"
        h["instructions"] = _buf_text(self.instr_buf)
        h["tts_engine"] = self.voice_ids[self.voice_row.get_selected()]
        h["language"] = self.lang_row.get_text().strip()
        for sid, buf in self.buffers.items():
            self.sections[sid]["prompt"] = _buf_text(buf)
        return h

    async def _save(self) -> None:
        b = self.backend
        host = self._collect()
        self.save_btn.set_sensitive(False)
        try:
            for sid in self.dirty_sections:
                if sid in self.sections:
                    await b.ai("sections/save", section=self.sections[sid])
            await b.ai("hosts/save", host=host)
        except Exception as err:  # noqa: BLE001
            self.save_btn.set_sensitive(True)
            self.dlg.add_toast(Adw.Toast(title=GLib.markup_escape_text(f"Couldn’t save: {err}"), timeout=5))
            return
        set_dj_icon(b, host["id"], self.icon_picker.icon)  # kept by Mass, not the server
        await b.refresh_ai()
        self.dlg.close()
        self._toast_parent(f"Saved {host['name']}")

    def _duplicate(self) -> None:
        h = self._collect()
        copy_ = copy.deepcopy(h)
        copy_["name"] = f"{h['name']} (copy)"
        copy_["_icon"] = self.icon_picker.icon
        self.dlg.close()
        DJEditor(self.backend, self.parent, template=copy_)

    def _confirm_delete(self) -> None:
        name = self.host.get("name", "this DJ")
        dlg = Adw.AlertDialog(heading=f"Delete {name}?",
                              body="Its shared segments stay on the server. A DJ that a station uses or "
                                   "that's on air can't be deleted.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "delete" and self.backend.run(self._delete()))
        dlg.present(self.dlg)

    async def _delete(self) -> None:
        try:
            await self.backend.ai("hosts/delete", host_id=self.host["id"])
        except Exception as err:  # noqa: BLE001
            self.dlg.add_toast(Adw.Toast(title=GLib.markup_escape_text(f"Couldn’t delete: {err}"), timeout=5))
            return
        await self.backend.refresh_ai()
        self.dlg.close()
        self._toast_parent(f"Deleted {self.host.get('name', 'DJ')}")


def new_dj_menu(backend, parent: Gtk.Widget) -> Gtk.MenuButton:
    """'New DJ' button whose popover offers a blank host or one of the server's presets."""
    btn = Gtk.MenuButton(label="New DJ", valign=Gtk.Align.CENTER)
    for c in ("m3", "tonal"):
        btn.add_css_class(c)
    pop = Gtk.Popover(has_arrow=False)
    btn.set_popover(pop)

    def fill(*_):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6)
        pop.set_child(box)

        def add(text, template):
            b = Gtk.Button(label=text)
            b.add_css_class("flat")
            b.get_child().set_xalign(0)
            b.connect("clicked", lambda *_: (pop.popdown(), DJEditor(backend, parent, template=template)))
            box.append(b)

        add("Blank DJ", None)

        async def presets():
            try:
                items = await backend.ai("hosts/presets/list") or []
            except Exception:  # noqa: BLE001
                items = []
            if items:
                box.append(Gtk.Separator(margin_top=4, margin_bottom=4))
                box.append(Gtk.Label(label="From a preset", xalign=0, margin_start=10,
                                     css_classes=["caption", "dim-label"]))
            for p in items:
                host = dict(p.get("host") or p)  # presets come as {"host": …, "sections": […]}
                host["sections"] = p.get("sections") or []
                add(host.get("name") or host.get("id") or "Preset", host)

        backend.run(presets())

    pop.connect("show", fill)
    return btn
