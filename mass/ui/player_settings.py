"""In-app Player settings and Player DSP, rendered from the server's config entries
(instead of sending the user to the Music Assistant web UI)."""

from __future__ import annotations

import logging
from gi.repository import Adw, GLib, Gtk
from music_assistant_models import dsp as M

log = logging.getLogger(__name__)

CATEGORY_TITLES = {
    "generic": "General",
    "player_controls": "Player controls",
    "announcements": "Announcements",
    "plugins": "Plugins",
    "audio": "Audio",
    "advanced": "Advanced",
}


def _category_title(cat: str) -> str:
    if cat in CATEGORY_TITLES:
        return CATEGORY_TITLES[cat]
    cat = cat.removeprefix("protocol_")
    return {"general": "Output protocol", "sendspin": "Sendspin", "airplay": "AirPlay",
            "dlna": "DLNA", "chromecast": "Chromecast"}.get(cat, cat.replace("_", " ").capitalize())


def _toast(widget: Gtk.Widget, text: str) -> None:
    if isinstance(widget, Adw.Dialog):
        widget.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))


def _shell(title: str, page: Adw.PreferencesPage, action: Gtk.Widget | None) -> Adw.Dialog:
    dlg = Adw.Dialog(title=title, content_width=560, content_height=680)
    tv = Adw.ToolbarView()
    hb = Adw.HeaderBar()
    if action:
        hb.pack_end(action)
    tv.add_top_bar(hb)
    tv.set_content(page)
    dlg.set_child(tv)
    return dlg


def _spin(title: str, value: float, lo: float, hi: float, step: float, digits: int, subtitle: str = ""):
    row = Adw.SpinRow.new_with_range(lo, hi, step)
    row.set_title(title)
    if subtitle:
        row.set_subtitle(subtitle)
    row.set_digits(digits)
    row.set_value(value if value is not None else 0)
    return row


# ====================================================================== player settings

