#!/usr/bin/env python3
"""Builds the README's animated SVGs into this folder. Standard library only.

Every animation is CSS inside the SVG, so it plays on GitHub: no scripts, no web fonts,
no external files. The look is the pitch's Buddy style (pitch/buddy.html): cream card,
violet Buddy chip, green = healthy, red = down, violet = Buddy acting.

Each picture makes one point, with one thing moving at a time, and every label stays
readable in a still frame.

    python3 docs/readme/build.py            # rewrites every *.svg next to this file

Numbers on the art come from the repo, each with its source in README.md.
"""
from math import hypot
from pathlib import Path

OUT = Path(__file__).resolve().parent

P = dict(bg='#FFF8EE', ink='#1F1B2E', mute='#6E6A7C', faint='#C9C1B4', line='#E8DECF',
         v='#6D5DF6', v2='#9A8FFF', vd='#5546E0', vl='#ECE9FF', g='#22B07D', gd='#0F7A54',
         g2='#DDF4EA', r='#F0556A', rd='#C8243C', r2='#FDE3E6', a='#F7B23B', ad='#B9770E',
         a2='#FDF0D5', arm='#F08A4B', b='#2F8FD8', bd='#1F6FB0', b2='#DCEEFB')

FONTS = """
.t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.r{font-family:ui-rounded,"SF Pro Rounded",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.fb{transform-box:fill-box;transform-origin:center}
"""
NB = ' '  # spaces that survive between tspans


def svg(w, h, title, desc, css, body, still):
    """One self-contained SVG. `still` is the time (s) shown when the viewer asks for reduced motion."""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-labelledby="tt dd">
<title id="tt">{title}</title>
<desc id="dd">{desc}</desc>
<style>{FONTS}{css}
@media (prefers-reduced-motion:reduce){{*{{animation-delay:-{still}s!important;animation-play-state:paused!important}}}}
</style>
{body}
</svg>
'''


def kf(name, T, stops):
    """@keyframes from (seconds, css) stops over a cycle of T seconds."""
    return f'@keyframes {name}{{' + ''.join(f'{max(0.0, min(100.0, t / T * 100)):.3f}%{{{c}}}' for t, c in stops) + '}'


def anim(sel, name, T, timing='linear'):
    return f'{sel}{{animation:{name} {T}s {timing} infinite}}'


def card(w, h):
    return f'<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="28" fill="{P["bg"]}" stroke="{P["line"]}" stroke-width="2"/>'


def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def text(x, y, s, size, fill, cls='t', weight=400, anchor='start', extra=''):
    return (f'<text x="{x}" y="{y}" class="{cls}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}" {extra}>{esc(s)}</text>')


def kicker(s, col=None):
    return text(48, 58, s, 16, col or P['vd'], 't', 750, 'start', 'letter-spacing="2.2"')


def buddy(x, y, size, face='content', idp=''):
    """The Buddy mark from pitch/buddy.html. Face parts carry classes so a scene can switch
    expressions: xo/eo open eyes (eo blinks), xh happy eyes, xc closed eyes, xb brows,
    xs smile, xm open mouth, xf frown, xl flat mouth."""
    s = size / 120
    pins = ''
    for p in (-24, 0, 24):
        pins += (f'<rect x="{p - 5.5}" y="-61" width="11" height="20" rx="5.5"/>'
                 f'<rect x="{p - 5.5}" y="41" width="11" height="20" rx="5.5"/>'
                 f'<rect x="-61" y="{p - 5.5}" width="20" height="11" rx="5.5"/>'
                 f'<rect x="41" y="{p - 5.5}" width="20" height="11" rx="5.5"/>')
    show = {'content': {'xo', 'xs'}, 'joy': {'xh', 'xm'}, 'worried': {'xo', 'xb', 'xf'}, 'focus': {'xc', 'xl'}}[face]

    def vis(c):
        return '' if c in show else ' opacity="0"'
    return f'''<g transform="translate({x} {y}) scale({s:.4f})"><g class="{idp}b">
