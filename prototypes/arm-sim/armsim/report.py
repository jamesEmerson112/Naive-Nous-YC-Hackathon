"""Charts of the Nov 8 generate-and-test runs on the real arm, for the README, from the local data folder.

    python -m armsim.report [--data ../../data/hackathon-2026-11-08] [--out media/gentest]

The raw data (the try log, camera frames, Astra's prompts) stays local and out of git; this writes only SVG charts,
each in a light and a dark version (<name>-light.svg, <name>-dark.svg) for a <picture> in the README. No plotting
library: plain SVG, so it runs anywhere the arm code runs. The jaw-height chart needs MuJoCo (the arm twin); without
it that one chart is skipped.

Design (the dataviz method): the reference palette's first three categorical slots for the three recordings
(validated all-pairs, light and dark), thin marks with a 2 px surface ring, hairline grid, system sans, a legend for
two or more series plus selective direct labels, text in ink colours only.
"""
import argparse
import json
import math
import statistics as st
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]                 # prototypes/arm-sim
DATA = ROOT.parents[1] / "data" / "hackathon-2026-11-08"    # <repo>/data/...: local only, git-ignored
OUT = ROOT / "media" / "gentest"

RECS = ("recording_2", "wave", "recording_3")              # slot order: the one that grips first
THEMES = {
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781", "grid": "#e1e0d9",
              "axis": "#c3c2b7", "rec": {"recording_2": "#2a78d6", "wave": "#eb6834", "recording_3": "#1baf7a"},
              "seg": ("#6250d6", "#e87ba4"), "on": "#ffffff"},
    "dark": {"surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781", "grid": "#2c2c2a",
             "axis": "#383835", "rec": {"recording_2": "#3987e5", "wave": "#d95926", "recording_3": "#199e70"},
             "seg": ("#9085e9", "#d55181"), "on": "#ffffff"},
}
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def tw(s, size):
    """Approximate rendered width of sans text (enough to place labels without collisions)."""
    return len(str(s)) * size * 0.55


def n(v):
    return f"{v:.1f}".rstrip("0").rstrip(".")


class Svg:
    def __init__(self, w, h, t, title, desc):
        self.w, self.h, self.t, self.title, self.desc, self.parts = w, h, t, title, desc, []

    def add(self, s):
        self.parts.append(s)

    def text(self, x, y, s, size=12, color="ink", anchor="start", weight=400, italic=False):
        fill = self.t.get(color, color)
        style = ' font-style="italic"' if italic else ""
        self.add(f'<text x="{n(x)}" y="{n(y)}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
                 f'font-weight="{weight}"{style}>{esc(s)}</text>')

    def line(self, x1, y1, x2, y2, color="grid", width=1, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<line x1="{n(x1)}" y1="{n(y1)}" x2="{n(x2)}" y2="{n(y2)}" stroke="{self.t.get(color, color)}" '
                 f'stroke-width="{width}"{d}/>')

    def dot(self, x, y, color, r=4.5, hollow=False):
        if hollow:
            self.add(f'<circle cx="{n(x)}" cy="{n(y)}" r="{r}" fill="{self.t["surface"]}" stroke="{color}" stroke-width="2"/>')
        else:
            self.add(f'<circle cx="{n(x)}" cy="{n(y)}" r="{r}" fill="{color}" stroke="{self.t["surface"]}" stroke-width="2"/>')

    def header(self, title, sub_lines):
        self.text(24, 34, title, size=17, weight=600)
        for i, s in enumerate(sub_lines):
            self.text(24, 56 + i * 17, s, size=12.5, color="ink2")

    def render(self):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" width="{self.w}" '
                f'height="{self.h}" role="img" aria-labelledby="t d"><title id="t">{esc(self.title)}</title>'
                f'<desc id="d">{esc(self.desc)}</desc><style>text{{font-family:{FONT}}}</style>'
                f'<rect width="100%" height="100%" rx="12" fill="{self.t["surface"]}"/>' + "".join(self.parts) + "</svg>")


def lin(d0, d1, r0, r1):
    return lambda v: r0 + (v - d0) * (r1 - r0) / (d1 - d0)


