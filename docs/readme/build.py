#!/usr/bin/env python3
"""Builds the README's animated SVGs into this folder. Standard library only.

Each picture is one self-contained SVG, so it plays on GitHub: CSS animation, no scripts,
no outside files. Barlow is embedded as WOFF2 (SIL Open Font License, fonts/OFL.txt).
Backgrounds are transparent and the colors follow the viewer's light or dark theme.

The look follows Anthropic's frontend-design skill and Emil Kowalski's animation skills:
- GitHub's own grays, plus three colors that mean one thing everywhere:
  red = broken, violet = Buddy or a Naive agent at work, green = running.
- Barlow Condensed for the name and the numbers, Barlow for words, sentence case.
- One bold moment, the arm in the hero. The diagrams replay once, hold, and repeat.
- Only transform, opacity, color and line drawing move, on curves from Emil's skills and
  easings.co. With reduced motion, each picture holds its finished state.

    python3 docs/readme/build.py            # rewrites every *.svg next to this file

Numbers on the art come from the repo, each with its source in README.md.
"""
import base64
from math import acos, atan2, cos, degrees, hypot, pi, sin
from pathlib import Path

OUT = Path(__file__).resolve().parent
FONTS = OUT / 'fonts'

# GitHub's Primer colors for light and dark pages. The brand violet and the video poster's
# text stay the same in both.
LIGHT = dict(ink='#1f2328', mute='#59636e', line='#d1d9e0', surf='#f6f8fa', red='#cf222e', vio='#8250df', grn='#1a7f37',
             pbg='#1f2328', pedge='#1f2328', brand='#8250df', brand2='#6639ba', paper='#f0f6fc', pmute='#9198a1')
DARK = dict(LIGHT, ink='#e6edf3', mute='#9198a1', line='#3d444d', surf='#151b23', red='#f85149', vio='#a371f7', grn='#3fb950',
            pbg='#151b23', pedge='#3d444d')

EASE_OUT = 'cubic-bezier(.23,1,.32,1)'      # Emil's strong ease-out: something arriving or leaving
EASE_IN_OUT = 'cubic-bezier(.77,0,.175,1)'  # Emil's strong ease-in-out: something moving across
JOINT = 'cubic-bezier(.65,0,.35,1)'         # easeInOutCubic (easings.co): the arm's joints
CRASH = 'cubic-bezier(.32,0,.67,0)'         # easeInCubic: only for the arm running into its fault

FACES = {'m': ('B', 500, 'Barlow-Medium.woff2'), 's': ('B', 600, 'Barlow-SemiBold.woff2'),
         'cs': ('BC', 600, 'BarlowCondensed-SemiBold.woff2'), 'cb': ('BC', 700, 'BarlowCondensed-Bold.woff2')}
SANS = 'system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif'
USED = set()  # faces the picture being built uses; svg() embeds only those
NB = ' '  # a space that survives between tspans


def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def n(v):
    """A short number for an attribute or a keyframe."""
    s = f'{v:.2f}'.rstrip('0').rstrip('.')
    return '0' if s in ('', '-0') else s


def text(x, y, s, size, face='m', color='ink', anchor='start', cls='', extra=''):
    USED.add(face)
    c = ' '.join(filter(None, (f't-{face}', f'f-{color}' if color else '', cls)))
    return f'<text x="{n(x)}" y="{n(y)}" class="{c}" font-size="{size}" text-anchor="{anchor}"{extra}>{esc(s)}</text>'


def runs(x, y, parts, anchor='start'):
    """One line in several faces: parts are (words, size, face, color)."""
    spans = ''
    for s, size, face, color in parts:
        USED.add(face)
        spans += f'<tspan class="t-{face} f-{color}" font-size="{size}">{esc(s)}</tspan>'
    return f'<text x="{n(x)}" y="{n(y)}" text-anchor="{anchor}">{spans}</text>'


def svg(w, h, title, desc, css, body, still):
    """One self-contained SVG. `still` is the time (s) shown when the viewer asks for reduced motion."""
    faces = ''
    for k, (fam, wt, fn) in FACES.items():
        if k in USED:
            data = base64.b64encode((FONTS / fn).read_bytes()).decode()
            faces += (f'@font-face{{font-family:{fam};font-weight:{wt};src:url(data:font/woff2;base64,{data}) format("woff2")}}\n'
                      f'.t-{k}{{font-family:{fam},{SANS};font-weight:{wt}}}\n')
    USED.clear()
    var = lambda p: ';'.join(f'--{k}:{v}' for k, v in p.items())
    theme = (f':root{{{var(LIGHT)}}}\n@media (prefers-color-scheme:dark){{:root{{{var(DARK)}}}}}\n'
             + ''.join(f'.f-{k}{{fill:var(--{k})}}.s-{k}{{stroke:var(--{k})}}' for k in LIGHT))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-labelledby="tt dd">