class PlayerSettingsDialog:
    """Builds and presents an Adw.Dialog for one player's config entries."""

    HIDDEN_TYPES = {"divider", "icon", "image", "url", "pairing_code", "unknown"}

    def __init__(self, backend, player_id: str, parent: Gtk.Widget) -> None:
        self.backend, self.player_id, self.parent = backend, player_id, parent
        self.entries: dict[str, dict] = {}
        self.values: dict = {}
        self.changed: dict = {}
        self.rows: dict[str, Gtk.Widget] = {}
        self.show_advanced = False
        backend.run(self._load())

    async def _load(self) -> None:
        try:
            cfg = await self.backend.client.send_command("config/players/get", player_id=self.player_id)
        except Exception as err:  # noqa: BLE001
            if (win := self.parent.get_root()) and hasattr(win, "toast"):
                win.toast(f"Couldn’t load player settings: {err}")
            return
        self.cfg = cfg
        self.entries = cfg.get("values", {})
        self.values = {k: e.get("value") for k, e in self.entries.items()}
        self.values["__name"] = cfg.get("name") or ""
        self.values["__enabled"] = cfg.get("enabled", True)
        self.save_btn = Gtk.Button(label="Save", sensitive=False)
        self.save_btn.add_css_class("suggested-action")
        self.save_btn.connect("clicked", lambda *_: self.backend.run(self._save()))
        self.page = Adw.PreferencesPage()
        name = cfg.get("name") or cfg.get("default_name") or self.player_id
        self.dlg = _shell(name, self.page, self.save_btn)
        self._build()
        self.dlg.present(self.parent.get_root())

    # ------------------------------------------------------------------ build

    def _visible(self, e: dict) -> bool:
        if e.get("hidden") or e.get("type") in self.HIDDEN_TYPES:
            return False
        if e.get("advanced") and not self.show_advanced:
            return False
        dep = e.get("depends_on")
        if dep:
            cur = self.values.get(dep)
            if e.get("depends_on_value") is not None:
                return cur == e["depends_on_value"]
            if e.get("depends_on_value_not") is not None:
                return cur != e["depends_on_value_not"]
            return bool(cur) and cur != "none"
        return True

    def _build(self) -> None:
        # rebuild the page from scratch (cheap; entries are few)
        for grp in list(getattr(self, "_groups", [])):
            self.page.remove(grp)
        self._groups = []
        self.rows = {}

        top = Adw.PreferencesGroup()
        name = Adw.EntryRow(title="Name", text=self.values["__name"])
        name.set_show_apply_button(False)
        if self.cfg.get("default_name"):
            name.set_tooltip_text(f"Leave empty to use “{self.cfg['default_name']}”")
        name.connect("changed", lambda r: self._set("__name", r.get_text()))
        top.add(name)
        enabled = Adw.SwitchRow(title="Enabled", active=bool(self.values["__enabled"]))
        enabled.connect("notify::active", lambda r, _p: self._set("__enabled", r.get_active()))
        top.add(enabled)
        adv = Adw.SwitchRow(title="Show advanced settings", active=self.show_advanced)
        adv.connect("notify::active", self._toggle_advanced)
        top.add(adv)
        self._add_group(top)

        groups: dict[str, Adw.PreferencesGroup] = {}
        for key, e in self.entries.items():
            row = self._row(key, e)
            if row is None:
                continue
            cat = e.get("category") or "generic"
            if cat not in groups:
                groups[cat] = Adw.PreferencesGroup(title=_category_title(cat))
            groups[cat].add(row)
            self.rows[key] = row
            row.set_visible(self._visible(e))
        for grp in groups.values():
            self._add_group(grp)
        self._sync_group_visibility()

    def _add_group(self, grp: Adw.PreferencesGroup) -> None:
        self.page.add(grp)
        self._groups.append(grp)

    def _sync_group_visibility(self) -> None:
        for grp in self._groups[1:]:
            rows = [r for k, r in self.rows.items() if r.get_ancestor(Adw.PreferencesGroup) is grp]
            grp.set_visible(any(r.get_visible() for r in rows))

    def _toggle_advanced(self, row, _p) -> None:
        self.show_advanced = row.get_active()
        GLib.idle_add(lambda: (self._build(), False)[1])

    def _row(self, key: str, e: dict) -> Gtk.Widget | None:
        t = e.get("type")
        if t in self.HIDDEN_TYPES or e.get("multi_value"):
            return None
        title = e.get("label") or key.split("||")[-1].replace("_", " ").capitalize()
        desc = e.get("description") or ""
        val = self.values.get(key)
        ro = bool(e.get("read_only"))

        if t in ("label", "alert"):
            row = Adw.ActionRow(title=GLib.markup_escape_text(title), use_markup=True)
            row.set_title_lines(0)
            if t == "alert":
                row.add_css_class("warning")
            return row
        if t == "action":
            row = Adw.ActionRow(title=title, subtitle=desc)
            btn = Gtk.Button(label=e.get("action_label") or "Run", valign=Gtk.Align.CENTER)
            btn.connect("clicked", lambda *_: self.backend.run(self._action(e.get("action") or key)))
            row.add_suffix(btn)
            return row

        options = e.get("options") or []
        if options:
            titles = [o.get("title") or str(o.get("value")) for o in options]
            row = Adw.ComboRow(title=title, model=Gtk.StringList.new(titles))
            vals = [o.get("value") for o in options]
            row.set_selected(vals.index(val) if val in vals else 0)
            row.connect("notify::selected", lambda r, _p: self._set(key, vals[r.get_selected()]))
        elif t == "boolean":
            row = Adw.SwitchRow(title=title, active=bool(val))
            row.connect("notify::active", lambda r, _p: self._set(key, r.get_active()))
        elif t in ("integer", "float"):
            lo, hi = e.get("range") or (-1_000_000, 1_000_000)
            is_int = t == "integer"
            row = _spin(title, float(val or 0), lo, hi, 1 if is_int else 0.1, 0 if is_int else 2)
            row.connect("notify::value",
                        lambda r, _p: self._set(key, int(r.get_value()) if is_int else r.get_value()))
        elif t == "secure_string":
            row = Adw.PasswordEntryRow(title=title, text=str(val or ""))
            row.connect("changed", lambda r: self._set(key, r.get_text()))
        else:  # string, splitted_string, folder
            row = Adw.EntryRow(title=title, text=str(val if val is not None else ""))
            row.connect("changed", lambda r: self._set(key, r.get_text() or None))
        if desc and hasattr(row, "set_subtitle") and not isinstance(row, Adw.EntryRow):
            row.set_subtitle(desc)
        elif desc:
            row.set_tooltip_text(desc)
        row.set_sensitive(not ro)
        return row

    # ------------------------------------------------------------------ state

    def _set(self, key: str, value) -> None:
        self.values[key] = value
        self.changed[key] = value
        self.save_btn.set_sensitive(True)
        # re-evaluate depends_on visibility
        for k, row in self.rows.items():
            row.set_visible(self._visible(self.entries[k]))
        self._sync_group_visibility()

    async def _action(self, action: str) -> None:
        try:
            await self.backend.client.config.invoke_player_config_action(self.player_id, action)
        except Exception as err:  # noqa: BLE001
            _toast(self.dlg, f"Action failed: {err}")

    async def _save(self) -> None:
        values = {k: v for k, v in self.changed.items() if not k.startswith("__")}
        if "__name" in self.changed:
            values["name"] = self.changed["__name"] or None
        if "__enabled" in self.changed:
            values["enabled"] = self.changed["__enabled"]
        self.save_btn.set_sensitive(False)
        try:
            await self.backend.client.send_command("config/players/save", player_id=self.player_id,
                                                   values=values)
        except Exception as err:  # noqa: BLE001
            self.save_btn.set_sensitive(True)
            _toast(self.dlg, f"Couldn’t save: {err}")
            return
        self.changed.clear()
        self.dlg.close()


