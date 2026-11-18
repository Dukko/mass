"""Signal path popover for the quality badge: input → processing → output, like the MA web UI.

Built from `streamdetails.audio_processing` (the server's effective processing chain).
"""

from __future__ import annotations

from gi.repository import Gtk

from .common import label
from .expressive import Sym

QUALITY = {"hi_res": "Hi-res", "lossless": "Lossless", "standard": "Standard", "low": "Low quality"}
CHANNELS = {1: "Mono", 2: "Stereo"}
NORMALIZATION = {
    "dynamic": "Dynamic (EBU R128)",
    "fixed_gain": "Fixed gain",
    "fallback_fixed_gain": "Fixed gain (fallback)",
    "fallback_dynamic": "Dynamic (fallback)",
    "measurement_only": "Measuring only",
}
CROSSFADE = {"smart_crossfade": "Smart crossfade", "standard_crossfade": "Crossfade",
             "voice_over": "Voice-over", "source": "Crossfaded by the source"}


def _val(x) -> str:
    return str(getattr(x, "value", x) or "")


def _codec(fmt) -> str:
    return _val(fmt.content_type).upper()


def _fmt_line(fmt) -> str:
    parts = []
    if fmt.sample_rate:
        parts.append(f"{fmt.sample_rate / 1000:g} kHz")
    if fmt.bit_depth:
        parts.append(f"{fmt.bit_depth}-bit")
    if fmt.channels:
        parts.append(CHANNELS.get(fmt.channels, f"{fmt.channels} ch"))
    if fmt.bit_rate:
        parts.append(f"{fmt.bit_rate} kbps")
    return " · ".join(parts)


def _pcm_line(fmt) -> str:
    ct = _val(fmt.content_type)
    kind = "float" if ct.startswith("f") else "integer"
    return f"{fmt.bit_depth}-bit {kind} PCM"


def _db(v: float) -> str:
    return f"{v:+.1f} dB"


def provider_name(backend, prov: str) -> str:
    if not prov:
        return ""
    client = backend.client
    inst = client.get_provider(prov, return_unavailable=True) if client else None
    if inst and getattr(inst, "name", None):
        return inst.name
    return prov.split("--")[0].replace("_", " ").title()