def logscale(d0, d1, r0, r1):
    a, b = math.log10(d0), math.log10(d1)
    return lambda v: r0 + (math.log10(max(v, d0)) - a) * (r1 - r0) / (b - a)


def legend(svg, x, y, items):
    """items: (label, color, hollow). Swatch dots carry the colour; the label stays in ink."""
    for label, color, hollow in items:
        svg.dot(x + 5, y - 4, color, r=4.5, hollow=hollow)
        svg.text(x + 15, y, label, size=12, color="ink2")
        x += 15 + tw(label, 12) + 18
    return x


# ---- data ----------------------------------------------------------------------------------------------------
def load(data):
    g = data / "arm-runs" / "gentest"
    rows = [json.loads(l) for l in (g / "hermes-trials.jsonl").read_text().splitlines() if l.strip()]
    fixed = [{**json.loads(l), "fixed": True} for l in (g / "trials.jsonl").read_text().splitlines() if l.strip()]
    tries = sorted([r for r in rows if not r.get("success")] + fixed, key=lambda r: r["ts"])
    return tries


def hours(ts):
    t = time.localtime(ts)
    return t.tm_hour + t.tm_min / 60 + t.tm_sec / 3600


def clock(h):
    return f"{int(h)}:{int(round((h % 1) * 60)):02d}"


