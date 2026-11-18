"""Frame Mass screenshots on Material 3 Expressive shape backgrounds (SVG -> PNG via rsvg-convert)."""
import base64, math, os, subprocess, sys

RAW, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)

# DMS / matugen dark palette the screenshots were taken with
P = dict(primary="#f4bd6e", on_primary="#432a00", primary_container="#714a01", secondary="#ddc2a1",
         secondary_container="#624f34", tertiary="#b8cea1", tertiary_container="#455734",
         surface="#18120b", surface_container="#262019", surface_container_high="#312b23")


def polar(cx, cy, r, fn, n=360, rot=0.0):
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        rr = r * fn(t)
        pts.append((cx + rr * math.cos(t + rot), cy + rr * math.sin(t + rot)))
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + " Z"


# M3 Expressive shape library, approximated as polar curves
SHAPES = {
    "cookie9": lambda t: 1 + 0.07 * math.cos(9 * t),
    "cookie12": lambda t: 1 + 0.045 * math.cos(12 * t),
    "clover4": lambda t: 0.82 + 0.18 * abs(math.cos(2 * t)) ** 0.6,
    "flower8": lambda t: 0.86 + 0.14 * math.cos(8 * t) ** 2,
    "sunny": lambda t: 1 + 0.035 * math.cos(16 * t),
    "soft_burst": lambda t: 0.9 + 0.1 * math.cos(10 * t),
    "circle": lambda t: 1.0,
}


def pill(x, y, w, h, rot):
    r = min(w, h) / 2
    return (f'<rect x="{x - w/2:.0f}" y="{y - h/2:.0f}" width="{w:.0f}" height="{h:.0f}" rx="{r:.0f}" '
            f'transform="rotate({rot} {x} {y})"/>')


def shape(kind, cx, cy, r, fill, rot=0.0, opacity=1.0):
    if kind == "pill":
        return f'<g fill="{fill}" opacity="{opacity}">{pill(cx, cy, r * 2.4, r, rot)}</g>'
    return f'<path d="{polar(cx, cy, r, SHAPES[kind], rot=math.radians(rot))}" fill="{fill}" opacity="{opacity}"/>'


THEMES = {
    "amber": dict(bg=P["primary_container"], a=P["primary"], b="#8a5d10", c=P["tertiary_container"], d=P["secondary_container"]),
    "olive": dict(bg=P["tertiary_container"], a=P["tertiary"], b="#5a6f45", c=P["primary_container"], d="#33402a"),
    "sand": dict(bg=P["secondary_container"], a=P["secondary"], b="#7a6446", c=P["primary_container"], d=P["tertiary_container"]),
    "gold": dict(bg=P["primary"], a="#ffddb0", b="#e0a64f", c=P["tertiary"], d=P["primary_container"]),
}

# shape layouts as fractions of the canvas: (kind, x, y, r(of height), color key, rot, opacity)
LAYOUTS = [
    [("cookie9", 0.06, 0.12, 0.42, "a", 0, .9), ("clover4", 0.95, 0.88, 0.48, "c", 20, 1),
     ("pill", 0.88, 0.08, 0.16, "b", -30, 1), ("flower8", 0.12, 0.96, 0.22, "d", 10, 1),
     ("circle", 0.97, 0.36, 0.06, "a", 0, 1)],
    [("soft_burst", 0.96, 0.1, 0.44, "a", 10, .85), ("cookie12", 0.03, 0.85, 0.46, "c", 0, 1),
     ("pill", 0.1, 0.1, 0.15, "b", 35, 1), ("clover4", 0.82, 0.98, 0.2, "d", 45, 1),
     ("circle", 0.02, 0.4, 0.07, "a", 0, 1)],
    [("flower8", 0.04, 0.2, 0.4, "c", 0, 1), ("cookie9", 0.98, 0.78, 0.5, "a", 15, .9),
     ("pill", 0.9, 0.12, 0.14, "d", 25, 1), ("sunny", 0.16, 0.98, 0.24, "b", 0, 1),
     ("circle", 0.6, 0.97, 0.05, "a", 0, 1)],
    [("clover4", 0.96, 0.16, 0.42, "c", 10, 1), ("soft_burst", 0.05, 0.9, 0.44, "a", 0, .85),
     ("pill", 0.08, 0.14, 0.15, "b", -20, 1), ("cookie12", 0.84, 0.96, 0.18, "d", 0, 1),
     ("circle", 0.4, 0.04, 0.05, "a", 0, 1)],
]


def frame(name, theme, layout, out, shots, W=2400, H=1500, pad=150, radius=34):
    th = THEMES[theme]
    parts = [f'<rect width="{W}" height="{H}" fill="{th["bg"]}"/>']
    for kind, x, y, r, col, rot, op in LAYOUTS[layout]:
        parts.append(shape(kind, x * W, y * H, r * H, th[col], rot, op))
    defs = ['<filter id="sh" x="-20%" y="-20%" width="140%" height="140%">'
            '<feGaussianBlur in="SourceAlpha" stdDeviation="28"/><feOffset dy="24"/>'
            '<feComponentTransfer><feFuncA type="linear" slope="0.55"/></feComponentTransfer>'
            '<feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>']
    for i, (img, x, y, w, h) in enumerate(shots):
        defs.append(f'<clipPath id="c{i}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}"/></clipPath>')
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{P["surface"]}" filter="url(#sh)"/>')
        parts.append(f'<image href="data:image/png;base64,{base64.b64encode(open(img,"rb").read()).decode()}" x="{x}" y="{y}" width="{w}" height="{h}" '
                     f'preserveAspectRatio="xMidYMid slice" clip-path="url(#c{i})"/>')
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="none" '
                     f'stroke="#ffffff" stroke-opacity="0.10" stroke-width="2"/>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
           f'<defs>{"".join(defs)}</defs>{"".join(parts)}</svg>')
    svgp = os.path.join(OUT, name + ".svg")
    open(svgp, "w").write(svg)
    subprocess.run(["rsvg-convert", "-w", str(out), "-o", os.path.join(OUT, name + ".png"), svgp], check=True)
    os.remove(svgp)


def single(name, src, theme, layout, out=1600):
    W, H, pad = 2400, 1500, 150
    sw = W - 2 * pad - 60
    sh = round(sw * 900 / 1400)
    frame(name, theme, layout, out, [(os.path.join(RAW, src + ".png"), (W - sw) // 2, (H - sh) // 2, sw, sh)], W, H)


def hero():
    """Two screens fanned over each other."""
    W, H = 2400, 1400
    sw, sh = 1500, round(1500 * 900 / 1400)
    frame("hero", "amber", 0, 2000, [
        (os.path.join(RAW, "home.png"), 130, 110, sw, sh),
        (os.path.join(RAW, "np-queue.png"), W - sw - 130, H - sh - 110, sw, sh),
    ], W, H)


hero()
for name, src, theme, layout in [
    ("now-playing", "np-lyrics", "olive", 1),
    ("home", "home", "sand", 2),
    ("library", "library", "amber", 3),
    ("artist", "artist", "olive", 0),
    ("album", "album", "gold", 1),
    ("ai-dj", "np-dj", "amber", 2),
    ("ai-radio", "airadio", "sand", 3),
    ("players", "players", "gold", 0),
    ("queue", "np-queue", "olive", 2),
]:
    single(name, src, theme, layout)
print("ok")