<title id="tt">{esc(title)}</title>
<desc id="dd">{esc(desc)}</desc>
<style>
{faces}{theme}
.fb{{transform-box:fill-box;transform-origin:center}}
{css}
@media (prefers-reduced-motion:reduce){{*{{animation-delay:-{still}s!important;animation-play-state:paused!important}}}}
</style>
{body}
</svg>
'''


# --------------------------------------------------------------------------- motion
def kf(name, T, stops):
    """@keyframes from (seconds, css) or (seconds, css, easing) stops over a T-second loop.
    An easing runs from its stop to the next one; without one that stretch is linear."""
    out = ''
    for st in stops:
        t, c = st[0], st[1]
        e = f';animation-timing-function:{st[2]}' if len(st) > 2 and st[2] else ''
        out += f'{max(0.0, min(100.0, t / T * 100)):.3f}%{{{c}{e}}}'
    return f'@keyframes {name}{{{out}}}'


def anim(sel, names, T):
    names = [names] if isinstance(names, str) else names
    return f'{sel}{{animation:{",".join(f"{k} {T}s linear infinite" for k in names)}}}'


def fades(T, spans, fin=.25, fout=.25, enter='', leave=''):
    """Stops for opacity 1 inside each (start, end) span and 0 outside, fading in before the start
    and out after the end; `enter`/`leave` are the transforms it comes from and goes to.
    A span that starts at 0 or ends at T runs on across the loop."""
    rest = 'none' if enter or leave else ''

    def st(op, tf):
        return f'opacity:{op}' + (f';transform:{tf}' if tf else '')
    stops = [] if spans[0][0] <= 0 else [(0, st(0, enter))]
    for a, b in spans:
        stops += [(0, st(1, rest))] if a <= 0 else [(a - fin, st(0, enter), EASE_OUT), (a, st(1, rest))]
        stops += [(T, st(1, rest))] if b >= T else [(b, st(1, rest), EASE_OUT), (b + fout, st(0, leave))]
    if spans[-1][1] < T:
        stops.append((T, st(0, enter)))
    return stops


def show(css, cls, T, spans, **kw):
    css.append(kf(cls, T, fades(T, spans, **kw)))
    css.append(anim(f'.{cls}', cls, T))


def blink(sel, period, at):
    return (kf('blink', period, [(0, 'transform:scaleY(1)'), (at, 'transform:scaleY(1)', EASE_OUT),
                                 (at + .07, 'transform:scaleY(.1)', EASE_OUT), (at + .22, 'transform:scaleY(1)'),
                                 (period, 'transform:scaleY(1)')])
            + anim(sel, 'blink', period))


def pulse(css, body, cls, x, y, r, color, t, T, grow=2.6, dur=.8):
    """One ring that grows and fades from a point at time t: "this just happened"."""
    body.append(f'<circle class="{cls} s-{color} fb" cx="{n(x)}" cy="{n(y)}" r="{r}" fill="none" stroke-width="2"/>')
    off, gone = 'opacity:0;transform:scale(1)', f'opacity:0;transform:scale({grow})'
    css.append(kf(cls, T, [(0, off), (t, off), (t + .01, 'opacity:.55;transform:scale(1)', EASE_OUT), (t + dur, gone), (T, gone)]))
    css.append(anim(f'.{cls}', cls, T))


def light_up(css, body, cls, x, y, r, color, t, reset, T):
    """A node that fills with its color at time t and stays lit until `reset`."""
    body.append(f'<circle class="{cls} f-{color} fb" cx="{n(x)}" cy="{n(y)}" r="{r}"/>')
    off, on = 'opacity:0;transform:scale(.9)', 'opacity:1;transform:scale(1)'
    css.append(kf(cls, T, [(0, off), (t, off, EASE_OUT), (t + .3, on), (reset, on), (reset + .01, off), (T, off)]))
    css.append(anim(f'.{cls}', cls, T))


def overlay(css, T, hold, reset):
    """The lit layer (class ov) fades out after `hold`, and is back, already empty, after `reset`."""
    css.append(kf('ov', T, [(0, 'opacity:1'), (hold, 'opacity:1', EASE_OUT), (hold + .4, 'opacity:0'),
                            (reset + .05, 'opacity:0'), (reset + .1, 'opacity:1'), (T, 'opacity:1')]))
    css.append(anim('.ov', 'ov', T))


def drawn(L, p):
    """stroke-dashoffset that shows the first p of a line L long (round caps hidden at 0)."""
    return f'stroke-dashoffset:{n(L - p if p > 0 else L + 6)}'


# --------------------------------------------------------------------------- the Buddy chip
def chip(x, y, size, smile=False, eyes=''):
    """The Buddy chip from pitch/buddy.html, centered on x, y. `eyes` is a class for blinking."""
    s = size / 40
    pins = ''.join(f'<rect x="{p - 3}" y="-27" width="6" height="9" rx="3"/><rect x="{p - 3}" y="18" width="6" height="9" rx="3"/>'
                   f'<rect x="-27" y="{p - 3}" width="9" height="6" rx="3"/><rect x="18" y="{p - 3}" width="9" height="6" rx="3"/>'
                   for p in (-10, 0, 10))
    ey = -4 if smile else -2
    face = (f'<g class="{eyes} fb" fill="#fff"><ellipse cx="-7.5" cy="{ey}" rx="3.6" ry="5.4"/>'
            f'<ellipse cx="7.5" cy="{ey}" rx="3.6" ry="5.4"/></g>')
    if smile:
        face += '<path d="M-6 8 Q0 13 6 8" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/>'
    return (f'<g transform="translate({n(x)} {n(y)}) scale({s:.4f})"><g class="f-brand2">{pins}</g>'
            f'<rect x="-20" y="-20" width="40" height="40" rx="12" class="f-brand"/>{face}</g>')


# --------------------------------------------------------------------------- the arm
L1, L2 = 150, 140  # shoulder to elbow, elbow to wrist
GRIP = 56          # wrist to the middle of a held block
OPEN = 7           # how far each jaw opens


def ik(S, W):
    """Shoulder and elbow angles (deg) that put the wrist at W, elbow up."""
    dx, dy = W[0] - S[0], W[1] - S[1]
    d = hypot(dx, dy)
    phi, beta = atan2(dy, dx), acos((L1 * L1 + d * d - L2 * L2) / (2 * L1 * d))
    sols = []
    for t1 in (phi + beta, phi - beta):
        ex, ey = S[0] + L1 * cos(t1), S[1] + L1 * sin(t1)
        sols.append((ey, degrees(t1), degrees(atan2(W[1] - ey, W[0] - ex) - t1)))
    return min(sols)[1:]


def poses(S, wrists):
    """Joint angles for each wrist point. The wrist angle keeps the gripper pointing straight down,
    and because every joint runs the same curve between stops, it keeps pointing down in between."""
    out = []
    for W in wrists:
        a1, a2 = ik(S, W)
        if out:
            a1 += 360 * round((out[-1][0] - a1) / 360)
            a2 += 360 * round((out[-1][1] - a2) / 360)
        out.append((a1, a2, 90 - a1 - a2))
    return out


def mix(p, q, f):
    return tuple(a + (b - a) * f for a, b in zip(p, q))


def arm(S, bench, sw=2.5):
    """The SO-101 side on: base, shoulder, upper arm, forearm, wrist and gripper, each joint a nested
    group that CSS rotates (j1 j2 j3). Outlines are currentColor, so the whole arm can change color.
    The jaws are jw1 and jw2, and hb is the block while it's held."""
    st = f'stroke="currentColor" stroke-width="{n(sw)}" stroke-linejoin="round"'

    def box(x, y, w, h, r, cls=''):
        return f'<rect x="{n(x)}" y="{n(y)}" width="{n(w)}" height="{n(h)}" rx="{r}" class="f-surf{cls}" {st}/>'
    pivot = f'<circle r="5.5" class="f-surf" {st}/><circle r="2" fill="currentColor"/>'
    sx, sy = S
    return ('<g class="arm">'
            + box(sx - 52, sy + 24, 104, bench - sy - 24, 7)          # base
            + box(sx - 24, sy - 24, 48, 48, 10)                        # shoulder servo
            + f'<g transform="translate({n(sx)} {n(sy)})"><g class="j1">'
            + box(-13, -13, L1 + 26, 26, 13)                           # upper arm
            + box(L1 - 18, -18, 36, 36, 8)                             # elbow servo
            + f'<g transform="translate({L1} 0)"><g class="j2">'
            + box(-10, -10, L2 + 20, 20, 10)                           # forearm
            + f'<g transform="translate({L2} 0)"><g class="j3">'
            + box(-15, -15, 30, 30, 7)                                 # wrist servo
            + '<rect class="hb f-ink" x="36" y="-20" width="40" height="40" rx="5"/>'
            + '<g class="jw1">' + box(31, 20, 37, 7, 3) + '</g>'
            + '<g class="jw2">' + box(31, -27, 37, 7, 3) + '</g>'
            + box(14, -30, 20, 60, 6)                                  # gripper
            + pivot + '</g></g>' + pivot + '</g></g>' + pivot + '</g></g></g>')


# --------------------------------------------------------------------------- the pictures
def hero():
    """The name, and the arm it's about. It picks up a block, a firmware bug stops it dead,
    Buddy rolls the firmware back, and the arm finishes the job. The page's one bold moment."""
    W, H, T = 1200, 350, 12.0
    bench, K = 284, 1.28  # the scene is drawn at 1x and shown at K, its bench on y=314
    sw = 2.5 / K
    S, PX, BX = (1040, bench - 54), 800, 915
    low = bench - 20 - GRIP  # wrist height that holds a block on the bench
    home, above, pick, lift, over, place = poses(S, [(955, 130), (PX, 150), (PX, low), (PX, 130), (BX, 130), (BX, low)])
    stall, hit = mix(lift, over, .38), mix(lift, over, .41)
    track = [(0, home), (.5, home, JOINT), (1.55, above, JOINT), (2.05, pick), (2.45, pick, JOINT), (3.0, lift, CRASH),
             (3.55, hit, EASE_OUT), (3.8, stall), (6.85, stall, JOINT), (7.75, over, JOINT), (8.3, place),
             (8.75, place, JOINT), (9.25, over, JOINT), (10.25, home), (T, home)]
    css, body = [], []
    for k, j in enumerate(('j1', 'j2', 'j3')):
        css.append(kf(j, T, [(s[0], f'transform:rotate({n(s[1][k])}deg)') + s[2:] for s in track]))
        css.append(anim(f'.{j}', j, T))
    for j, sign in (('jw1', 1), ('jw2', -1)):
        op, shut = f'transform:translateY({OPEN * sign}px)', 'transform:translateY(0)'
        css.append(kf(j, T, [(0, op), (2.08, op, EASE_OUT), (2.35, shut), (8.33, shut, EASE_OUT), (8.6, op), (T, op)]))
        css.append(anim(f'.{j}', j, T))
    css.append(kf('hb', T, [(0, 'opacity:0'), (2.36, 'opacity:0'), (2.37, 'opacity:1'), (8.33, 'opacity:1'), (8.34, 'opacity:0'), (T, 'opacity:0')]))
    css.append(kf('b0', T, [(0, 'opacity:1'), (2.36, 'opacity:1'), (2.37, 'opacity:0'), (11.35, 'opacity:0', EASE_OUT), (11.75, 'opacity:1'), (T, 'opacity:1')]))
    css.append(kf('b1', T, [(0, 'opacity:0'), (8.33, 'opacity:0'), (8.34, 'opacity:1'), (10.95, 'opacity:1', EASE_OUT), (11.3, 'opacity:0'), (T, 'opacity:0')]))
    css.append(anim('.hb', 'hb', T) + anim('.b0', 'b0', T) + anim('.b1', 'b1', T))
    # the arm goes red when it stops, violet while Buddy works on it, and back to ink
    css.append(kf('armc', T, [(0, 'color:var(--ink)'), (3.5, 'color:var(--ink)', EASE_OUT), (3.65, 'color:var(--red)'),
                              (5.0, 'color:var(--red)', EASE_OUT), (5.3, 'color:var(--vio)'), (6.7, 'color:var(--vio)', EASE_OUT),
                              (7.0, 'color:var(--ink)'), (T, 'color:var(--ink)')]))
    css.append(anim('.arm', 'armc', T))
    body.append(f'<g transform="translate({n(650 - 760 * K)} {n(314 - bench * K)}) scale({K})">')
    body.append(f'<path d="M760 {bench} H1140" class="s-line" stroke-width="{n(sw)}" stroke-linecap="round"/>')
    body.append(f'<path d="M{BX - 44} {bench - 46} V{bench} H{BX + 44} V{bench - 46}" fill="none" class="s-ink" '
                f'stroke-width="{n(sw)}" stroke-linejoin="round" stroke-linecap="round"/>')
    for cls, x in (('b0', PX), ('b1', BX)):
        body.append(f'<rect class="{cls} f-ink" x="{x - 20}" y="{bench - 40}" width="40" height="40" rx="5"/>')
    body.append(arm(S, bench, sw) + '</g>')
    # the words
    body.append(text(53, 158, 'Hardware Buddy', 92, 'cb', 'ink', extra=' letter-spacing="-.5"'))
    body.append(text(57, 208, 'Robots that fix themselves.', 30, 'm', 'mute'))
    # Buddy says what's happening
    cx, cy = 77, 305
    body.append(chip(cx, cy, 32, eyes='be'))
    css.append(kf('blink', T, [(0, 'transform:scaleY(1)'), (6.95, 'transform:scaleY(1)', EASE_OUT), (7.02, 'transform:scaleY(.1)', EASE_OUT),
                               (7.17, 'transform:scaleY(1)'), (T, 'transform:scaleY(1)')]) + anim('.be', 'blink', T))
    ring = f'cx="{cx}" cy="{cy}" r="28" fill="none" stroke-width="2.5"'
    body.append(f'<circle class="rr s-red fb" {ring}/>')
    show(css, 'rr', T, [(3.75, 5.05)], fin=.25, fout=.3, enter='scale(.9)', leave='scale(1)')
    pulse(css, body, 'rp', cx, cy, 28, 'red', 3.6, T, grow=1.6, dur=.9)
    C = 2 * pi * 28
    body.append(f'<g transform="rotate(-90 {cx} {cy})"><circle class="rv s-vio" {ring} stroke-linecap="round" stroke-dasharray="{n(C)} {n(C + 10)}"/></g>')
    css.append(kf('rvd', T, [(0, f'stroke-dashoffset:{n(C)}'), (5.1, f'stroke-dashoffset:{n(C)}', EASE_IN_OUT), (6.65, 'stroke-dashoffset:0'), (T, 'stroke-dashoffset:0')]))
    css.append(kf('rvo', T, fades(T, [(5.05, 6.8)], fin=.1, fout=.3)))
    css.append(anim('.rv', ['rvd', 'rvo'], T))
    body.append(f'<circle class="rg s-grn fb" {ring}/>')
    show(css, 'rg', T, [(6.75, 10.3)], fin=.2, fout=.5)
    lines = [('Picking up a block', [(0, 3.6), (11.85, T)]), ('Stopped by a firmware bug', [(3.85, 5.0)]),
             ('Rolling the firmware back, offline', [(5.25, 6.75)]), ('Running again, nobody touched it', [(7.0, 11.6)])]
    for i, (s, spans) in enumerate(lines):
        body.append(f'<g class="st{i}">' + text(cx + 44, 314, s, 24, 's', 'ink') + '</g>')
        show(css, f'st{i}', T, spans, enter='translateY(7px)', leave='translateY(-7px)')
    return svg(W, H, 'Hardware Buddy: robots that fix themselves',
               'A robot arm picks up a block. A firmware bug stops it dead and it turns red. Buddy rolls the firmware '
               'back, offline, and the arm finishes the job: running again, nobody touched it.',
               '\n'.join(css), ''.join(body), 10.35)


def heal():
    """The real bench run as a timeline at real-time proportions, replayed in under 7 s."""
    W, H, T = 1200, 206, 11.0
    x0, x1, y = 60, 1140, 92
    X = lambda s: x0 + (x1 - x0) * s / 141
    at = lambda s: .6 + 6.6 * s / 141
    HOLD, RESET = 10.0, 10.45
    events = [(0, 'Bad firmware flashed', 'v1.7, the arm fails its job', 'red', 'start'),
              (46, 'Rolled back to v1.0', 'by the local healer', 'vio', 'middle'),
              (72, 'Job runs clean', '663 of 663 frames', 'grn', 'middle'),
              (141, 'Root cause found', 'one line in hcsr04.c', 'vio', 'end')]
    css, body, ov = [], [], []
    body.append(f'<path d="M{x0} {y} H{x1}" class="s-line" stroke-width="4" stroke-linecap="round"/>')
    for s, title, detail, col, anchor in events:
        x = X(s)
        lx = {'start': x - 8, 'end': x + 8}.get(anchor, x)
        body.append(f'<circle cx="{n(x)}" cy="{y}" r="8" class="f-line"/>')
        body.append(text(lx, 50, f'{s} s', 34, 'cs', 'ink', anchor))
        body.append(text(lx, 142, title, 22, 's', 'ink', anchor))
        body.append(text(lx, 170, detail, 19, 'm', 'mute', anchor))
    # red while the arm is broken, green once the firmware is rolled back; time runs linear
    for cls, a, b, col in (('pr', 0, 46, 'red'), ('pg', 46, 141, 'grn')):
        L = X(b) - X(a)
        ov.append(f'<path class="{cls} s-{col}" d="M{n(X(a))} {y} H{n(X(b))}" fill="none" stroke-width="4" stroke-linecap="round" '
                  f'stroke-dasharray="{n(L)} {n(L + 20)}"/>')
        css.append(kf(cls, T, [(0, drawn(L, 0)), (at(a), drawn(L, 0)), (at(b), drawn(L, L)), (RESET, drawn(L, L)),
                               (RESET + .01, drawn(L, 0)), (T, drawn(L, 0))]))
        css.append(anim(f'.{cls}', cls, T))
    span = x1 - x0
    ov.append(f'<g class="hx"><circle class="hd" cx="{x0}" cy="{y}" r="6.5"/></g>')
    css.append(kf('hx', T, [(0, 'transform:translateX(0)'), (at(0), 'transform:translateX(0)'), (at(141), f'transform:translateX({n(span)}px)'),
                            (RESET, f'transform:translateX({n(span)}px)'), (RESET + .01, 'transform:translateX(0)'), (T, 'transform:translateX(0)')]))
    css.append(kf('hc', T, [(0, 'fill:var(--red)'), (at(46), 'fill:var(--red)'), (at(46) + .01, 'fill:var(--grn)'), (RESET, 'fill:var(--grn)'),
                            (RESET + .01, 'fill:var(--red)'), (T, 'fill:var(--red)')]))
    css.append(kf('ho', T, fades(T, [(at(0), at(141))], fin=.2, fout=.25)))
    css.append(anim('.hx', 'hx', T) + anim('.hd', ['hc', 'ho'], T))
    for i, (s, _, _, col, _) in enumerate(events):
        light_up(css, ov, f'n{i}', X(s), y, 8, col, at(s), RESET, T)
        pulse(css, ov, f'p{i}', X(s), y, 8, col, at(s), T)
    overlay(css, T, HOLD, RESET)
    body.append('<g class="ov">' + ''.join(ov) + '</g>')
    return svg(W, H, 'The arm heals its own firmware, offline',
               'A real bench run with no internet and nobody touching it. At 0 s, bad firmware v1.7 makes the arm fail its job. '
               'At 46 s, the local healer has rolled the firmware back to v1.0. At 72 s, the job runs clean, 663 of 663 frames. At 141 s, it has found '
               'the root cause, one line in hcsr04.c.',
               '\n'.join(css), ''.join(body), 9.0)


def steps(css, body, T, nodes, y, dim, t0=.6, dwell=.5, move=.8):
    """Nodes on a track, lit in turn by a head that moves node to node and waits at each.
    `dim` is (i, j, number, words): a dimension line, as on a drawing, from node i to node j that
    fills in step with the head. Returns a time when everything is lit."""
    xs = [nd[0] for nd in nodes]
    arrive = [t0 + k * (dwell + move) for k in range(len(nodes))]
    HOLD = arrive[-1] + 3.4
    RESET = HOLD + .45
    assert RESET + .3 < T
    xa, xb = xs[0], xs[-1]
    ov = []
    body.append(f'<path d="M{xa} {y} H{xb}" class="s-line" stroke-width="4" stroke-linecap="round"/>')
    # where the head is, as (time, px from xa, easing to the next stop)
    path = [(0, 0)]
    for k in range(len(nodes) - 1):
        path += [(arrive[k] + dwell, xs[k] - xa, EASE_IN_OUT), (arrive[k + 1], xs[k + 1] - xa)]
    path += [(RESET, xb - xa), (RESET + .01, 0), (T, 0)]
    L = xb - xa
    ov.append(f'<path class="tp s-ink" d="M{xa} {y} H{xb}" fill="none" stroke-width="4" stroke-linecap="round" stroke-dasharray="{L} {L + 20}"/>')
    css.append(kf('tp', T, [(t, drawn(L, p)) + tuple(rest) for t, p, *rest in path]) + anim('.tp', 'tp', T))
    ov.append(f'<g class="hx"><circle class="hd f-ink" cx="{xa}" cy="{y}" r="6.5"/></g>')
    css.append(kf('hx', T, [(t, f'transform:translateX({n(p)}px)') + tuple(rest) for t, p, *rest in path]) + anim('.hx', 'hx', T))
    css.append(kf('ho', T, fades(T, [(t0, arrive[-1])], fin=.2, fout=.25)) + anim('.hd', 'ho', T))
    for k, (x, title, detail, col) in enumerate(nodes):
        body.append(f'<circle cx="{x}" cy="{y}" r="8" class="f-line"/>')
        body.append(text(x, y + 52, title, 22, 's', 'ink', 'middle'))
        body.append(text(x, y + 80, detail, 19, 'm', 'mute', 'middle'))
        light_up(css, ov, f'n{k}', x, y, 8, col, arrive[k], RESET, T)
        pulse(css, ov, f'p{k}', x, y, 8, col, arrive[k], T)
    i, j, num, words = dim
    di, dj, dy = xs[i], xs[j], y - 60
    body.append(f'<path d="M{di} {y - 18} V{dy - 10} M{dj} {y - 18} V{dy - 10}" class="s-line" stroke-width="1.5"/>')
    body.append(f'<path d="M{di + 2} {dy} H{dj - 2}" class="s-mute" stroke-width="1.5"/>'
                f'<path d="M{di} {dy} l11 -4.5 v9 Z M{dj} {dy} l-11 -4.5 v9 Z" class="f-mute"/>')
    D = dj - di
    ov.append(f'<path class="dp s-ink" d="M{di} {dy} H{dj}" fill="none" stroke-width="2.5" stroke-dasharray="{D} {D + 20}"/>')
    clamp = lambda p: max(0, min(D, p + xa - di))
    css.append(kf('dp', T, [(t, drawn(D, clamp(p))) + tuple(rest) for t, p, *rest in path]) + anim('.dp', 'dp', T))
    body.append(runs((di + dj) / 2, dy - 16, [(num, 30, 'cb', 'ink'), (NB + words, 19, 'm', 'mute')], 'middle'))
    overlay(css, T, HOLD, RESET)
    body.append('<g class="ov">' + ''.join(ov) + '</g>')
    return HOLD - .6


def technician():
    """A hardware fault, taken by a Naive agent, ending at the one step a person keeps."""
    W, H, T = 1200, 232, 9.0
    css, body = [], []
    still = steps(css, body, T, [(150, 'The arm jams', 'the healer parks it', 'red'),
                                 (450, 'A Naïve agent diagnoses it', 'one 27 s session, $0.08', 'vio'),
                                 (750, 'Plain code emails a tech', 'and stages the spare part', 'ink'),
                                 (1050, 'A person approves the buy', 'the one step we keep human', 'grn')],
                  y=128, dim=(0, 3, '38 s', 'from the jam to a case waiting for approval'))
    return svg(W, H, 'When it needs hands, a Naïve agent takes the case',
               'A live run. The arm jams and the healer parks it. A Naïve agent diagnoses it in one 27 s session for $0.08. '
               'Plain code emails a technician and stages the spare part, and a person approves the purchase. 38 s from the '
               'jam to a case waiting for approval.',
               '\n'.join(css), ''.join(body), still)


def fleet():
    """A fix that failed on one robot becomes a skill, the gate tries it, and the fleet gets it."""
    W, H, T = 1200, 232, 9.0
    css, body = [], []
    still = steps(css, body, T, [(150, 'A fix fails on sim-1', 'and holds on sim-3', 'red'),
                                 (450, 'A Naïve agent writes a skill', 'in 22 s, for $0.03', 'vio'),
                                 (750, 'The gate tries it', 'on sim-2, for 30 s', 'ink'),
                                 (1050, 'Promoted to the fleet', '0 human clicks', 'grn')],
                  y=128, dim=(1, 3, '57 s', 'from the agent session to the whole fleet'))
    return svg(W, H, 'Across the fleet, every fault happens once',
               'A live run. A fix fails on sim-1 and holds on sim-3. A Naïve agent writes the lesson down as a skill in 22 s for '
               '$0.03. The gate tries it on sim-2 for 30 s, then promotes it to the fleet: 57 s from the agent session, 0 human clicks.',
               '\n'.join(css), ''.join(body), still)


def stats():
    """Four measured numbers, set as type. Still on purpose."""
    W, H = 1200, 168
    cols = [('141 s', 'for a real arm to heal its own', 'firmware, offline'),
            ('10 of 12', 'planted-fault runs fixed by', 'a 3.3 GB local model'),
            ('57 s', 'from a Naïve agent session', 'to a fleet-wide fix'),
            ('$0.08', 'for a Naïve agent session', 'that diagnoses a fault')]
    body = ''
    for i, (num, l1, l2) in enumerate(cols):
        x = 48 + i * 292
        body += text(x - 2, 76, num, 80, 'cb', 'ink') + text(x, 118, l1, 19, 'm', 'mute') + text(x, 144, l2, 19, 'm', 'mute')
    return svg(W, H, 'Hardware Buddy in numbers',
               '141 s for a real arm to heal its own firmware, offline. 10 of 12 planted-fault runs fixed by a 3.3 GB local model. '
               '57 s from a Naïve agent session to a fleet-wide fix. $0.08 for a Naïve agent session that diagnoses a fault.',
               '', body, 0)


def stack():
    """Three layers. Reports go up, fixes come down."""
    W, H, T = 1200, 470, 9.0
    tiers = [('Naïve', "the company's agents", [('Fleet doctor', 'turns failures into skills'), ('Technician', 'emails, stages parts'),
                                                 ('Board and Skills', 'job cards, playbooks')]),
             ('Romulus', 'our cloud', [('Incidents', 'every heal, labelled'), ('The gate', 'replay, then a sim canary'),
                                       ('Work orders', 'faults that need hands')]),
             ('The bench', 'offline', [('Sensor pod and healer', 'qwen3.5:4b, 38 tools'), ('SO-101 arm', 'a watchdog parks it'),
                                       ('Hermes', 'runs Naïve job cards')])]
    css, body = [], []
    ys = [66, 232, 398]
    for (name, sub, comps), yb in zip(tiers, ys):
        body.append(text(46, yb, name, 34, 'cb', 'ink') + text(48, yb + 28, sub, 19, 'm', 'mute'))
        for k, (c1, c2) in enumerate(comps):
            x = 330 + k * 300
            body.append(text(x, yb, c1, 22, 's', 'ink') + text(x, yb + 28, c2, 19, 'm', 'mute'))
    UP, DN = 597, 623
    gaps = [(ys[0], 'work orders, failure clusters', 'skills, rules'), (ys[1], 'incidents, heal runs', 'new versions')]
    for yb, up, dn in gaps:
        top, bot = yb + 50, yb + 124
        for x, y0, y1 in ((UP, bot, top), (DN, top, bot)):
            d = 1 if y1 > y0 else -1
            body.append(f'<path d="M{x} {y0} V{y1 - 2 * d}" class="s-mute" stroke-width="1.5"/>'
                        f'<path d="M{x} {y1} l-4.5 {-11 * d} h9 Z" class="f-mute"/>')
        mid = (top + bot) / 2 + 6
        body.append(text(UP - 16, mid, up, 19, 'm', 'mute', 'end') + text(DN + 16, mid, dn, 19, 'm', 'mute'))
    # one report goes up through both gaps, then one fix comes down
    trips = [('u0', UP, ys[1] + 124, ys[1] + 50, .5, 'ink'), ('u1', UP, ys[0] + 124, ys[0] + 50, 1.5, 'ink'),
             ('d0', DN, ys[0] + 50, ys[0] + 124, 3.4, 'grn'), ('d1', DN, ys[1] + 50, ys[1] + 124, 4.4, 'grn')]
    for cls, x, y0, y1, t, col in trips:
        body.append(f'<g class="{cls}"><circle cx="{x}" cy="0" r="5" class="f-{col}"/></g>')
        css.append(kf(cls, T, [(0, f'opacity:0;transform:translateY({y0}px)'), (t, f'opacity:0;transform:translateY({y0}px)', EASE_OUT),
                               (t + .15, f'opacity:1;transform:translateY({n(y0 + (y1 - y0) * .12)}px)', EASE_IN_OUT),
                               (t + .75, f'opacity:1;transform:translateY({n(y1 - (y1 - y0) * .12)}px)', EASE_OUT),
                               (t + .9, f'opacity:0;transform:translateY({y1}px)'), (T, f'opacity:0;transform:translateY({y1}px)')]))
        css.append(anim(f'.{cls}', cls, T))
    return svg(W, H, 'The stack, from servo to company',
               'Three layers. On the bench, offline: the sensor pod and its local healer (qwen3.5:4b, 38 tools), the SO-101 arm with '
               'a watchdog, and Hermes running Naïve job cards. Incidents and heal runs go up to Romulus, our cloud, which keeps the '
               'labelled incidents, the gate and the work orders. Work orders and failure clusters go up to Naïve agents: the fleet '
               'doctor, the technician, and the board with Naïve Skills. Skills and rules come back down through the gate, and new '
               'versions go back to the bench.',
               '\n'.join(css), ''.join(body), 0)


def video():
    """The demo video's poster: a player, a play button, and where it opens."""
    W, H, T = 1200, 540, 6.0
    css, body = [], []
    body.append(f'<rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="24" class="f-pbg s-pedge" stroke-width="2"/>')
    body.append(chip(66, 64, 30) + text(98, 72, 'Hardware Buddy', 24, 's', 'paper'))
    cx, cy = 600, 238
    pulse(css, body, 'pp', cx, cy, 62, 'brand', 2.4, T, grow=1.55, dur=1.4)
    body.append(f'<circle cx="{cx}" cy="{cy}" r="62" class="f-brand"/>'
                f'<path d="M{cx - 16} {cy - 29} L{cx + 32} {cy} L{cx - 16} {cy + 29} Z" fill="#fff" stroke="#fff" stroke-width="8" stroke-linejoin="round"/>')
    body.append(text(cx, 384, 'Watch the demo', 64, 'cb', 'paper', 'middle'))
    body.append(text(cx, 424, 'Opens on Google Drive', 22, 'm', 'pmute', 'middle'))
    return svg(W, H, 'Watch the Hardware Buddy demo', 'Video poster with a play button. The demo video opens on Google Drive.',
               '\n'.join(css), ''.join(body), 0)


def mark():
    """The Buddy chip on its own, blinking now and then."""
    return svg(176, 176, 'Hardware Buddy', 'The Buddy chip: a violet chip with pins, two eyes and a smile.',
               blink('.me', 7.0, 4.6), chip(88, 88, 120, smile=True, eyes='me'), 0)


BUILDERS = {'hero.svg': hero, 'buddy.svg': mark, 'video.svg': video, 'heal.svg': heal,
            'technician.svg': technician, 'fleet.svg': fleet, 'stats.svg': stats, 'stack.svg': stack}

if __name__ == '__main__':
    for name, fn in BUILDERS.items():
        s = fn()
        (OUT / name).write_text(s)
        print('wrote', OUT / name, f'{len(s) / 1024:.1f} KB')