def experiments(data):
    def rows(p):
        f = data / "experiments" / p / "snap" / "results.jsonl"
        return [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []
    return {"sol": rows("sol-vs-astra"), "effort": rows("reasoning-effort"), "lean": rows("full-vs-lean"),
            "lean12": rows("lean-12-tries")}


def milestones(tries):
    first = lambda pred: next((hours(r["ts"]) for r in tries if pred(r)), None)
    marks = [("Astra plans each test", first(lambda r: not r.get("fixed"))),
             ("grip squeezes to 99%", first(lambda r: r.get("squeeze_pct"))),
             ("probe before the grab", first(lambda r: r.get("probe"))),
             ("labelled grid", first(lambda r: r.get("jaws_cell"))),
             ("looser go rule", first(lambda r: r.get("probe") and r.get("go"))),
             ("recording_2 only, frame sheets", first(lambda r: r.get("sequence"))),
             ("re-grab check", first(lambda r: r.get("regrab")))]
    return [(label, h) for label, h in marks if h is not None]


# ---- 1. the session timeline (hero) ------------------------------------------------------------------------------
def chart_timeline(tries, t):
    W, H = 960, 500
    full = [r for r in tries if not r.get("probe") and r.get("holding_s") is not None]
    probes = [r for r in tries if r.get("probe")]
    svg = Svg(W, H, t, "96 tries in 2 h 40 min",
              f"Every try of the chips pick-and-place on the real SO-101 on Nov 8: {len(full)} full tries and "
              f"{len(probes)} probes, grip time on a log scale, with the changes made along the way.")
    svg.header(f"{len(full) + len(probes)} tries in 2 h 40 min, one red bag of chips",
               ["Every try on the real arm, Nov 8. Dots: how long the jaws held something (log scale). Rings below:",
                "probes, where the arm stops with the jaws open so Astra can check the aim (filled = go)."])
    x0, x1, top, bot, pby = 64, W - 28, 112, 352, 382
    h0 = math.floor(min(hours(r["ts"]) for r in tries) * 2) / 2 - 0.05
    h1 = max(hours(r["ts"]) for r in tries) + 0.08
    X, Y = lin(h0, h1, x0, x1), logscale(0.4, 30, bot, top)
    for v in (0.5, 1, 2, 5, 10, 20):
        svg.line(x0, Y(v), x1, Y(v))
        svg.text(x0 - 8, Y(v) + 4, f"{n(v)} s", size=11, color="muted", anchor="end")
    svg.text(x0 - 8, Y(0.4) + 4, "none", size=11, color="muted", anchor="end")
    hh = math.ceil(h0 * 2) / 2
    while hh <= h1:
        svg.line(X(hh), bot, X(hh), bot + 5, color="axis")
        svg.text(X(hh), pby + 32, clock(hh), size=11, color="muted", anchor="middle")
        hh += 0.5
    svg.line(x0, bot, x1, bot, color="axis")
    ms = milestones(tries)
    for i, (label, h) in enumerate(ms, 1):
        svg.line(X(h), top - 6, X(h), pby + 10, color="axis")
        svg.add(f'<circle cx="{n(X(h))}" cy="{top - 14}" r="8" fill="{t["ink2"]}"/>')
        svg.text(X(h), top - 10, str(i), size=10.5, color="surface", anchor="middle", weight=600)
    svg.text(x0 - 8, pby + 4, "probes", size=11, color="muted", anchor="end")
    for r in probes:
        svg.dot(X(hours(r["ts"])), pby, t["rec"][r["recording"]], r=4, hollow=not r.get("go"))
    for r in full:
        svg.dot(X(hours(r["ts"])), Y(r["holding_s"]), t["rec"][r["recording"]])
    best = max(full, key=lambda r: r["holding_s"])
    second = sorted(full, key=lambda r: -r["holding_s"])[1]
    for r, txt in ((best, f"{n(best['holding_s'])} s: three bags carried ({best['recording']}, pan {best['pan_offset']:+g})"),
                   (second, f"{n(second['holding_s'])} s: carried, dropped short of the box ({second['recording']}, pan {second['pan_offset']:+g})")):
        x, y = X(hours(r["ts"])), Y(r["holding_s"])
        anchor, dx = ("end", -10) if x > W / 2 else ("start", 10)
        svg.text(x + dx, y + 4, txt, size=12, color="ink", anchor=anchor)
    lx = legend(svg, x0, H - 62, [(r, t["rec"][r], False) for r in RECS])
    legend(svg, lx + 6, H - 62, [("probe: go", t["muted"], False), ("probe: no-go", t["muted"], True)])
    kx, ky = x0, H - 32                                   # the milestone key, wrapped to the plot width
    for i, (label, _) in enumerate(ms, 1):
        w = 22 + tw(label, 11.5) + 18
        if kx + w > x1 and kx > x0:
            kx, ky = x0, ky + 18
        svg.add(f'<circle cx="{n(kx + 7)}" cy="{ky - 4}" r="7" fill="{t["ink2"]}"/>')
        svg.text(kx + 7, ky, str(i), size=9.5, color="surface", anchor="middle", weight=600)
        svg.text(kx + 19, ky, label, size=11.5, color="ink2")
        kx += w
    return svg.render()


# ---- 2. grip by recording ------------------------------------------------------------------------------------------
def chart_grip(tries, t):
    W, H = 760, 300
    full = [r for r in tries if not r.get("probe") and r.get("holding_s") is not None]
    svg = Svg(W, H, t, "Grip time by recording", "Grip time of every full try by recording, log scale, with medians.")
    svg.header("recording_2 is the motion that grips",
               ["Grip time of every full try, by recording (log scale). Tick = median."])
    x0, x1 = 150, W - 40
    X = logscale(0.4, 30, x0, x1)
    rows_y = {rec: 120 + i * 52 for i, rec in enumerate(RECS)}
    for v in (0.5, 1, 2, 5, 10, 20):
        svg.line(X(v), 100, X(v), 252)
        svg.text(X(v), 272, f"{n(v)} s", size=11, color="muted", anchor="middle")
    svg.text(X(0.4), 272, "none", size=11, color="muted", anchor="middle")
    for rec, y in rows_y.items():
        vals = [r for r in full if r["recording"] == rec]
        svg.dot(24 + 5, y - 4, t["rec"][rec])
        svg.text(40, y, f"{rec}", size=12.5, color="ink")
        svg.text(40, y + 15, f"{len(vals)} tries", size=11, color="muted")
        med = st.median(r["holding_s"] for r in vals)
        for i, r in enumerate(sorted(vals, key=lambda r: r["ts"])):
            jitter = ((i * 7) % 5 - 2) * 3
            svg.dot(X(r["holding_s"]), y - 4 + jitter, t["rec"][rec], r=4)
        svg.line(X(med), y - 18, X(med), y + 10, color="ink2", width=2)
        top = max(vals, key=lambda r: r["holding_s"])
        if top["holding_s"] > 2:
            svg.text(X(top["holding_s"]) - 9, y - 12, f"{n(top['holding_s'])} s", size=11.5, color="ink", anchor="end")
    return svg.render()


# ---- 3. recording_2's pan search ----------------------------------------------------------------------------------
def chart_pan(tries, t):
    W, H = 760, 330
    r2 = [r for r in tries if r.get("recording") == "recording_2" and not r.get("probe") and r.get("holding_s") is not None]
    svg = Svg(W, H, t, "recording_2 pan search", "Grip time of every full recording_2 try against its base pan.")
    svg.header("recording_2: the search narrowed to pan −2 … −6",
               ["Grip time of every full recording_2 try against the base rotation Astra chose (log scale).",
                "The one carry came at pan −4; the same pan later held 0.9 s, so the bag's lie matters too."])
    x0, x1, top, bot = 70, W - 30, 108, 282
    X, Y = lin(-11, 11, x0, x1), logscale(0.4, 30, bot, top)
    for v in (0.5, 1, 2, 5, 10, 20):
        svg.line(x0, Y(v), x1, Y(v))
        svg.text(x0 - 8, Y(v) + 4, f"{n(v)} s", size=11, color="muted", anchor="end")
    svg.text(x0 - 8, Y(0.4) + 4, "none", size=11, color="muted", anchor="end")
    for p in range(-10, 11, 2):
        svg.text(X(p), bot + 20, f"{p:+d}°" if p else "0°", size=11, color="muted", anchor="middle")
    svg.line(x0, bot, x1, bot, color="axis")
    svg.text((x0 + x1) / 2, bot + 40, "base pan chosen by Astra (degrees)", size=11.5, color="ink2", anchor="middle")
    for r in r2:
        svg.dot(X(r["pan_offset"]), Y(r["holding_s"]), t["rec"]["recording_2"])
    best = max(r2, key=lambda r: r["holding_s"])
    svg.text(X(best["pan_offset"]) + 10, Y(best["holding_s"]) + 4,
             f"{n(best['holding_s'])} s at pan {best['pan_offset']:+g}: three bags carried", size=12, color="ink")
    return svg.render()


# ---- 4. where the jaws closed (top-down) -----------------------------------------------------------------------------
def chart_map(tries, t):
    W, H = 760, 560
    svg = Svg(W, H, t, "Where the jaws closed",
              "Top-down view of where the jaws closed on each full try and stood open on each probe, in cm from the "
              "arm's base, with Astra's estimates of the bag's near edge.")
    svg.header("Where the jaws closed: a 6 × 6 cm search",
               ["Top-down, in cm from the arm's base: dots where the jaws closed, rings where probes held them open,",
                "crosses where Astra put the red bag's near edge."])
    x0, x1, top, bot = 90, 560, 110, 500                  # plot: y_left on the horizontal axis (left = arm's left)
    Xs = lin(-4, -15, x0, x1)                             # y_left from −4 (right edge) … −15: further to the arm's right
    Yf = lin(8, 19, bot, top)                              # forward distance, upward
    k = (x1 - x0) / 11
    for v in range(-14, -3, 2):
        svg.line(Xs(v), top, Xs(v), bot)
        svg.text(Xs(v), bot + 18, f"{-v} cm right", size=11, color="muted", anchor="middle")
    for v in range(9, 19, 2):
        svg.line(x0, Yf(v), x1, Yf(v))
        svg.text(x0 - 8, Yf(v) + 4, f"{v} cm", size=11, color="muted", anchor="end")
    svg.text(x0 - 8, top - 12, "forward", size=11, color="ink2", anchor="end")
    for r in tries:
        b = r.get("hermes_bag_before") or {}
        e = b.get("near_edge_m")
        if isinstance(e, list) and len(e) == 2 and all(isinstance(v, (int, float)) for v in e):
            cx, cy = Xs(e[1] * 100), Yf(e[0] * 100)
            if x0 <= cx <= x1 and top <= cy <= bot:
                svg.add(f'<path d="M{n(cx - 3.5)} {n(cy - 3.5)}L{n(cx + 3.5)} {n(cy + 3.5)}M{n(cx - 3.5)} {n(cy + 3.5)}'
                        f'L{n(cx + 3.5)} {n(cy - 3.5)}" stroke="{t["muted"]}" stroke-width="1.5"/>')
    pts = []
    for r in tries:
        tip = (r.get("open_jaws_at") if r.get("probe") else r.get("close_at")) or {}
        tip = tip.get("tip_m")
        if not tip or r.get("fixed") and r.get("points") != "measured":
            continue
        pts.append((r, Xs(tip[1] * 100), Yf(tip[0] * 100)))
    for r, x, y in pts:
        svg.dot(x, y, t["rec"][r["recording"]], r=4, hollow=bool(r.get("probe")))
    best = max((p for p in pts if not p[0].get("probe")), key=lambda p: p[0].get("holding_s") or 0)
    svg.add(f'<circle cx="{n(best[1])}" cy="{n(best[2])}" r="10" fill="none" stroke="{t["ink"]}" stroke-width="1.5"/>')
    svg.text(best[1] - 14, best[2] - 24, f"the {n(best[0]['holding_s'])} s carry", size=12, color="ink", anchor="end")
    lx, ly = 588, 140
    for rec in RECS:
        svg.dot(lx + 5, ly - 4, t["rec"][rec])
        svg.text(lx + 16, ly, rec, size=12, color="ink2")
        ly += 22
    svg.dot(lx + 5, ly - 4, t["muted"], hollow=True)
    svg.text(lx + 16, ly, "probe (jaws open)", size=12, color="ink2")
    ly += 22
    svg.add(f'<path d="M{lx + 1.5} {ly - 7.5}L{lx + 8.5} {ly - 0.5}M{lx + 1.5} {ly - 0.5}L{lx + 8.5} {ly - 7.5}" '
            f'stroke="{t["muted"]}" stroke-width="1.5"/>')
    svg.text(lx + 16, ly, "Astra: bag's near edge", size=12, color="ink2")
    svg.text(lx, ly + 34, "The box is off this view,", size=11.5, color="muted")
    svg.text(lx, ly + 50, "about 8 cm to the arm's left.", size=11.5, color="muted")
    return svg.render()


# ---- 5. jaw height through each recording ---------------------------------------------------------------------------
def recording_heights():
    import mujoco
    from .arm import SimArm
    from .gentest import DROP_AT, GripEvents, LIB
    arm, out = SimArm(), {}
    for rec in RECS:
        frames = json.loads((LIB / f"{rec}.json").read_text())["frames"]
        ev, marks, series = GripEvents(), {}, []
        for i, f in enumerate(frames):
            e = ev.step(f[5])
            if e and e not in marks:
                marks[e] = i
            if i % 5 == 0 or e:
                arm.d.qpos[arm._qadr] = arm._raw(f)
                mujoco.mj_forward(arm.m, arm.d)
                series.append((i / 50, float(arm.d.site_xpos[arm._tip][2]) * 100))
        z = dict(series)
        out[rec] = {"series": series, "close": marks.get("close_at"), "open": marks.get("open_at"),
                    "drop": DROP_AT.get(rec), "z": z}
    return out


def chart_heights(h, t):
    W, H = 900, 360
    svg = Svg(W, H, t, "Jaw height through each recording",
              "Height of the gripper tip above the table through each taught recording at full speed, with where the "
              "jaws close and open.")
    svg.header("Why recording_2 carried the bags home",
               ["Jaw height through each taught motion (full speed). Filled dot: the jaws close. Ring: they open over",
                "the box. recording_2 never opened, so a hard grip held on; it now opens at its dip over the box."])
    x0, x1, top, bot = 64, W - 210, 108, 300
    tmax = max(s[-1][0] for s in (v["series"] for v in h.values()))
    X, Y = lin(0, math.ceil(tmax), x0, x1), lin(0, 35, bot, top)
    for v in range(0, 36, 10):
        svg.line(x0, Y(v), x1, Y(v))
        svg.text(x0 - 8, Y(v) + 4, f"{v} cm", size=11, color="muted", anchor="end")
    for s in range(0, math.ceil(tmax) + 1, 2):
        svg.text(X(s), bot + 20, f"{s} s", size=11, color="muted", anchor="middle")
    svg.line(x0, bot, x1, bot, color="axis")
    for rec in RECS:
        d = h[rec]
        pts = " ".join(f"{n(X(a))},{n(Y(b))}" for a, b in d["series"])
        svg.add(f'<polyline points="{pts}" fill="none" stroke="{t["rec"][rec]}" stroke-width="2" '
                f'stroke-linejoin="round" stroke-linecap="round"/>')
    for rec in RECS:
        d = h[rec]
        near = lambda i: min(d["series"], key=lambda p: abs(p[0] - i / 50))
        if d["close"] is not None:
            a, b = near(d["close"])
            svg.dot(X(a), Y(b), t["rec"][rec])
        rel = d["open"] if d["open"] is not None else d["drop"]
        if rel is not None:
            a, b = near(rel)
            svg.dot(X(a), Y(b), t["rec"][rec], hollow=True)
            if d["open"] is None:
                svg.text(X(a) + 10, Y(b) + 18, "recording_2 now opens here", size=11.5, color="ink")
        a, b = d["series"][-1]
        svg.dot(x1 + 18, Y(b) - 4 + {"recording_2": -14, "wave": 0, "recording_3": 14}[rec], t["rec"][rec])
        svg.text(x1 + 30, Y(b) + {"recording_2": -14, "wave": 0, "recording_3": 14}[rec], rec, size=12, color="ink2")
    return svg.render()


# ---- 6. where a try's time goes ---------------------------------------------------------------------------------------
def chart_time(tries, t):
    W, H = 760, 220
    full = [r for r in tries if not r.get("probe") and r.get("seconds")]
    gaps = [b["ts"] - a["ts"] for a, b in zip(tries, tries[1:]) if 30 < b["ts"] - a["ts"] < 200 and not a.get("fixed")]
    cycle = st.median(gaps)
    motion = st.median(r["seconds"] for r in full)
    plan = st.median(r["plan_s"] for r in tries if r.get("plan_s"))
    other = max(cycle - motion - plan, 0)
    svg = Svg(W, H, t, "Where a try's time goes",
              f"Median try cycle {n(cycle)} s: arm motion {n(motion)} s, Astra planning {n(plan)} s, the rest {n(other)} s.")
    svg.header(f"Where a try's {round(cycle)} s goes", ["Medians over the session. The arm, not the model, is most of the time."])
    x0, x1, y, hgt = 24, W - 24, 104, 28
    X = lin(0, cycle, x0, x1)
    segs = [("arm motion", motion, t["seg"][0], t["on"]), ("Astra planning", plan, t["seg"][1], t["on"]),
            ("camera, SSH and saving", other, t["grid"], t["ink2"])]
    xa = x0
    for i, (label, v, fill, ink) in enumerate(segs):
        w = X(v) - x0
        gap = 2 if i < len(segs) - 1 else 0
        svg.add(f'<rect x="{n(xa)}" y="{y}" width="{n(max(w - gap, 0))}" height="{hgt}" rx="4" fill="{fill}"/>')
        txt = f"{round(v)} s"
        if tw(txt, 12) + 16 < w:
            svg.text(xa + 8, y + 19, txt, size=12, color=ink, weight=600)
        xa += w
    lx = x0
    for label, v, fill, _ in segs:
        svg.add(f'<rect x="{n(lx)}" y="{y + 52}" width="10" height="10" rx="2" fill="{fill}"/>')
        svg.text(lx + 16, y + 61, label, size=12, color="ink2")
        lx += 16 + tw(label, 12) + 22
    return svg.render()


# ---- 7. Astra's planning time --------------------------------------------------------------------------------------
def chart_planning(tries, ex, t):
    rows = []
    for e in ("none", "low", "medium"):
        v = [r["plan_s"] for r in tries if r.get("effort") == e and r.get("plan_s")]
        if v:
            rows.append(("on the arm, by reasoning effort", f"effort {e}", v))
    if ex["sol"]:
        for m, label in (("astra", "Astra"), ("sol", "Sol")):
            rows.append(("same prompt and images, by model", label, [r["wall_s"] for r in ex["sol"] if r["model"] == m]))
    if ex["effort"]:
        for e in ("none", "low", "medium"):
            rows.append(("same prompt and images, by effort", f"effort {e}",
                         [r["plan_s"] for r in ex["effort"] if r["effort"] == e]))
    if ex["lean"]:
        rows.append(("same snapshot, by prompt size", "full prompt (22 k chars)",
                     [r["plan_s"] for r in ex["lean"] if r["mode"] == "full"]))
        rows.append(("same snapshot, by prompt size", "lean prompt, 6 tries",
                     [r["plan_s"] for r in ex["lean"] if r["mode"] == "lean"]))
    if ex["lean12"]:
        rows.append(("same snapshot, by prompt size", "lean prompt, 12 tries", [r["plan_s"] for r in ex["lean12"]]))
    W = 800
    H = 112 + len(rows) * 28 + len({g for g, _, _ in rows}) * 40 + 56
    svg = Svg(W, H, t, "Astra's planning time",
              "Seconds per planning call: on the arm by reasoning effort, and in matched tests by model, effort and "
              "prompt size.")
    svg.header("Astra's planning time: prompt size matters, effort doesn't",
               ["Seconds per planning call (dots), median tick. Matched tests reuse one frozen snapshot."])
    x0, x1 = 250, W - 40
    X = lin(0, 50, x0, x1)
    y, group = 104, None
    ys = []
    for g, label, vals in rows:
        if g != group:
            y += 34 if group else 12
            svg.text(24, y, g, size=12, color="ink", weight=600)
            group = g
            y += 4
        y += 28
        svg.text(36, y + 4, label, size=12, color="ink2")
        for i, v in enumerate(sorted(vals)):
            svg.dot(X(v), y, t["rec"]["recording_2"], r=4)
        if vals:
            med = st.median(vals)
            svg.line(X(med), y - 11, X(med), y + 11, color="ink2", width=2)
            svg.text(X(med) + 8, y - 8, f"{n(med)} s", size=11, color="muted")
        ys.append(y)
    for v in range(0, 51, 10):
        svg.line(X(v), 112, X(v), ys[-1] + 16)
        svg.text(X(v), ys[-1] + 34, f"{v} s", size=11, color="muted", anchor="middle")
    return svg.render()


# ---- main -------------------------------------------------------------------------------------------------------------
def build(data, out):
    tries = load(data)
    ex = experiments(data)
    try:
        heights = recording_heights()
    except Exception as e:                                       # no MuJoCo here: skip that one chart
        print(f"skipping jaw-heights ({e.__class__.__name__}: {e})")
        heights = None
    charts = {"timeline": lambda t: chart_timeline(tries, t), "grip-by-recording": lambda t: chart_grip(tries, t),
              "recording2-pan": lambda t: chart_pan(tries, t), "jaw-map": lambda t: chart_map(tries, t),
              "time-split": lambda t: chart_time(tries, t), "planning-time": lambda t: chart_planning(tries, ex, t)}
    if heights:
        charts["jaw-heights"] = lambda t: chart_heights(heights, t)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, fn in charts.items():
        for mode, theme in THEMES.items():
            p = out / f"{name}-{mode}.svg"
            p.write_text(fn(theme) + "\n")
            written.append(p)
    return written


def main():
    ap = argparse.ArgumentParser(prog="python -m armsim.report", description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=str(DATA), help="the local data folder (default: <repo>/data/hackathon-2026-11-08)")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    for p in build(Path(a.data), Path(a.out)):
        print(p.relative_to(ROOT) if p.is_relative_to(ROOT) else p)


if __name__ == "__main__":
    main()