# ====================================================================== DSP

FILTER_NAMES = {
    "parametric_eq": "Parametric EQ",
    "tone_control": "Tone control",
    "gain": "Gain",
    "balance": "Balance",
    "high_low_pass": "High/low-pass",
    "stereo_width": "Stereo width",
    "crossfeed": "Crossfeed",
    "transpose": "Transpose",
    "safety_limiter": "Safety limiter",
    "compressor": "Compressor",
    "convolution": "Convolution",
}
FILTER_CLASSES = {
    "parametric_eq": M.ParametricEQFilter, "tone_control": M.ToneControlFilter, "gain": M.GainFilter,
    "balance": M.BalanceFilter, "high_low_pass": M.HighLowPassFilter, "stereo_width": M.StereoWidthFilter,
    "crossfeed": M.CrossfeedFilter, "transpose": M.TransposeFilter, "safety_limiter": M.SafetyLimiterFilter,
    "compressor": M.CompressorFilter, "convolution": M.ConvolutionFilter,
}
# field -> (title, lo, hi, step, digits, unit)
PARAMS = {
    "gain": ("Gain", -60, 60, 0.5, 1, "dB"),
    "preamp": ("Preamp", -60, 60, 0.5, 1, "dB"),
    "bass_level": ("Bass", -60, 60, 0.5, 1, "dB"),
    "mid_level": ("Mid", -60, 60, 0.5, 1, "dB"),
    "treble_level": ("Treble", -60, 60, 0.5, 1, "dB"),
    "balance": ("Balance", -100, 100, 1, 0, "L ← → R"),
    "frequency": ("Frequency", 20, 20000, 10, 0, "Hz"),
    "q": ("Q", 0.01, 100, 0.05, 2, ""),
    "width": ("Width", 0, 2, 0.05, 2, "0 = mono, 1 = unchanged"),
    "strength": ("Strength", 0, 1, 0.05, 2, ""),
    "soundstage": ("Soundstage", 0, 1, 0.05, 2, ""),
    "semitones": ("Semitones", -12, 12, 0.1, 2, ""),
    "ceiling": ("Ceiling", -60, 0, 0.5, 1, "dB"),
    "threshold": ("Threshold", -60, 0, 0.5, 1, "dB"),
    "ratio": ("Ratio", 1, 20, 0.1, 1, ": 1"),
    "attack": ("Attack", 0.01, 2000, 1, 2, "ms"),
    "release": ("Release", 0.01, 9000, 10, 2, "ms"),
    "knee": ("Knee", 0, 18, 0.5, 1, "dB"),
    "makeup": ("Make-up gain", 0, 36, 0.5, 1, "dB"),
}
ENUM_FIELDS = {
    "mode": [("high_pass", "High-pass"), ("low_pass", "Low-pass")],
    "slope": [(6, "6 dB/oct"), (12, "12 dB/oct"), (18, "18 dB/oct"), (24, "24 dB/oct"), (36, "36 dB/oct"),
              (48, "48 dB/oct")],
    "type": [(t.value, t.value.replace("_", " ").capitalize()) for t in M.ParametricEQBandType],
    "channel": [(c.value, c.value.replace("_", " ").capitalize()) for c in M.AudioChannel],
}