<g fill="{P['vd']}">{pins}</g>
<rect x="-46" y="-46" width="92" height="92" rx="27" fill="{P['v']}"/>
<path d="M-31 -35 H-12" stroke="{P['v2']}" stroke-width="6" stroke-linecap="round"/>
<g fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round">
<g class="{idp}xo"{vis('xo')}><g class="{idp}eo fb" stroke="none" fill="#fff"><ellipse cx="-16" cy="-6" rx="8.5" ry="12.5"/><ellipse cx="16" cy="-6" rx="8.5" ry="12.5"/></g></g>
<path class="{idp}xh"{vis('xh')} d="M-25 -3 Q-16 -17 -7 -3 M7 -3 Q16 -17 25 -3"/>
<path class="{idp}xc"{vis('xc')} d="M-25 -6 Q-16 2 -7 -6 M7 -6 Q16 2 25 -6"/>
<path class="{idp}xb"{vis('xb')} d="M-27 -22 L-11 -28 M11 -28 L27 -22"/>
<path class="{idp}xs"{vis('xs')} d="M-10 18 Q0 26 10 18"/>
<path class="{idp}xm"{vis('xm')} d="M-12 16 Q0 32 12 16 Z" fill="#fff"/>
<path class="{idp}xf"{vis('xf')} d="M-10 25 Q0 17 10 25"/>
<path class="{idp}xl"{vis('xl')} d="M-7 21 H7"/>
</g></g></g>'''


def blink_css(sel, period=4.2, at=0.62):
    """A quick blink once per period, `at` = fraction of the period."""
    name = 'blink' + sel.strip('.')
    return (kf(name, period, [(0, 'transform:scaleY(1)'), (period * at, 'transform:scaleY(1)'), (period * at + .09, 'transform:scaleY(.08)'),
                              (period * at + .2, 'transform:scaleY(1)'), (period, 'transform:scaleY(1)')])
            + f'{sel}{{animation:{name} {period}s ease-in-out infinite}}')


def polyline_len(pts):
    return sum(hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def pts_str(pts):
    return ' '.join(f'{x:.1f},{y:.1f}' for x, y in pts)


def steps_opacity(T, windows, fade=.18):
    """Opacity keyframes that are 1 inside each (start, end) window and 0 outside."""
    st = [(0, 'opacity:0')]
    for a, b in windows:
        st += [(max(a - fade, 0), 'opacity:0'), (a, 'opacity:1'), (b, 'opacity:1'), (min(b + fade, T), 'opacity:0')]
    st.append((T, 'opacity:0'))
    # windows that start at 0 or end at T keep their edge
    if windows and windows[0][0] <= 0:
        st = [(0, 'opacity:1')] + [s for s in st[1:] if not (s[0] <= windows[0][0] and s[1] == 'opacity:0')]
    if windows and windows[-1][1] >= T:
        st = [s for s in st if not (s[0] >= windows[-1][1] and s[1] == 'opacity:0')] + [(T, 'opacity:1')]
    st.sort(key=lambda s: s[0])
    return st


def win(css, cls, T, windows, fade=.18):
    """Show elements of class `cls` only inside the (start, end) windows."""
    css.append(kf(cls, T, steps_opacity(T, windows, fade)))
    css.append(anim(f'.{cls}', cls, T))


def pop(css, cls, T, t):
    """A small bounce at time t, to say "this just happened"."""
    css.append(kf(cls, T, [(0, 'transform:scale(1)'), (t, 'transform:scale(1)'), (t + .18, 'transform:scale(1.15)'),
                           (t + .42, 'transform:scale(1)'), (T, 'transform:scale(1)')]))
    css.append(anim(f'.{cls}', cls, T, 'ease-in-out'))


def travel(css, body, cls, pts, t0, t1, col, T):
    """A dot that runs along the polyline `pts` between t0 and t1 (seconds), then fades."""
    body.append(f'<g class="{cls}"><circle r="12" fill="{col}" opacity=".22"/><circle r="7" fill="{col}"/></g>')
    seg = [hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])]
    tot, acc = sum(seg), 0
    (x0, y0), (xn, yn) = pts[0], pts[-1]
    st = [(0, f'opacity:0;transform:translate({x0}px,{y0}px)'), (t0, f'opacity:0;transform:translate({x0}px,{y0}px)'),
          (t0 + .1, f'opacity:1;transform:translate({x0}px,{y0}px)')]
    for k, (x, y) in enumerate(pts[1:]):
        acc += seg[k]
        st.append((t0 + .1 + (t1 - t0 - .1) * acc / tot, f'opacity:1;transform:translate({x}px,{y}px)'))
    st += [(t1 + .2, f'opacity:0;transform:translate({xn}px,{yn}px)'), (T, f'opacity:0;transform:translate({xn}px,{yn}px)')]
    css.append(kf(cls, T, st))
    css.append(anim(f'.{cls}', cls, T, 'ease-in-out'))


def numbers(x, y, parts, size=23):
    """One centred line of measured numbers: bold number, quiet words, dots between."""
    spans = []
    for k, (n, words) in enumerate(parts):
        if k:
            spans.append(f'<tspan fill="{P["faint"]}" font-weight="800">{NB * 3}·{NB * 3}</tspan>')
        spans.append(f'<tspan fill="{P["ink"]}" font-weight="800">{esc(n)}</tspan>')
        if words:
            spans.append(f'<tspan fill="{P["mute"]}" font-weight="550">{NB}{esc(words)}</tspan>')
    return f'<text x="{x}" y="{y}" class="t" font-size="{size}" text-anchor="middle">{"".join(spans)}</text>'


# --------------------------------------------------------------------------- icons
ICON_ST = f'stroke="{P["ink"]}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"'


def icon(kind):
    """Small illustrations centred on 0,0, about 60 px across."""
    st = ICON_ST
    if kind == 'jam':
        return (f'<path d="M-26 22 H8" {st}/><path d="M-18 22 L-14 10 H0 L4 22" fill="{P["arm"]}" {st}/>'
                f'<path d="M-7 8 L-12 -16 L16 -26" fill="none" stroke="{P["ink"]}" stroke-width="14" stroke-linecap="round" stroke-linejoin="round"/>'
                f'<path d="M-7 8 L-12 -16 L16 -26" fill="none" stroke="{P["arm"]}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>'
                f'<circle cx="19" cy="6" r="12" fill="{P["r"]}"/><path d="M19 0 V7" stroke="#fff" stroke-width="4" stroke-linecap="round"/>'
                f'<circle cx="19" cy="12.5" r="2.4" fill="#fff"/>')
    if kind == 'agent':
        return (f'<path d="M-26 -20 H26 A9 9 0 0 1 35 -11 V9 A9 9 0 0 1 26 18 H-2 L-16 30 V18 H-26 A9 9 0 0 1 -35 9 V-11 A9 9 0 0 1 -26 -20 Z" fill="#fff" {st}/>'
                f'<circle cx="-13" cy="-1" r="4.5" fill="{P["v"]}"/><circle cy="-1" r="4.5" fill="{P["v"]}"/><circle cx="13" cy="-1" r="4.5" fill="{P["v"]}"/>')
    if kind == 'mail':
        return (f'<rect x="-30" y="-20" width="60" height="42" rx="8" fill="#fff" {st}/><path d="M-24 -13 L0 5 L24 -13" fill="none" {st}/>'
                f'<rect x="12" y="8" width="22" height="18" rx="5" fill="{P["a"]}" {st}/>')
    if kind == 'click':
        return (f'<rect x="-34" y="-20" width="58" height="30" rx="15" fill="{P["v"]}" {st}/>'
                f'<path d="M-21 -5 L-14 2 L-1 -11" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>'
                f'<path d="M10 2 L10 30 L17 23 L22 34 L28 31 L23 20 L33 19 Z" fill="#fff" {st}/>')
    if kind == 'robotx':
        return (buddy(-2, 2, 66, 'worried') + f'<circle cx="24" cy="-24" r="13" fill="{P["r"]}" stroke="#fff" stroke-width="3"/>'
                '<path d="M19.5 -28.5 L28.5 -19.5 M28.5 -28.5 L19.5 -19.5" stroke="#fff" stroke-width="3.5" stroke-linecap="round"/>')
    if kind == 'gate':
        return (f'<path d="M0 -32 L27 -21 V0 C27 18 14 29 0 35 C-14 29 -27 18 -27 0 V-21 Z" fill="#fff" {st}/>'
                f'<path d="M-11 1 L-3 9 L12 -7" fill="none" stroke="{P["g"]}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>')
    if kind == 'fleet':
        return ''.join(buddy(dx, dy, 32, 'joy') for dx, dy in ((-19, -13), (19, -13), (0, 19)))
    raise ValueError(kind)


def steps_row(css, body, T, steps, y=150, xs=(210, 470, 730, 990), r=56, t0=.5, dt=1.6, hold=8.8):
    """Four icon circles joined by arrows. Each lights up in turn and stays lit until `hold`."""
    for i, (ic, l1, l2, col, light) in enumerate(steps):
        x = xs[i]
        if i < len(steps) - 1:
            a0, a1 = x + r + 16, xs[i + 1] - r - 16
            body.append(f'<path d="M{a0} {y} H{a1}" stroke="{P["faint"]}" stroke-width="4" stroke-linecap="round"/>'
                        f'<path d="M{a1 - 9} {y - 8} L{a1} {y} L{a1 - 9} {y + 8}" fill="none" stroke="{P["faint"]}" stroke-width="4" '
                        'stroke-linecap="round" stroke-linejoin="round"/>')
        body.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="#FFFFFF" stroke="{P["line"]}" stroke-width="2.5"/>')
        body.append(f'<circle class="on{i}" cx="{x}" cy="{y}" r="{r}" fill="{light}" stroke="{col}" stroke-width="4"/>')
        ta = t0 + i * dt
        win(css, f'on{i}', T, [(ta, hold)], .3)
        body.append(f'<g transform="translate({x} {y})"><g class="ic{i} fb">{icon(ic)}</g></g>')
        pop(css, f'ic{i}', T, ta)
        body.append(text(x, y + r + 42, l1, 22, P['ink'], 'r', 750, 'middle'))
        body.append(text(x, y + r + 70, l2, 20, P['mute'], 't', 550, 'middle'))


# --------------------------------------------------------------------------- the pictures
def hero():
    """Buddy and a heartbeat: running, a fault, Buddy heals it, running again."""
    W, H, T = 1200, 380, 10.0
    css, body = [], []
    base, amp = 300, .75
    beat = [(0, 0), (14, 0), (20, -8), (26, 0), (34, 0), (38, 6), (44, -58), (50, 18), (55, 0), (66, 0), (76, -14), (86, 0), (100, 0)]

    def beats(x0, n, tail=0):
        pts = []
        for i in range(n):
            for dx, dy in beat:
                p = (x0 + i * 100 + dx, base + dy * amp)
                if not pts or pts[-1] != p:
                    pts.append(p)
        if tail:
            pts.append((pts[-1][0] + tail, base))
        return pts

    segs = [(beats(344, 3), 0.0, 2.6, P['g']),
            ([(644, base), (790, base)], 2.6, 4.2, P['r']),
            ([(790, base), (806, base), (812, base - 26), (818, base + 18), (824, base - 10), (830, base), (850, base)], 4.2, 5.2, P['v']),
            (beats(850, 2, tail=80), 5.2, 7.6, P['g'])]
    END, OFF = 9.2, 9.6
    pen = []
    for i, (pts, t0, t1, col) in enumerate(segs):
        L = polyline_len(pts)
        css.append(kf(f'sg{i}', T, [(0, f'stroke-dashoffset:{L:.1f};opacity:1'), (t0, f'stroke-dashoffset:{L:.1f};opacity:1'),
                                     (t1, 'stroke-dashoffset:0;opacity:1'), (END, 'stroke-dashoffset:0;opacity:1'),
                                     (OFF, 'stroke-dashoffset:0;opacity:0'), (T, f'stroke-dashoffset:{L:.1f};opacity:0')]))
        css.append(anim(f'.sg{i}', f'sg{i}', T))
        body.append(f'<polyline class="sg{i}" points="{pts_str(pts)}" fill="none" stroke="{col}" stroke-width="4.5" '
                    f'stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="{L:.1f} {L + 20:.1f}"/>')
        acc = 0
        for j, p in enumerate(pts):
            if j:
                acc += hypot(p[0] - pts[j - 1][0], p[1] - pts[j - 1][1])
            pen.append((t0 + (t1 - t0) * acc / L, p, col))
    last = pen[-1][1]
    css.append(kf('pen', T, [(t, f'transform:translate({x:.1f}px,{y:.1f}px)') for t, (x, y), _ in pen]
                  + [(T, f'transform:translate({last[0]:.1f}px,{last[1]:.1f}px)')]))
    css.append(kf('penc', T, [(t, f'fill:{c}') for t, _, c in pen] + [(T, f'fill:{P["g"]}')]))
    css.append(kf('peno', T, [(0, 'opacity:1'), (7.6, 'opacity:1'), (8.0, 'opacity:0'), (T - .05, 'opacity:0'), (T, 'opacity:1')]))
    css.append(anim('.pen', 'pen', T) + anim('.penc', 'penc', T) + anim('.peno', 'peno', T))
    body.append('<g class="pen"><g class="peno"><circle r="12" class="penc" opacity=".22"/><circle r="6" class="penc"/></g></g>')
    # one status word under the end of the line
    words = [('running', P['gd'], [(0, 2.6), (9.8, T)]), ('fault: the arm stopped', P['rd'], [(2.6, 4.2)]),
             ('healing itself', P['vd'], [(4.2, 5.2)]), ('running again, 0 humans', P['gd'], [(5.2, 9.4)])]
    for i, (s, col, w) in enumerate(words):
        body.append(f'<g class="st{i}">' + text(1130, 352, s, 19, col, 't', 650, 'end') + '</g>')
        win(css, f'st{i}', T, w, .15)
    # Buddy's face follows the line
    face = {'xo': [(0, 4.2), (9.6, T)], 'xs': [(0, 2.6), (9.6, T)], 'xb': [(2.6, 4.2)], 'xf': [(2.6, 4.2)],
            'xc': [(4.2, 5.2)], 'xl': [(4.2, 5.2)], 'xh': [(5.2, 9.6)], 'xm': [(5.2, 9.6)]}
    for c, w in face.items():
        win(css, f'h{c}', T, w, .1)
    css.append(blink_css('.heo', 4.0, .5))
    body.insert(0, f'<ellipse cx="176" cy="306" rx="70" ry="9" fill="{P["ink"]}" opacity=".07"/>' + buddy(176, 188, 196, 'content', 'h'))
    words_top = (text(338, 162, 'Hardware Buddy', 86, P['ink'], 'r', 800, 'start', 'letter-spacing="-1.5"')
                 + text(342, 212, 'Robots that fix themselves.', 30, P['mute'], 't', 500))
    return svg(W, H, 'Hardware Buddy', 'Hardware Buddy: robots that fix themselves. A heartbeat line runs green, goes flat and red '
               'when the arm stops, Buddy heals it, and it runs green again with 0 humans.',
               '\n'.join(css), card(W, H) + words_top + ''.join(body), 8.5)


def video():
    """The demo video's poster: one play button."""
    W, H = 1200, 520
    css, body = [], []
    body.append(f'<defs><radialGradient id="vg" cx="50%" cy="44%" r="64%"><stop offset="0" stop-color="#3A2F7A"/>'
                f'<stop offset=".6" stop-color="#231E3C"/><stop offset="1" stop-color="{P["ink"]}"/></radialGradient></defs>'
                f'<rect width="{W}" height="{H}" rx="28" fill="url(#vg)"/>')
    body.append(f'<g transform="translate(70 70) scale(.42)">{buddy(0, 0, 120, "content", "vb")}</g>'
                + text(112, 80, 'Hardware Buddy', 26, '#FFF8EE', 'r', 800))
    css.append(blink_css('.vbeo', 3.4, .6))
    cx, cy = 600, 236
    body.append(f'<circle class="ring fb" cx="{cx}" cy="{cy}" r="74" fill="none" stroke="{P["v2"]}" stroke-width="3"/>')
    css.append(kf('ring', 2.8, [(0, 'opacity:.7;transform:scale(1)'), (2.8, 'opacity:0;transform:scale(1.8)')]))
    css.append(anim('.ring', 'ring', 2.8, 'ease-out'))
    body.append(f'<circle cx="{cx}" cy="{cy}" r="74" fill="{P["v"]}"/>'
                f'<path d="M{cx - 16} {cy - 32} L{cx + 30} {cy} L{cx - 16} {cy + 32} Z" fill="#FFFFFF" stroke="#FFFFFF" stroke-width="10" stroke-linejoin="round"/>')
    body.append(text(cx, 392, 'Watch the demo', 52, '#FFF8EE', 'r', 800, 'middle', 'letter-spacing="-.5"'))
    body.append(text(cx, 438, 'Opens on Google Drive', 22, '#C9C4DD', 't', 550, 'middle'))
    return svg(W, H, 'Watch the Hardware Buddy demo', 'Video poster with a play button. The demo video opens on Google Drive.',
               '\n'.join(css), ''.join(body), 1.0)


def heal():
    """The real bench run as a timeline, replayed at real-time proportions."""
    W, H, T = 1200, 250, 11.0
    css, body = [], [kicker('REAL BENCH RUN · NOV 8, 12:05 AM')]
    x = 1152
    for s in ('nobody touching it', 'no internet'):
        w = len(s) * 9.4 + 36
        x -= w
        body.append(f'<rect x="{x:.0f}" y="34" width="{w:.0f}" height="36" rx="18" fill="#FFFFFF" stroke="{P["line"]}" stroke-width="2"/>'
                    + text(x + w / 2, 58, s, 16, P['ink'], 't', 650, 'middle'))
        x -= 12
    x0, x1, y = 120, 1080, 162

    def X(s):
        return x0 + (x1 - x0) * s / 141

    def at(s):  # animation time for real second s
        return .6 + 7.2 * s / 141
    body.append(f'<defs><linearGradient id="hg" gradientUnits="userSpaceOnUse" x1="{x0}" x2="{x1}" y1="0" y2="0">'
                f'<stop offset="0" stop-color="{P["r"]}"/><stop offset=".22" stop-color="{P["v"]}"/>'
                f'<stop offset=".5" stop-color="{P["g"]}"/><stop offset="1" stop-color="{P["g"]}"/></linearGradient></defs>')
    body.append(f'<rect x="{x0}" y="{y - 5}" width="{x1 - x0}" height="10" rx="5" fill="{P["line"]}"/>')
    body.append(f'<rect class="hf" x="{x0}" y="{y - 5}" width="{x1 - x0}" height="10" rx="5" fill="url(#hg)"/>')
    HOLD, OFF = 10.2, 10.6
    css.append(kf('hf', T, [(0, 'transform:scaleX(0);opacity:1'), (at(0), 'transform:scaleX(0);opacity:1'),
                            (at(141), 'transform:scaleX(1);opacity:1'), (HOLD, 'transform:scaleX(1);opacity:1'),
                            (OFF, 'transform:scaleX(1);opacity:0'), (T, 'transform:scaleX(0);opacity:0')]))
    css.append('.hf{animation:hf 11s linear infinite;transform-box:fill-box;transform-origin:left center}')
    stops = [(0, 'Bad firmware', P['r']), (46, 'Rolled back', P['v']), (72, 'Job runs clean', P['g']), (141, 'Root cause found', P['g'])]
    for i, (s, lab, col) in enumerate(stops):
        xs = X(s)
        body.append(f'<circle cx="{xs:.1f}" cy="{y}" r="13" fill="#FFFFFF" stroke="{P["faint"]}" stroke-width="3"/>')
        body.append(f'<g class="hs{i}"><g class="hp{i} fb"><circle cx="{xs:.1f}" cy="{y}" r="13" fill="{col}" stroke="#FFFFFF" stroke-width="3"/></g></g>')
        win(css, f'hs{i}', T, [(at(s) + .05, HOLD)], .3)
        pop(css, f'hp{i}', T, at(s))
        body.append(text(xs, y - 30, f'{s} s', 26, P['ink'], 'r', 800, 'middle'))
        body.append(text(xs, y + 48, lab, 21, P['mute'], 't', 600, 'middle'))
    return svg(W, H, 'The arm heals its own firmware, offline',
               'A real bench run with no internet and nobody touching it. At 0 s bad firmware makes the arm fail its job. At 46 s the '
               'local healer has rolled the firmware back. At 72 s the job runs clean. At 141 s it has found the root cause.',
               '\n'.join(css), card(W, H) + ''.join(body), 9.0)


def technician():
    """A hardware fault, taken by a Naïve agent, ending at one human click."""
    W, H, T = 1200, 360, 10.0
    css, body = [], [kicker('LIVE RUN · NOV 8, 8:44 AM', P['ad'])]
    steps_row(css, body, T, [('jam', 'Arm jams,', 'the healer parks it', P['r'], P['r2']),
                             ('agent', 'Naïve agent', 'diagnoses it', P['v'], P['vl']),
                             ('mail', 'Emails a tech,', 'stages the part', P['b'], P['b2']),
                             ('click', 'A person', 'approves the buy', P['a'], P['a2'])])
    body.append(numbers(600, 328, [('38 s', 'to a case'), ('$0.08', 'per case'), ('1', 'human click')]))
    return svg(W, H, 'When it needs hands, a Naïve agent takes the case',
               'A live run. The arm jams and the healer parks it. A Naïve agent diagnoses it, plain code emails a technician and stages '
               'the spare part, and a person approves the purchase. 38 s to a case, $0.08 per case, 1 human click.',
               '\n'.join(css), card(W, H) + ''.join(body), 8.0)


def fleet():
    """One robot's failed fix becomes a skill, tested by the gate, promoted to the fleet."""
    W, H, T = 1200, 360, 10.0
    css, body = [], [kicker('LIVE RUN · NOV 8, 10:04 AM', P['bd'])]
    steps_row(css, body, T, [('robotx', 'A fix fails', 'on one robot', P['r'], P['r2']),
                             ('agent', 'Naïve agent', 'writes a skill', P['v'], P['vl']),
                             ('gate', 'The gate', 'replays and canaries it', P['b'], P['b2']),
                             ('fleet', 'Promoted', 'to the fleet', P['g'], P['g2'])])
    body.append(numbers(600, 328, [('57 s', 'from agent session to fleet'), ('0', 'human clicks')]))
    return svg(W, H, 'Across the fleet, every fault happens once',
               'A live run. A fix fails on one robot. A Naïve agent writes a skill. The gate replays it against past incidents and '
               'tries it on a simulated canary, then promotes it to the fleet: 57 s from the agent session, 0 human clicks.',
               '\n'.join(css), card(W, H) + ''.join(body), 8.0)


def stats():
    """Four measured numbers. Still on purpose."""
    W, H = 1200, 216
    tiles = [('141 s', P['g'], 'for a real arm to heal', 'its own firmware, offline'),
             ('10 / 12', P['v'], 'planted-fault runs fixed', 'by a 3.3 GB local model'),
             ('57 s', P['b'], 'from a Naïve agent session', 'to a fleet-wide fix'),
             ('$0.08', P['ad'], 'for a Naïve agent session', 'that diagnoses a fault')]
    w, g = 270, 20
    body = []
    for i, (n, col, l1, l2) in enumerate(tiles):
        x, y = 30 + i * (w + g), 24
        body.append(f'<rect x="{x}" y="{y}" width="{w}" height="168" rx="20" fill="#FFFFFF" stroke="{P["line"]}" stroke-width="2"/>'
                    + text(x + 26, y + 72, n, 54, col, 'r', 800, 'start', 'letter-spacing="-1"')
                    + f'<rect x="{x + 28}" y="{y + 88}" width="44" height="5" rx="2.5" fill="{col}"/>'
                    + text(x + 28, y + 124, l1, 17, P['mute'], 't', 550) + text(x + 28, y + 147, l2, 17, P['mute'], 't', 550))
    return svg(W, H, 'Hardware Buddy in numbers',
               '141 s for a real arm to heal its own firmware, offline. 10 of 12 planted-fault runs fixed by a 3.3 GB local model. '
               '57 s from a Naïve agent session to a fleet-wide fix. $0.08 for a Naïve agent session that diagnoses a fault.',
               '', card(W, H) + ''.join(body), 0)


def stack():
    """Three layers. Reports go up on the left, fixes come down on the right."""
    W, H, T = 1200, 540, 8.0
    css, body = [], []
    bands = [(24, P['a2'], P['ad'], P['a'], "NAÏVE · THE COMPANY'S AGENTS",
              [('Fleet doctor', 'turns failures into skills'), ('Technician', 'emails, stages parts'), ('Board + Skills', 'job cards, playbooks')]),
             (208, P['b2'], P['bd'], P['b'], 'ROMULUS · OUR CLOUD',
              [('Incidents', 'every heal, labelled'), ('The gate', 'replay, then a sim canary'), ('Work orders', 'faults that need hands')]),
             (392, P['vl'], P['vd'], P['v'], 'ON THE BENCH · OFFLINE',
              [('Sensor pod + healer', 'qwen3.5:4b, 38 tools'), ('SO-101 arm', 'a watchdog parks it'), ('Hermes', 'runs Naïve job cards')])]
    bw, gap, bx0 = 340, 30, 60
    for y, fill, col, acc, lab, boxes in bands:
        body.append(f'<rect x="30" y="{y}" width="1140" height="124" rx="22" fill="{fill}"/>' + text(56, y + 30, lab, 14, col, 't', 800, 'start', 'letter-spacing="1.8"'))
        for k, (t1, t2) in enumerate(boxes):
            x = bx0 + k * (bw + gap)
            body.append(f'<rect x="{x}" y="{y + 44}" width="{bw}" height="64" rx="14" fill="#FFFFFF" stroke="{P["line"]}" stroke-width="2"/>'
                        f'<rect x="{x}" y="{y + 56}" width="5" height="40" rx="2.5" fill="{acc}"/>'
                        + text(x + 22, y + 71, t1, 20, P['ink'], 'r', 800) + text(x + 22, y + 95, t2, 16, P['mute'], 't', 550))
    UP, DN = 420, 780
    gaps = [(148, 208, 'work orders, failure clusters', 'skills, rules'), (332, 392, 'incidents, heal runs', 'new versions')]
    for ya, yb, up, dn in gaps:
        for xa, y0, y1 in ((UP, yb - 9, ya + 9), (DN, ya + 9, yb - 9)):
            d = -1 if y1 < y0 else 1
            body.append(f'<path d="M{xa} {y0} V{y1}" stroke="{P["faint"]}" stroke-width="4" stroke-linecap="round"/>'
                        f'<path d="M{xa - 8} {y1 - 9 * d} L{xa} {y1} L{xa + 8} {y1 - 9 * d}" fill="none" stroke="{P["faint"]}" '
                        'stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>')
        mid = (ya + yb) / 2 + 6
        body.append(text(UP - 18, mid, up, 17, P['mute'], 't', 650, 'end') + text(DN + 18, mid, dn, 17, P['mute'], 't', 650, 'start'))
    for i, (pts, t0, t1, col) in enumerate([([(UP, 384), (UP, 340)], .3, 1.0, P['b']), ([(UP, 200), (UP, 156)], 1.3, 2.0, P['b']),
                                            ([(DN, 156), (DN, 200)], 3.3, 4.0, P['g']), ([(DN, 340), (DN, 384)], 4.3, 5.0, P['g'])]):
        travel(css, body, f'd{i}', pts, t0, t1, col, T)
    return svg(W, H, 'The stack, from servo to company',
               'Three layers. On the bench, offline: the sensor pod and its local healer (qwen3.5:4b, 38 tools), the SO-101 arm with '
               'a watchdog, and Hermes running Naïve job cards. Incidents and heal runs go up to Romulus, our cloud, which keeps the '
               'labelled incidents, the gate and the work orders. Work orders and failure clusters go up to Naïve agents: the fleet '
               'doctor, the technician, and the board with Naïve Skills. Skills and rules come back down through the gate, and new '
               'versions go back to the bench.',
               '\n'.join(css), card(W, H) + ''.join(body), 4.6)


def mark():
    """The Buddy chip on its own, blinking, on a transparent background."""
    return svg(176, 176, 'Hardware Buddy', 'The Buddy chip: a violet chip with pins and two eyes, blinking.',
               blink_css('.fmeo', 3.8, .55), buddy(88, 90, 140, 'content', 'fm'), 1.0)


BUILDERS = {'hero.svg': hero, 'buddy.svg': mark, 'video.svg': video, 'heal.svg': heal,
            'technician.svg': technician, 'fleet.svg': fleet, 'stats.svg': stats, 'stack.svg': stack}

if __name__ == '__main__':
    for name, fn in BUILDERS.items():
        s = fn()
        (OUT / name).write_text(s)
        print('wrote', OUT / name, f'{len(s) / 1024:.1f} KB')