class SignalPath(Gtk.Box):
    """A vertical timeline of stages, each a dot + heading with rows hanging off the rail."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("signal-path")

    def _clear(self) -> None:
        while (c := self.get_first_child()) is not None:
            self.remove(c)

    def _stage(self, title: str, quality: str | None, lit: bool) -> None:
        head = Gtk.Box(spacing=10)
        head.add_css_class("sp-stage")
        dot = Gtk.Box(valign=Gtk.Align.CENTER)
        dot.add_css_class("sp-dot")
        if lit:
            dot.add_css_class("lit")
        head.append(dot)
        head.append(label(title, "sp-title", ellipsize=False))
        if quality:
            head.append(label(quality, "sp-quality", ellipsize=False))
        self.append(head)

    def _row(self, symbol: str, title: str, subtitle: str = "", chip: str = "", tip: str = "") -> None:
        row = Gtk.Box(spacing=12)
        row.add_css_class("sp-row")
        row.append(Sym(symbol, 20))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        top = Gtk.Box(spacing=8)
        top.append(label(title, "sp-row-title", ellipsize=False))
        if chip:
            c = label(chip, "sp-chip", ellipsize=False, valign=Gtk.Align.CENTER)
            top.append(c)
        text.append(top)
        if subtitle:
            text.append(label(subtitle, "sp-row-sub", ellipsize=False, wrap=True))
        row.append(text)
        if tip:
            info = Sym("info", 18)
            info.add_css_class("sp-info")
            info.set_valign(Gtk.Align.CENTER)
            info.set_tooltip_text(tip)
            row.append(info)
        self.append(row)

    def update(self, backend) -> bool:
        """Rebuild from the active queue's current item; False when there's nothing to show."""
        self._clear()
        q = backend.active_queue
        sd = getattr(q.current_item, "streamdetails", None) if q and q.current_item else None
        fmt = getattr(sd, "audio_format", None)
        if not sd or not fmt:
            self.append(label("No stream details yet", "sp-row-sub", ellipsize=False))
            return False
        chain = getattr(sd, "audio_processing", None)

        # ------------------------------------------------ input
        inq = QUALITY.get(_val(chain.input_fidelity.quality)) if chain else None
        self._stage("INPUT", inq, True)
        mi = q.current_item.media_item
        prov = provider_name(backend, sd.provider or getattr(mi, "provider", ""))
        if prov:
            self._row("music_note", prov)
        self._row("description", _codec(fmt), _fmt_line(fmt),
                  tip="The format Music Assistant receives from the provider")

        # ------------------------------------------------ processing
        qp = chain.queue_processing if chain else None
        self._stage("PROCESSING", None, False)
        if qp and qp.pcm_format:
            self._row("speed", "Processing headroom", _pcm_line(qp.pcm_format),
                      tip="Internal format used while mixing, normalizing and applying DSP")
        norm = qp.normalization if qp else None
        mode = _val(norm.mode) if norm else _val(getattr(sd, "volume_normalization_mode", ""))
        if mode == "source":
            self._row("graphic_eq", "Volume normalization", f"Applied by {prov or 'the source'}")
        elif mode and mode not in ("disabled", "unknown"):
            bits = [NORMALIZATION.get(mode, mode.replace("_", " ").capitalize())]
            if norm and norm.applied_gain_db is not None:
                bits.append(f"{_db(norm.applied_gain_db)} applied")
            if norm and norm.target_lufs is not None:
                bits.append(f"target {norm.target_lufs:g} LUFS")
            if norm and norm.measured_lufs is not None:
                bits.append(f"measured {norm.measured_lufs:g} LUFS")
            self._row("graphic_eq", "Volume normalization", " · ".join(bits))
        if qp and _val(qp.crossfade_mode) in CROSSFADE:
            self._row("join", CROSSFADE[_val(qp.crossfade_mode)])
        if qp and qp.playback_speed and qp.playback_speed != 1.0:
            self._row("slow_motion_video", "Playback speed", f"{qp.playback_speed:g}×")
        if qp and qp.overlay_active:
            self._row("layers", "Audio overlay", "Mixed over the music")

        # ------------------------------------------------ output(s)
        for out in (chain.outputs if chain else []):
            dsp = out.dsp
            if _val(dsp.state) == "enabled":
                bits = []
                if dsp.input_gain:
                    bits.append(f"input {_db(dsp.input_gain)}")
                active = [f for f in dsp.filters if getattr(f, "enabled", True)]
                if active:
                    names = ", ".join(_val(f.type).replace("_", " ") for f in active)
                    bits.append(f"{len(active)} filter{'s' * (len(active) != 1)} ({names})")
                if dsp.output_gain:
                    bits.append(f"output {_db(dsp.output_gain)}")
                self._row("tune", "DSP", " · ".join(bits) or "Enabled, no filters")
            elif _val(dsp.state) == "disabled_by_unsupported_group":
                self._row("tune", "DSP", "Off — not supported while grouped")

        outs = chain.outputs if chain else []
        if not outs:
            return True
        for out in outs:
            fid = out.fidelity
            self._stage("OUTPUT", QUALITY.get(_val(fid.quality)), True)
            if out.output_format:
                self._row("description", _codec(out.output_format), _fmt_line(out.output_format),
                          chip="Bit-perfect" if fid.bit_perfect else "",
                          tip="Bit-perfect: nothing altered the samples between input and this output"
                          if fid.bit_perfect else "")
            names = []
            for pid in out.player_ids:
                p = backend.get_player(pid)
                names.append(p.name if p else pid)
            if names:
                self._row("speaker", ", ".join(names),
                          f"{_val(out.source_channel).capitalize()} channel" if out.source_channel else "")
        return True


class SignalPathPopover(Gtk.Popover):
    def __init__(self, backend) -> None:
        super().__init__(has_arrow=False)
        self.backend = backend
        self.add_css_class("signal-path-popover")
        self.path = SignalPath()
        self.set_child(self.path)
        self.connect("show", lambda *_: self.path.update(backend))