class DSPDialog:
    """Edit a player's DSP chain: master switch, presets, input/output gain, ordered filters."""

    def __init__(self, backend, player_id: str, parent: Gtk.Widget) -> None:
        self.backend, self.player_id, self.parent = backend, player_id, parent
        self.dsp: dict = {}
        self.presets: list = []
        self.irs: list[dict] = []
        self._save_src = 0
        backend.run(self._load())

    @property
    def cfg(self):
        return self.backend.client.config

    async def _load(self) -> None:
        try:
            self.dsp = await self.backend.client.send_command("config/players/dsp/get",
                                                              player_id=self.player_id)
        except Exception as err:  # noqa: BLE001
            if (win := self.parent.get_root()) and hasattr(win, "toast"):
                win.toast(f"Couldn’t load DSP settings: {err}")
            return
        for coro, attr in ((self.cfg.get_dsp_preset(), "presets"), (self.cfg.get_dsp_irs(), "irs")):
            try:
                setattr(self, attr, await coro)
            except Exception:  # noqa: BLE001  (older servers: no presets / IRs)
                pass
        p = self.backend.get_player(self.player_id)
        self.page = Adw.PreferencesPage()
        self.status = Gtk.Label(css_classes=["dim-label", "caption"])
        self.dlg = _shell(f"DSP · {p.name if p else self.player_id}", self.page, self.status)
        self._build()
        self.dlg.present(self.parent.get_root())

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        for grp in list(getattr(self, "_groups", [])):
            self.page.remove(grp)
        self._groups = []
        d = self.dsp

        main = Adw.PreferencesGroup(description="Processing runs on the server, before audio is sent to "
                                                "the player. It turns bit-perfect playback off.")
        en = Adw.SwitchRow(title="Enable DSP", active=bool(d.get("enabled")))
        en.connect("notify::active", lambda r, _p: self._edit(lambda: d.__setitem__("enabled", r.get_active()),
                                                              rebuild=True))
        main.add(en)
        if self.presets:
            names = ["Custom"] + [pr.name for pr in self.presets]
            ids = [None] + [pr.preset_id for pr in self.presets]
            pr = Adw.ComboRow(title="Preset", model=Gtk.StringList.new(names))
            pr.set_selected(ids.index(d.get("preset_id")) if d.get("preset_id") in ids else 0)
            pr.connect("notify::selected", lambda r, _p: ids[r.get_selected()] and self.backend.run(
                self._apply_preset(ids[r.get_selected()])))
            main.add(pr)
        self._add(main)

        gains = Adw.PreferencesGroup(title="Gain")
        for key, title in (("input_gain", "Input gain"), ("output_gain", "Output gain")):
            row = _spin(title, d.get(key, 0.0), -60, 60, 0.5, 1, "dB")
            row.connect("notify::value", lambda r, _p, k=key: self._edit(lambda: d.__setitem__(k, r.get_value())))
            gains.add(row)
        gains.set_sensitive(bool(d.get("enabled")))
        self._add(gains)

        add = Gtk.MenuButton(label="Add filter", valign=Gtk.Align.CENTER)
        add.add_css_class("flat")
        pop = Gtk.Popover()
        lst = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for ftype, fname in FILTER_NAMES.items():
            if ftype == "convolution" and not self.irs:
                continue
            b = Gtk.Button(label=fname)
            b.add_css_class("flat")
            b.get_child().set_xalign(0)
            b.connect("clicked", lambda _b, t=ftype: (pop.popdown(), self._add_filter(t)))
            lst.append(b)
        pop.set_child(lst)
        add.set_popover(pop)
        filters = Adw.PreferencesGroup(title="Filters", description="Applied top to bottom",
                                       header_suffix=add)
        filters.set_sensitive(bool(d.get("enabled")))
        if not d.get("filters"):
            filters.add(Adw.ActionRow(title="No filters", css_classes=["dim-label"]))
        for i, f in enumerate(d.get("filters", [])):
            filters.add(self._filter_row(i, f))
        self._add(filters)

    def _add(self, grp) -> None:
        self.page.add(grp)
        self._groups.append(grp)

    def _filter_row(self, idx: int, f: dict) -> Adw.ExpanderRow:
        ftype = f.get("type", "")
        row = Adw.ExpanderRow(title=FILTER_NAMES.get(ftype, ftype), subtitle=self._summary(f))
        sw = Gtk.Switch(active=f.get("enabled", True), valign=Gtk.Align.CENTER, tooltip_text="Enabled")
        sw.connect("notify::active", lambda s, _p: self._edit(lambda: f.__setitem__("enabled", s.get_active())))
        n = len(self.dsp["filters"])
        for icon, tip, delta in (("go-up-symbolic", "Move up", -1), ("go-down-symbolic", "Move down", 1)):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER,
                           sensitive=0 <= idx + delta < n)
            b.add_css_class("flat")
            b.connect("clicked", lambda *_, dl=delta: self._move(f, dl))
            row.add_suffix(b)
        rm = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Remove", valign=Gtk.Align.CENTER)
        rm.add_css_class("flat")
        rm.connect("clicked", lambda *_: self._remove(self.dsp["filters"], f))
        row.add_suffix(rm)
        row.add_suffix(sw)

        for key, val in f.items():
            if key in ("enabled", "type", "bands", "per_channel_preamp"):
                continue
            r = self._param_row(f, key, val, lambda: row.set_subtitle(self._summary(f)))
            if r:
                row.add_row(r)
        if ftype == "parametric_eq":
            for bi, band in enumerate(f.get("bands", [])):
                row.add_row(self._band_row(f, bi, band))
            ab = Adw.ButtonRow(title="Add band", start_icon_name="list-add-symbolic")
            ab.connect("activated", lambda *_: self._edit(lambda: f.setdefault("bands", []).append(
                M.ParametricEQBand().to_dict()), rebuild=True))
            row.add_row(ab)
        return row

    def _band_row(self, f: dict, bi: int, band: dict) -> Adw.ExpanderRow:
        def summary():
            return f"{band.get('frequency', 0):g} Hz · {band.get('gain', 0):+g} dB · Q {band.get('q', 1):g}"

        br = Adw.ExpanderRow(title=f"Band {bi + 1}", subtitle=summary())
        sw = Gtk.Switch(active=band.get("enabled", True), valign=Gtk.Align.CENTER)
        sw.connect("notify::active", lambda s, _p: self._edit(lambda: band.__setitem__("enabled", s.get_active())))
        rm = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Remove band", valign=Gtk.Align.CENTER)
        rm.add_css_class("flat")
        rm.connect("clicked", lambda *_: self._remove(f["bands"], band))
        br.add_suffix(rm)
        br.add_suffix(sw)
        for key, val in band.items():
            if key == "enabled":
                continue
            r = self._param_row(band, key, val, lambda: br.set_subtitle(summary()))
            if r:
                br.add_row(r)
        return br

    def _param_row(self, obj: dict, key: str, val, on_change) -> Gtk.Widget | None:
        def setv(v):
            obj[key] = v
            on_change()
            self._edit(None)

        if key == "ir_id":
            ids = [ir.get("id") or ir.get("ir_id") for ir in self.irs]
            row = Adw.ComboRow(title="Impulse response",
                               model=Gtk.StringList.new([ir.get("name", "?") for ir in self.irs]))
            row.set_selected(ids.index(val) if val in ids else 0)
            row.connect("notify::selected", lambda r, _p: setv(ids[r.get_selected()]))
            return row
        if key in ENUM_FIELDS:
            opts = ENUM_FIELDS[key]
            vals = [o[0] for o in opts]
            row = Adw.ComboRow(title=key.capitalize(), model=Gtk.StringList.new([o[1] for o in opts]))
            row.set_selected(vals.index(val) if val in vals else 0)
            row.connect("notify::selected", lambda r, _p: setv(vals[r.get_selected()]))
            return row
        if key in PARAMS and isinstance(val, (int, float)):
            title, lo, hi, step, digits, unit = PARAMS[key]
            row = _spin(title, float(val), lo, hi, step, digits, unit)
            row.connect("notify::value", lambda r, _p: setv(r.get_value()))
            return row
        return None

    @staticmethod
    def _summary(f: dict) -> str:
        t = f.get("type")
        if t == "parametric_eq":
            n = len(f.get("bands", []))
            return f"{n} band{'s' * (n != 1)}"
        bits = []
        for key, val in f.items():
            if key in PARAMS and isinstance(val, (int, float)):
                title, *_r, unit = PARAMS[key]
                bits.append(f"{title} {val:g}{' ' + unit if unit in ('dB', 'Hz', 'ms') else ''}")
            elif key == "mode":
                bits.insert(0, str(val).replace("_", "-").capitalize())
        return " · ".join(bits[:3])

    # ------------------------------------------------------------------ edits

    def _add_filter(self, ftype: str) -> None:
        cls = FILTER_CLASSES[ftype]
        kw = {"enabled": True}
        if ftype == "convolution" and self.irs:
            kw["ir_id"] = self.irs[0].get("id") or self.irs[0].get("ir_id") or ""
        flt = cls(**kw)
        if ftype == "parametric_eq":
            flt.bands = [M.ParametricEQBand(frequency=fq) for fq in (100.0, 1000.0, 8000.0)]
        self._edit(lambda: self.dsp.setdefault("filters", []).append(flt.to_dict()), rebuild=True)

    @staticmethod
    def _index_of(items: list[dict], item: dict) -> int | None:
        return next((i for i, x in enumerate(items) if x is item), None)

    def _move(self, f: dict, delta: int) -> None:
        fl = self.dsp["filters"]
        i = self._index_of(fl, f)
        if i is not None and 0 <= i + delta < len(fl):
            self._edit(lambda: fl.insert(i + delta, fl.pop(i)), rebuild=True)

    def _remove(self, items: list[dict], item: dict) -> None:
        if (i := self._index_of(items, item)) is not None:
            self._edit(lambda: items.pop(i), rebuild=True)

    def _edit(self, fn, rebuild: bool = False) -> None:
        if fn:
            fn()
        self.dsp["preset_id"] = None
        if rebuild:
            GLib.idle_add(lambda: (self._build(), False)[1])
        # coalesce rapid spin-button changes into one save
        if self._save_src:
            GLib.source_remove(self._save_src)
        self._save_src = GLib.timeout_add(400, self._flush)
        self.status.set_label("Saving…")

    def _flush(self) -> bool:
        self._save_src = 0
        self.backend.run(self._save())
        return False

    async def _save(self) -> None:
        try:
            cfg = M.DSPConfig.from_dict(self.dsp)
            cfg.validate()
            await self.cfg.save_player_dsp_config(self.player_id, cfg)
            self.status.set_label("Saved")
        except Exception as err:  # noqa: BLE001
            log.warning("DSP save failed: %s", err)
            self.status.set_label("Not saved")
            _toast(self.dlg, f"Couldn’t save DSP: {err}")

    async def _apply_preset(self, preset_id: str) -> None:
        try:
            cfg = await self.cfg.apply_dsp_preset(self.player_id, preset_id)
        except Exception as err:  # noqa: BLE001
            _toast(self.dlg, f"Couldn’t apply preset: {err}")
            return
        self.dsp = cfg.to_dict()
        self._build()

