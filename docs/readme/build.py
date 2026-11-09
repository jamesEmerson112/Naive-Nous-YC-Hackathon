#!/usr/bin/env python3
"""Builds the README's animated SVGs into this folder. Standard library only.

Every animation is CSS inside the SVG, so it plays on GitHub: no scripts, no web fonts,
no external files. The look is the pitch's Buddy style (pitch/buddy.html): cream card,
violet Buddy chip, green = healthy, red = down, violet = Buddy acting.

    python3 docs/readme/build.py            # rewrites every *.svg next to this file

Numbers on the art come from the repo, each with its source in README.md.
"""
from math import hypot
from pathlib import Path

OUT = Path(__file__).resolve().parent

P = dict(bg='#FFF8EE', ink='#1F1B2E', mute='#6E6A7C', faint='#C9C1B4', line='#E8DECF',
         v='#6D5DF6', v2='#9A8FFF', vd='#5546E0', vl='#ECE9FF', g='#22B07D', gd='#0F7A54',
         g2='#DDF4EA', r='#F0556A', rd='#C8243C', r2='#FDE3E6', a='#F7B23B', ad='#B9770E',
         a2='#FDF0D5', kraft='#F3E3C7', sky='#D3E8F6', arm='#F08A4B', armd='#C9612A',
         term='#1A1726', term2='#2A2540')

FONTS = """
.t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.r{font-family:ui-rounded,"SF Pro Rounded",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.m{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace}
.fb{transform-box:fill-box;transform-origin:center}
"""


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


def kf(name, T, stops, ease=None):
    """@keyframes from (seconds, css) stops over a cycle of T seconds."""
    out = []
    for t, css in stops:
        pct = max(0.0, min(100.0, t / T * 100))
        e = f'animation-timing-function:{ease};' if ease else ''
        out.append(f'{pct:.3f}%{{{css};{e}}}')
    return f'@keyframes {name}{{{"".join(out)}}}'


def anim(sel, name, T, extra='', timing='linear'):
    return f'{sel}{{animation:{name} {T}s {timing} infinite;{extra}}}'


def card(w, h, fill=None, dots=True):
    fill = fill or P['bg']
    pat = (f'<pattern id="dots" width="26" height="26" patternUnits="userSpaceOnUse">'
           f'<circle cx="3" cy="3" r="1.5" fill="{P["line"]}"/></pattern>') if dots else ''
    s = f'<defs>{pat}</defs><rect x="1" y="1" width="{w-2}" height="{h-2}" rx="28" fill="{fill}" stroke="{P["line"]}" stroke-width="2"/>'
    if dots:
        s += f'<rect x="1" y="1" width="{w-2}" height="{h-2}" rx="28" fill="url(#dots)" opacity=".55"/>'
    return s


def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def text(x, y, s, size, fill, cls='t', weight=400, anchor='start', extra=''):
    return (f'<text x="{x}" y="{y}" class="{cls}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}" {extra}>{esc(s)}</text>')


def buddy(x, y, size, face='content', idp=''):
    """The Buddy mark from pitch/buddy.html. Face parts carry classes so a scene can switch
    expressions: xo/eo open eyes (eo blinks), xh happy eyes, xc closed eyes, xb brows,
    xs smile, xm open mouth, xf frown, xl flat mouth."""
    s = size / 120
    pins = ''
    for p in (-24, 0, 24):
        pins += (f'<rect x="{p-5.5}" y="-61" width="11" height="20" rx="5.5"/>'
                 f'<rect x="{p-5.5}" y="41" width="11" height="20" rx="5.5"/>'
                 f'<rect x="-61" y="{p-5.5}" width="20" height="11" rx="5.5"/>'
                 f'<rect x="41" y="{p-5.5}" width="20" height="11" rx="5.5"/>')
    show = {'content': {'xo', 'xs'}, 'joy': {'xh', 'xm'}, 'worried': {'xo', 'xb', 'xf'},
            'focus': {'xc', 'xl'}}[face]
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
    return (kf(f'blink{sel.strip(".")}', period, [(0, 'transform:scaleY(1)'), (period * at, 'transform:scaleY(1)'),
            (period * at + .09, 'transform:scaleY(.08)'), (period * at + .2, 'transform:scaleY(1)'), (period, 'transform:scaleY(1)')])
            + f'{sel}{{animation:blink{sel.strip(".")} {period}s ease-in-out infinite}}')


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


# --------------------------------------------------------------------------- hero
def hero():
    W, H, T = 1200, 430, 12.0
    base = 352
    beat = [(0, 0), (14, 0), (20, -8), (26, 0), (34, 0), (38, 6), (44, -58), (50, 18), (55, 0), (66, 0),
            (76, -14), (86, 0), (100, 0)]

    def beats(x0, n, tail=0):
        pts = []
        for i in range(n):
            for dx, dy in beat:
                p = (x0 + i * 100 + dx, base + dy)
                if not pts or pts[-1] != p:
                    pts.append(p)
        if tail:
            pts.append((pts[-1][0] + tail, base))
        return pts

    segA = beats(60, 4)
    segB = [(460, base), (700, base)]
    segC = [(700, base), (722, base), (728, base - 34), (735, base + 24), (742, base - 14), (748, base), (800, base)]
    segD = beats(800, 3, tail=40)
    # (segment, start, end, colour)
    segs = [(segA, 0.0, 4.0, P['g']), (segB, 4.0, 6.3, P['r']), (segC, 6.3, 7.6, P['v']), (segD, 7.6, 10.6, P['g'])]
    css, body = [], []
    pen = []
    for i, (pts, a, b, col) in enumerate(segs):
        L = polyline_len(pts)
        css.append(kf(f'sg{i}', T, [(0, f'stroke-dashoffset:{L:.1f};opacity:1'), (a, f'stroke-dashoffset:{L:.1f};opacity:1'),
                                     (b, 'stroke-dashoffset:0;opacity:1'), (11.55, 'stroke-dashoffset:0;opacity:1'),
                                     (11.95, 'stroke-dashoffset:0;opacity:0'), (T, f'stroke-dashoffset:{L:.1f};opacity:0')]))
        css.append(anim(f'.sg{i}', f'sg{i}', T))
        glow = f'<polyline points="{pts_str(pts)}" fill="none" stroke="{col}" stroke-width="10" stroke-linecap="round" stroke-linejoin="round" opacity=".16" class="sg{i}" stroke-dasharray="{L:.1f} {L+20:.1f}"/>'
        body.append(glow + f'<polyline points="{pts_str(pts)}" fill="none" stroke="{col}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" class="sg{i}" stroke-dasharray="{L:.1f} {L+20:.1f}"/>')
        acc = 0
        for j, p in enumerate(pts):
            if j:
                acc += hypot(p[0] - pts[j - 1][0], p[1] - pts[j - 1][1])
            t = a + (b - a) * acc / L
            pen.append((t, p, col))
    # pen dot follows the trace vertex by vertex
    pst, fst = [], []
    for t, (x, y), col in pen:
        pst.append((t, f'transform:translate({x:.1f}px,{y:.1f}px)'))
        fst.append((t, f'fill:{col}'))
    last = pen[-1][1]
    pst += [(T, f'transform:translate({last[0]:.1f}px,{last[1]:.1f}px)')]
    css.append(kf('pen', T, pst))
    css.append(kf('penc', T, fst + [(T, f'fill:{P["g"]}')]))
    css.append(kf('peno', T, [(0, 'opacity:1'), (10.6, 'opacity:1'), (11.2, 'opacity:0'), (T, 'opacity:0')]))
    css.append('.pen{animation:pen 12s linear infinite}.penc{animation:penc 12s linear infinite}.peno{animation:peno 12s linear infinite}')
    body.append(f'<g class="pen"><g class="peno"><circle r="13" class="penc" opacity=".22"/><circle r="6.5" class="penc"/></g></g>')

    # labels under the trace
    labels = [(260, base + 44, 'running', P['gd'], 0.2, 11.6), (580, base + 44, 'unplanned downtime', P['rd'], 4.2, 11.6),
              (752, base - 50, 'healed on the device', P['vd'], 6.5, 11.6), (972, base + 44, 'running again · 0 humans', P['gd'], 7.9, 11.6)]
    for i, (x, y, s, col, a, b) in enumerate(labels):
        css.append(kf(f'lb{i}', T, [(0, 'opacity:0'), (a, 'opacity:0'), (a + .35, 'opacity:1'), (b, 'opacity:1'), (b + .3, 'opacity:0'), (T, 'opacity:0')]))
        css.append(anim(f'.lb{i}', f'lb{i}', T))
        body.append(f'<g class="lb{i}">' + text(x, y, s, 17, col, 't', 650, 'middle') + '</g>')

    # status pill
    pills = [('RUNNING', P['g'], P['g2'], P['gd'], [(0, 4.0), (7.6, T)]), ('DOWN', P['r'], P['r2'], P['rd'], [(4.0, 6.3)]),
             ('HEALING', P['v'], P['vl'], P['vd'], [(6.3, 7.6)])]
    for i, (s, dot, bgc, fg, wins) in enumerate(pills):
        css.append(kf(f'pl{i}', T, steps_opacity(T, wins, .12)))
        css.append(anim(f'.pl{i}', f'pl{i}', T))
        body.append(f'''<g class="pl{i}" transform="translate(1004 40)"><rect width="156" height="40" rx="20" fill="{bgc}" stroke="{dot}" stroke-width="2"/>
<circle cx="24" cy="20" r="6" fill="{dot}"/>{text(88, 26.5, s, 16, fg, 't', 750, 'middle', 'letter-spacing="1.6"')}</g>''')

    # Buddy: expressions per phase
    face = {'xo': [(0, 4.0)], 'xs': [(0, 4.0)], 'xb': [(4.0, 6.3)], 'xf': [(4.0, 6.3)], 'xc': [(6.3, 7.6)],
            'xl': [(6.3, 7.6)], 'xh': [(7.6, T)], 'xm': [(7.6, T)]}
    face['xo'] = [(0, 6.3)]
    for c, wins in face.items():
        css.append(kf(f'h{c}', T, steps_opacity(T, wins, .1)))
        css.append(anim(f'.h{c}', f'h{c}', T))
    css.append(blink_css('.heo', 4.0, .5))
    css.append(kf('hjump', T, [(0, 'transform:translateY(0)'), (7.6, 'transform:translateY(0)'), (7.85, 'transform:translateY(-22px)'),
                               (8.15, 'transform:translateY(0)'), (8.3, 'transform:translateY(-6px)'), (8.45, 'transform:translateY(0)'), (T, 'transform:translateY(0)')]))
    css.append(kf('hbob', 3.0, [(0, 'transform:translateY(0)'), (1.5, 'transform:translateY(-6px)'), (3.0, 'transform:translateY(0)')]))
    css.append('.hjump{animation:hjump 12s ease-in-out infinite}.hbob{animation:hbob 3s ease-in-out infinite}')
    css.append(kf('hshade', 3.0, [(0, 'transform:scale(1)'), (1.5, 'transform:scale(.88)'), (3.0, 'transform:scale(1)')]))
    css.append('.hshade{animation:hshade 3s ease-in-out infinite}')
    body.insert(0, f'<ellipse cx="190" cy="292" rx="74" ry="10" fill="{P["ink"]}" opacity=".08" class="hshade fb"/>'
                   f'<g class="hjump"><g class="hbob">{buddy(190, 168, 200, "content", "h")}</g></g>')
    # sparkles while healing
    for i, (x, y, col, t0) in enumerate([(92, 78, P['a'], 6.35), (296, 96, P['v'], 6.6), (110, 250, P['v2'], 6.9), (285, 238, P['a'], 7.15)]):
        css.append(kf(f'sp{i}', T, [(0, 'transform:scale(0) rotate(0deg)'), (t0, 'transform:scale(0) rotate(0deg)'),
                                     (t0 + .3, 'transform:scale(1) rotate(45deg)'), (t0 + .75, 'transform:scale(0) rotate(90deg)'), (T, 'transform:scale(0) rotate(90deg)')]))
        css.append(anim(f'.sp{i}', f'sp{i}', T, timing='ease-out'))
        body.append(f'<path class="sp{i} fb" d="M{x} {y-14} Q{x+2} {y-2} {x+14} {y} Q{x+2} {y+2} {x} {y+14} Q{x-2} {y+2} {x-14} {y} Q{x-2} {y-2} {x} {y-14} Z" fill="{col}"/>')

    words = (text(352, 110, 'NAÏVE AUTONOMOUS COMPANY HACKATHON · Y COMBINATOR · NOV 2026', 15, P['vd'], 't', 750, 'start', 'letter-spacing="2.2"')
             + text(348, 196, 'Hardware Buddy', 86, P['ink'], 'r', 800, 'start', 'letter-spacing="-1.5"')
             + text(352, 248, 'Robots that fix themselves, from a company run by AI agents.', 25, P['mute'], 't', 500))
    css_s = '\n'.join(css)
    return svg(W, H, 'Hardware Buddy', 'Hardware Buddy: robots that fix themselves, from a company run by AI agents. '
               'A heartbeat line runs green, flatlines red, the Buddy chip heals it, and it runs green again with 0 humans.',
               css_s, card(W, H) + words + ''.join(body), 9.4)


def win(css, cls, T, windows, fade=.18):
    """Show elements of class `cls` only inside the (start, end) windows."""
    css.append(kf(cls, T, steps_opacity(T, windows, fade)))
    css.append(anim(f'.{cls}', cls, T))


def pop_in(css, cls, T, t0, t1=None, dx=-12, dy=0, dur=.35):
    """Slide-and-fade in at t0, out at t1 (default: just before the cycle ends)."""
    t1 = T - .5 if t1 is None else t1
    css.append(kf(cls, T, [(0, f'opacity:0;transform:translate({dx}px,{dy}px)'), (t0, f'opacity:0;transform:translate({dx}px,{dy}px)'),
                           (t0 + dur, 'opacity:1;transform:translate(0,0)'), (t1, 'opacity:1;transform:translate(0,0)'),
                           (min(t1 + .35, T), 'opacity:0;transform:translate(0,0)'), (T, f'opacity:0;transform:translate({dx}px,{dy}px)')]))
    css.append(anim(f'.{cls}', cls, T, timing='ease-out'))


def so101(prefix, x, y):
    """A side view of the SO-101: shoulder, elbow and wrist groups that CSS rotates (classes {prefix}j1..j3)."""
    o, f = P['ink'], P['arm']
    st = f'stroke="{o}" stroke-width="4" stroke-linejoin="round"'
    return f'''<g transform="translate({x} {y})">
<g class="{prefix}j1">
  <rect x="-6" y="-14" width="152" height="28" rx="14" fill="{f}" {st}/>
  <g transform="translate(140 0)"><g class="{prefix}j2">
    <rect x="-6" y="-12" width="130" height="24" rx="12" fill="{f}" {st}/>
    <g transform="translate(118 0)"><g class="{prefix}j3">
      <rect x="-4" y="-15" width="32" height="30" rx="8" fill="{f}" {st}/>
      <path d="M27 -13 H52 V-5 H35 Z" fill="{f}" {st}/><path d="M27 13 H52 V5 H35 Z" fill="{f}" {st}/>
    </g><circle r="12" fill="{o}"/><circle r="4.5" fill="{f}"/></g>
  </g><circle r="16" fill="{o}"/><circle r="6" fill="{f}"/></g>
</g><circle r="19" fill="{o}"/><circle r="7.5" fill="{f}"/>
</g>'''


def joint_css(css, prefix, T, frames):
    """frames: list of (t, (j1, j2, j3), timing). Writes rotate keyframes for each joint."""
    for k in range(3):
        stops = [(t, f'transform:rotate({ang[k]}deg);animation-timing-function:{tm}') for t, ang, tm in frames]
        css.append(kf(f'{prefix}k{k+1}', T, stops))
        css.append(f'.{prefix}j{k+1}{{animation:{prefix}k{k+1} {T}s linear infinite;transform-origin:0 0}}')


def heal():
    W, H, T = 1200, 640, 14.0
    css, body = [], []
    # ---------------- header
    body.append(text(40, 54, 'REAL BENCH RUN · SO-101 ARM + STM32 SENSOR POD · SUN NOV 8, 12:05 AM', 14, P['vd'], 't', 750, 'start', 'letter-spacing="2"'))
    body.append(text(38, 92, 'The arm heals its own firmware, offline', 32, P['ink'], 'r', 800))
    body.append(f'''<g transform="translate(748 50)"><rect width="176" height="40" rx="20" fill="{P['ink']}"/>
<g class="blinkdot"><circle cx="24" cy="20" r="6" fill="{P['faint']}"/></g>{text(98, 26.5, 'NO INTERNET', 15, '#FFFFFF', 't', 750, 'middle', 'letter-spacing="1.6"')}
<path d="M17 27 L31 13" stroke="{P['ink']}" stroke-width="2.5"/></g>
<g transform="translate(936 50)"><rect width="226" height="40" rx="20" fill="#FFFFFF" stroke="{P['line']}" stroke-width="2"/>
{text(22, 26, 'HUMAN TOUCHES', 13, P['mute'], 't', 750, 'start', 'letter-spacing="1.4"')}
<g transform="translate(198 20)"><g class="zero fb">{text(0, 9, '0', 26, P['g'], 'r', 800, 'middle')}</g></g></g>''')
    css.append(kf('zero', T, [(0, 'transform:scale(1)'), (12.0, 'transform:scale(1)'), (12.25, 'transform:scale(1.45)'), (12.6, 'transform:scale(1)'), (T, 'transform:scale(1)')]))
    css.append(anim('.zero', 'zero', T, timing='ease-in-out'))
    css.append(kf('bd', 1.6, [(0, 'opacity:1'), (.8, 'opacity:.25'), (1.6, 'opacity:1')]) + '.blinkdot{animation:bd 1.6s ease-in-out infinite}')

    # ---------------- scene panel
    body.append(f'<rect x="30" y="112" width="440" height="408" rx="22" fill="#FFFDF9" stroke="{P["line"]}" stroke-width="2"/>')
    # aura behind the arm: red while broken, green while the job re-runs
    body.append(f'''<defs><radialGradient id="aR"><stop offset="0" stop-color="{P['r']}" stop-opacity=".34"/><stop offset="1" stop-color="{P['r']}" stop-opacity="0"/></radialGradient>
<radialGradient id="aG"><stop offset="0" stop-color="{P['g']}" stop-opacity=".30"/><stop offset="1" stop-color="{P['g']}" stop-opacity="0"/></radialGradient></defs>
<ellipse class="auR" cx="250" cy="340" rx="200" ry="170" fill="url(#aR)"/><ellipse class="auG" cx="250" cy="340" rx="200" ry="170" fill="url(#aG)"/>''')
    win(css, 'auR', T, [(0, 3.2)], .4)
    win(css, 'auG', T, [(6.8, 9.6)], .4)
    # floor, base, arm
    body.append(f'<path d="M48 490 H452" stroke="{P["faint"]}" stroke-width="3" stroke-linecap="round"/>')
    body.append(f'<path d="M136 490 L160 446 H240 L264 490 Z" fill="{P["arm"]}" stroke="{P["ink"]}" stroke-width="4" stroke-linejoin="round"/>'
                f'<rect x="168" y="436" width="64" height="14" rx="5" fill="{P["armd"]}" stroke="{P["ink"]}" stroke-width="4"/>')
    body.append(so101('a', 200, 432))
    rest, home = (-118, 148, 60), (-72, 82, 40)
    j1s = [-60, -95, -52, -88, -48, -92, -58, -85, -50, -96, -62, -84, -55]
    j2s = [70, 108, 56, 112, 60, 104, 75, 115, 52, 100, 68, 110, 58]
    j3s = [30, 62, 22, 66, 25, 58, 35, 64, 22, 55, 30, 62, 24]
    fr = [(i * .23, (j1s[i], j2s[i], j3s[i]), 'ease-in-out') for i in range(len(j1s))]
    fr += [(3.0, (-60, 70, 30), 'ease-in-out'), (3.55, rest, 'linear'), (6.5, rest, 'ease-in-out'), (6.9, home, 'ease-in-out')]
    t = 6.9
    for k in range(3):
        fr += [(t + .4, (-60, 98, 14), 'ease-in-out'), (t + .8, home, 'ease-in-out')]
        t += .8
    fr += [(9.3, home, 'ease-in-out'), (9.8, rest, 'linear'), (13.55, rest, 'ease-in-out'), (T, (-60, 70, 30), 'linear')]
    joint_css(css, 'a', T, fr)
    # sensor pod (right), facing left, with sonar
    body.append(f'''<rect x="384" y="446" width="72" height="44" rx="9" fill="#FFFFFF" stroke="{P['ink']}" stroke-width="4"/>
<rect x="372" y="452" width="14" height="13" rx="4" fill="{P['sky']}" stroke="{P['ink']}" stroke-width="3"/>
<rect x="372" y="471" width="14" height="13" rx="4" fill="{P['sky']}" stroke="{P['ink']}" stroke-width="3"/>
<g class="v17">{text(420, 474, 'v1.7', 16, P['rd'], 'm', 800, 'middle')}</g><g class="v10">{text(420, 474, 'v1.0', 16, P['gd'], 'm', 800, 'middle')}</g>
{text(420, 510, 'pod firmware', 13, P['mute'], 't', 600, 'middle')}''')
    win(css, 'v17', T, [(0, 5.55)], .08)
    win(css, 'v10', T, [(5.6, 13.6)], .08)
    # flash ripple on the pod at rollback
    body.append(f'<circle class="rip fb" cx="420" cy="468" r="40" fill="none" stroke="{P["g"]}" stroke-width="4"/>')
    css.append(kf('rip', T, [(0, 'opacity:0;transform:scale(.3)'), (5.55, 'opacity:0;transform:scale(.3)'), (5.6, 'opacity:1;transform:scale(.4)'),
                             (6.3, 'opacity:0;transform:scale(1.7)'), (T, 'opacity:0;transform:scale(1.7)')]))
    css.append(anim('.rip', 'rip', T, timing='ease-out'))
    for col, cls, wins in ((P['r'], 'sonR', [(0, 5.55)]), (P['g'], 'sonG', [(5.6, 13.6)])):
        arcs = ''
        for i, r in enumerate((16, 30, 44)):
            arcs += f'<path class="{cls}{i}" d="M{368 - r * .64:.1f} {468 - r * .77:.1f} A{r} {r} 0 0 0 {368 - r * .64:.1f} {468 + r * .77:.1f}" fill="none" stroke="{col}" stroke-width="4" stroke-linecap="round"/>'
            css.append(kf(f'{cls}p{i}', 1.2, [(0, 'opacity:.15'), (i * .2 + .1, 'opacity:.15'), (i * .2 + .3, 'opacity:1'), (i * .2 + .7, 'opacity:.15'), (1.2, 'opacity:.15')]))
            css.append(f'.{cls}{i}{{animation:{cls}p{i} 1.2s linear infinite}}')
        body.append(f'<g class="{cls}">{arcs}</g>')
        win(css, cls, T, wins, .1)
    # ghost obstacle the bad firmware "sees"
    body.append(f'''<g class="ghost"><rect x="300" y="450" width="40" height="38" rx="9" fill="{P['r2']}" stroke="{P['r']}" stroke-width="3" stroke-dasharray="7 6"/>
{text(320, 476, '?', 22, P['rd'], 'r', 800, 'middle')}</g>''')
    win(css, 'ghost', T, [(0, 5.55)], .25)
    # sensor bubble
    for cls, s, fg, bg, wins in (('bubR', 'ghost obstacle at 15 mm', P['rd'], P['r2'], [(0, 5.55)]), ('bubG', 'path clear', P['gd'], P['g2'], [(5.6, 13.6)])):
        w = 210 if cls == 'bubR' else 120
        body.append(f'''<g class="{cls}"><rect x="{452 - w}" y="178" width="{w}" height="34" rx="17" fill="{bg}"/>
<path d="M{430} 212 L{424} 224 L{416} 212 Z" fill="{bg}"/>{text(452 - w / 2, 200.5, s, 15, fg, 't', 700, 'middle')}</g>''')
        win(css, cls, T, wins, .2)
    # phase banner
    phases = [('1 · the job runs, the reflex fires at nothing', P['r2'], P['rd'], (0, 3.2)),
              ('2 · arm parked, the local healer takes over', P['vl'], P['vd'], (3.2, 5.6)),
              ('3 · good firmware back, the job re-runs', P['g2'], P['gd'], (5.6, 10.0)),
              ('4 · root cause found and written down', P['g2'], P['gd'], (10.0, 13.6))]
    for i, (s, bg, fg, w_) in enumerate(phases):
        body.append(f'<g class="ph{i}"><rect x="52" y="128" width="396" height="34" rx="17" fill="{bg}"/>{text(250, 150.5, s, 15, fg, "t", 700, "middle")}</g>')
        win(css, f'ph{i}', T, [w_], .15)
    # exit-4 stamp
    body.append(f'''<g transform="translate(232 300) rotate(-8)"><g class="stamp fb"><rect x="-128" y="-26" width="256" height="52" rx="10" fill="#FFFFFFE6" stroke="{P['r']}" stroke-width="4"/>
{text(0, 7, 'EXIT 4 · JOB GAVE UP', 19, P['rd'], 't', 800, 'middle', 'letter-spacing="1.5"')}</g></g>''')
    css.append(kf('stamp', T, [(0, 'opacity:0;transform:scale(1.6)'), (2.9, 'opacity:0;transform:scale(1.6)'), (3.12, 'opacity:1;transform:scale(1)'),
                               (4.1, 'opacity:1;transform:scale(1)'), (4.5, 'opacity:0;transform:scale(1)'), (T, 'opacity:0;transform:scale(1.6)')]))
    css.append(anim('.stamp', 'stamp', T, timing='ease-out'))

    # ---------------- terminal
    body.append(f'''<rect x="490" y="112" width="680" height="408" rx="22" fill="{P['term']}"/>
<path d="M490 152 V134 A22 22 0 0 1 512 112 H1148 A22 22 0 0 1 1170 134 V152 Z" fill="{P['term2']}"/>
<circle cx="514" cy="132" r="6.5" fill="{P['r']}"/><circle cx="535" cy="132" r="6.5" fill="{P['a']}"/><circle cx="556" cy="132" r="6.5" fill="{P['g']}"/>
{text(578, 137.5, 'pod healer · qwen3.5:4b · runs locally, no internet', 15, '#B9B4CF', 'm', 500)}''')
    risk = {'read': ('#8EC5FF', '#22324A'), 'flash': (P['a'], '#4A3815'), 'arm': ('#5BE0A8', '#173F31')}
    lines = [  # (y, t_appear, tool, risk, result, result_col, time_txt, t_result_swap, pre_text)
        (230, 3.9, 'status', 'read', 'checks the pod and its alarms', '#CFCBE0', '', None, None),
        (268, 4.5, 'fw_images', 'read', 'v1.7 running, v1.0 known-good on file', '#CFCBE0', '', None, None),
        (306, 5.0, 'fw_rollback', 'flash', '✓ v1.0 back on the chip, verified', '#5BE0A8', '+46 s', 5.6, 'backup, flash, verify…'),
        (344, 6.5, 'arm_run', 'arm', '✓ exit 0, 663 of 663 frames', '#5BE0A8', '+72 s', 9.3, "re-running the operator's job…"),
        (382, 10.0, 'src_diff', 'read', 'what changed from v1.0 to v1.7', '#CFCBE0', '', None, None),
        (420, 10.6, 'src_read', 'read', 'hcsr04.c, line 122', '#CFCBE0', '', None, None),
        (458, 11.2, 'note', 'read', 'root cause: a (uint16_t) cast truncates', '#B7AEFF', '', None, None),
    ]
    body.append(f'<g class="l0">{text(512, 192, "● incident opened · us_mismatch · critical", 16, "#FF8A9A", "m", 600)}</g>')
    pop_in(css, 'l0', T, 3.4, 13.5)
    for i, (y, t0, tool, rk, res, rc, tm, tswap, pre) in enumerate(lines, 1):
        fg, bg = risk[rk]
        tag = text(679, y - .5, rk, 12.5, fg, 't', 700, 'middle', 'letter-spacing=".6"')
        g = (f'{text(512, y, "›", 18, fg, "m", 700)}{text(530, y, tool, 17, "#F4F1FF", "m", 650)}'
             f'<rect x="652" y="{y - 16}" width="54" height="22" rx="11" fill="{bg}"/>{tag}')
        if pre:
            g += f'<g class="pre{i}">{text(722, y, pre, 16, "#F7D58A", "t", 500)}</g><g class="res{i}">{text(722, y, res, 16, rc, "t", 650)}{text(1150, y, tm, 15, "#5BE0A8", "m", 700, "end")}</g>'
            win(css, f'pre{i}', T, [(t0 + .2, tswap - .05)], .1)
            win(css, f'res{i}', T, [(tswap, 13.5)], .12)
        else:
            g += text(722, y, res, 16, rc, 't', 650 if tool == 'note' else 500)
        body.append(f'<g class="l{i}">{g}</g>')
        pop_in(css, f'l{i}', T, t0, 13.5)
    body.append(f'<path d="M512 478 H1150" stroke="#3A3452" stroke-width="2"/>')
    body.append(f'<g class="sum">{text(512, 505, "■ healed in 141 s · 12 turns · 10 tool calls · 0 humans", 16.5, "#5BE0A8", "m", 750)}</g>')
    pop_in(css, 'sum', T, 12.0, 13.5)
    # waiting cursor
    cur = [(0, 'transform:translateY(0)')]
    ys = [192, 230, 268, 306, 344, 382, 420, 458, 505]
    times = [3.4, 3.9, 4.5, 5.0, 6.5, 10.0, 10.6, 11.2, 12.0]
    for k, tt in enumerate(times[:-1]):
        cur += [(tt + .3, f'transform:translateY({ys[k + 1] - ys[0]}px)')]
    cur += [(12.0, f'transform:translateY({ys[-1] - ys[0] + 6}px)'), (13.6, f'transform:translateY({ys[-1] - ys[0] + 6}px)'), (T, 'transform:translateY(0)')]
    css.append(kf('cur', T, cur, ease='steps(1,end)'))
    css.append('.cur{animation:cur 14s linear infinite}')
    css.append(kf('curb', 1.0, [(0, 'opacity:1'), (.5, 'opacity:1'), (.51, 'opacity:0'), (1.0, 'opacity:0')]) + '.curb{animation:curb 1s linear infinite}')
    body.append(f'<g class="curw"><g class="cur"><rect class="curb" x="512" y="176" width="10" height="20" rx="2" fill="#B9B4CF"/></g></g>')
    win(css, 'curw', T, [(0, 11.95)], .05)

    # ---------------- progress of the real run
    x0, x1 = 60, 1140
    def X(s):
        return x0 + s / 141 * (x1 - x0)
    body.append(text(40, 560, "THE AGENT'S RUN IN REAL TIME, SPED UP HERE", 13, P['mute'], 't', 750, 'start', 'letter-spacing="1.6"'))
    body.append(f'<rect x="{x0}" y="574" width="{x1 - x0}" height="10" rx="5" fill="{P["line"]}"/>')
    body.append(f'<rect class="bar" x="{x0}" y="574" width="{x1 - x0}" height="10" rx="5" fill="{P["g"]}"/>')
    mp = [(0, 0), (3.4, 0), (5.6, 46), (9.3, 72), (12.0, 141), (13.5, 141)]
    css.append(kf('bar', T, [(tt, f'transform:scaleX({max(s / 141, .0001):.4f})') for tt, s in mp] + [(13.9, 'transform:scaleX(.0001)'), (T, 'transform:scaleX(.0001)')]))
    css.append('.bar{animation:bar 14s linear infinite;transform-box:fill-box;transform-origin:left center}')
    css.append(kf('head', T, [(tt, f'transform:translateX({X(s) - x0:.1f}px)') for tt, s in mp] + [(13.9, 'transform:translateX(0)'), (T, 'transform:translateX(0)')]))
    css.append('.head{animation:head 14s linear infinite}')
    for s, lab, anchor, tt in ((46, '46 s · rollback verified', 'middle', 5.6), (72, '72 s · job re-run clean', 'middle', 9.3), (141, '141 s · root cause named', 'end', 12.0)):
        body.append(f'<circle cx="{X(s):.1f}" cy="579" r="8" fill="#FFFFFF" stroke="{P["faint"]}" stroke-width="3"/>'
                    f'<circle class="tk{s} fb" cx="{X(s):.1f}" cy="579" r="8" fill="{P["g"]}" stroke="{P["gd"]}" stroke-width="3"/>')
        win(css, f'tk{s}', T, [(tt, 13.5)], .15)
        body.append(text(X(s) + (8 if anchor == 'end' else 0), 612, lab, 15, P['ink'], 't', 650, anchor))
    body.append(text(x0, 612, '0 s', 15, P['mute'], 't', 650, 'middle'))
    body.append(f'<g class="head"><circle cx="{x0}" cy="579" r="11" fill="{P["ink"]}"/><circle cx="{x0}" cy="579" r="4.5" fill="#FFFFFF"/></g>')
    return svg(W, H, 'The arm heals its own firmware, offline',
               'A real bench run. On bad firmware v1.7 the sensor pod reports a ghost obstacle at 15 mm, the arm flails and the job '
               'gives up (exit 4). The local healer, qwen3.5:4b with no internet, checks the pod, rolls the firmware back to v1.0 and '
               'verifies it at 46 s, re-runs the job cleanly at 72 s (663 of 663 frames), then finds the root cause, a (uint16_t) '
               'cast on line 122 of hcsr04.c. 141 s, 12 turns, 10 tool calls, 0 humans.',
               '\n'.join(css), card(W, H) + ''.join(body), 12.6)


def halo_text(x, y, s, size, fill, weight=650, anchor='middle', cls='t'):
    """Text with a cream outline so it stays legible over ring lines."""
    return text(x, y, s, size, fill, cls, weight, anchor,
                f'paint-order="stroke" stroke="{P["bg"]}" stroke-width="6" stroke-linejoin="round"')


def loops():
    from math import cos, sin, radians
    W, H, T = 1200, 600, 12.0
    cx, cy = 330, 352
    css, body = [], []
    body.append(text(40, 58, 'HOW IT WORKS', 14, P['vd'], 't', 750, 'start', 'letter-spacing="2"'))
    body.append(text(38, 96, 'One heal loop, at three scales', 32, P['ink'], 'r', 800))
    rings = [  # radius, colour, light, period, label, nodes (angle, text)
        (66, P['v'], P['vl'], 3.0, 'arm', [(-90, 'detect'), (30, 'fix'), (150, 'verify')]),
        (122, '#2F8FD8', '#DCEEFB', 6.0, 'fleet', [(-90, 'sync'), (0, 'replay'), (90, 'sim canary'), (180, 'promote')]),
        (180, P['a'], P['a2'], 12.0, 'company', [(-50, 'work order'), (40, 'Naïve agent'), (130, 'technician + part'), (220, '1 human click')]),
    ]
    for k, (R, col, light, per, name, nodes) in enumerate(rings):
        body.append(f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="none" stroke="{light}" stroke-width="16"/>'
                    f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="none" stroke="{col}" stroke-width="3" stroke-dasharray="2 10" stroke-linecap="round" opacity=".7"/>')
    for k, (R, col, light, per, name, nodes) in enumerate(rings):
        # comet: a short arc trail plus a head, rotating clockwise
        trail = ''
        for j in range(6):
            a0 = -90 - j * 7
            x, y = R * cos(radians(a0)), R * sin(radians(a0))
            trail += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{7.5 - j:.1f}" fill="{col}" opacity="{1 - j * .15:.2f}"/>'
        body.append(f'<g transform="translate({cx} {cy})"><g class="orb{k}">{trail}</g></g>')
        css.append(kf(f'orb{k}', per, [(0, 'transform:rotate(0deg)'), (per, 'transform:rotate(360deg)')]))
        css.append(f'.orb{k}{{animation:orb{k} {per}s linear infinite;transform-origin:0 0}}')
        for j, (ang, lab) in enumerate(nodes):
            x, y = cx + R * cos(radians(ang)), cy + R * sin(radians(ang))
            tpass = ((ang + 90) % 360) / 360 * per
            cls = f'nd{k}{j}'
            body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="11" fill="#FFFFFF" stroke="{col}" stroke-width="4"/>'
                        f'<circle class="{cls} fb" cx="{x:.1f}" cy="{y:.1f}" r="11" fill="{col}"/>')
            pre = max(tpass - .02, 0)
            stops = [(0, 'opacity:0;transform:scale(.6)')]
            if tpass < .05:
                stops = [(0, 'opacity:1;transform:scale(1.25)'), (per * .16, 'opacity:0;transform:scale(.6)'), (per, 'opacity:0;transform:scale(.6)')]
                stops[-1] = (per * .92, 'opacity:0;transform:scale(.6)')
                stops.append((per, 'opacity:1;transform:scale(1.25)'))
            else:
                stops += [(pre, 'opacity:0;transform:scale(.6)'), (tpass + .04, 'opacity:1;transform:scale(1.25)'),
                          (min(tpass + per * .16, per), 'opacity:0;transform:scale(.6)'), (per, 'opacity:0;transform:scale(.6)')]
            css.append(kf(cls, per, stops))
            css.append(anim(f'.{cls}', cls, per, timing='ease-out'))
            dx, dy = cos(radians(ang)), sin(radians(ang))
            lx, ly = cx + (R + 22) * dx, cy + (R + 22) * dy + 6
            anchor = 'start' if dx > .3 else ('end' if dx < -.3 else 'middle')
            if anchor == 'middle':
                ly += 8 if dy > 0 else -4
            body.append(halo_text(round(lx, 1), round(ly, 1), lab, 17, P['ink'], 650, anchor))
    body.append(f'<g class="hb">{buddy(cx, cy, 78, "content", "lp")}</g>')
    css.append(blink_css('.lpeo', 3.6, .55))
    css.append(kf('hb', 3.0, [(0, 'transform:scale(1)'), (1.5, 'transform:scale(1.04)'), (3.0, 'transform:scale(1)')]))
    css.append(f'.hb{{animation:hb 3s ease-in-out infinite;transform-origin:{cx}px {cy}px}}')
    # legend
    rows = [
        (P['v'], P['vl'], 'ON THE ROBOT', 'Heals itself, offline',
         ['A local model fixes software faults with no internet', 'and parks the arm when the fault is hardware. Every', 'flash is backed up, verified and rolled back if bad.']),
        ('#2F8FD8', '#DCEEFB', 'ACROSS THE FLEET', 'What one robot learns, every robot gets',
         ['Back online, each robot syncs to the Romulus cloud.', 'A fix that holds is replayed against past incidents,', 'tried on a simulated canary, then shipped to all.']),
        (P['a'], P['a2'], 'ACROSS THE COMPANY', 'Naïve agents take what needs hands',
         ['A hardware fault becomes a work order. A Naïve agent', 'diagnoses it, emails a technician and stages the part.', 'A person clicks once, only to spend money.']),
    ]
    y0 = 150
    for k, (col, light, kick, title, lines) in enumerate(rows):
        y = y0 + k * 148
        body.append(f'<g class="lg{k}"><rect x="606" y="{y - 30}" width="556" height="140" rx="18" fill="#FFFFFF" stroke="{P["line"]}" stroke-width="2"/>'
                    f'<rect x="606" y="{y - 30}" width="8" height="140" rx="4" fill="{col}"/>'
                    + text(634, y, kick, 13, col if col != P['a'] else P['ad'], 't', 800, 'start', 'letter-spacing="1.8"')
                    + text(634, y + 28, title, 21, P['ink'], 'r', 800)
                    + ''.join(text(634, y + 56 + i * 21, ln, 15.5, P['mute'], 't', 500) for i, ln in enumerate(lines)) + '</g>')
    return svg(W, H, 'One heal loop, at three scales',
               'Three concentric loops around the Buddy chip. On the robot: detect, fix, verify, offline. Across the fleet: sync, '
               'replay, sim canary, promote. Across the company: work order, Naïve agent, technician and part, one human click.',
               '\n'.join(css), card(W, H) + ''.join(body), 1.0)


def check_badge(x, y, col, r=13):
    return (f'<circle cx="{x}" cy="{y}" r="{r}" fill="{col}" stroke="#FFFFFF" stroke-width="3"/>'
            f'<path d="M{x - r * .42:.1f} {y + r * .02:.1f} L{x - r * .1:.1f} {y + r * .34:.1f} L{x + r * .44:.1f} {y - r * .3:.1f}" '
            f'fill="none" stroke="#FFFFFF" stroke-width="{r * .24:.1f}" stroke-linecap="round" stroke-linejoin="round"/>')


ICON_ST = f'stroke="{P["ink"]}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"'


def icon(kind):
    """Small illustrations centred on 0,0, about 60 px across."""
    st = ICON_ST
    if kind == 'jam':
        return (f'<path d="M-26 22 H8" {st}/><path d="M-18 22 L-14 10 H0 L4 22" fill="{P["arm"]}" {st}/>'
                f'<path d="M-7 8 L-12 -16 L16 -26" fill="none" stroke="{P["ink"]}" stroke-width="14" stroke-linecap="round" stroke-linejoin="round"/>'
                f'<path d="M-7 8 L-12 -16 L16 -26" fill="none" stroke="{P["arm"]}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>'
                f'<path d="M-22 -24 L-3 -9" stroke="{P["r"]}" stroke-width="6" stroke-linecap="round"/>'
                f'<circle cx="19" cy="6" r="12" fill="{P["r"]}"/><path d="M19 0 V7" stroke="#fff" stroke-width="4" stroke-linecap="round"/><circle cx="19" cy="12.5" r="2.4" fill="#fff"/>')
    if kind == 'agent':
        return (f'<path d="M-26 -20 H26 A9 9 0 0 1 35 -11 V9 A9 9 0 0 1 26 18 H-2 L-16 30 V18 H-26 A9 9 0 0 1 -35 9 V-11 A9 9 0 0 1 -26 -20 Z" fill="#fff" {st}/>'
                f'<circle cx="-13" cy="-1" r="4.5" fill="{P["v"]}"/><circle cy="-1" r="4.5" fill="{P["v"]}"/><circle cx="13" cy="-1" r="4.5" fill="{P["v"]}"/>')
    if kind == 'mail':
        return (f'<rect x="-30" y="-20" width="60" height="42" rx="8" fill="#fff" {st}/><path d="M-24 -13 L0 5 L24 -13" fill="none" {st}/>')
    if kind == 'cart':
        return (f'<path d="M-32 -22 H-22 L-14 12 H20 L28 -12 H-19" fill="none" {st}/>'
                f'<path d="M-17 -6 H25" stroke="{P["ink"]}" stroke-width="3" stroke-linecap="round"/>'
                f'<circle cx="-9" cy="23" r="5.5" fill="{P["ink"]}"/><circle cx="16" cy="23" r="5.5" fill="{P["ink"]}"/>'
                f'<rect x="-10" y="-30" width="20" height="16" rx="4" fill="{P["a"]}" {st}/>')
    if kind == 'click':
        return (f'<rect x="-34" y="-20" width="58" height="30" rx="15" fill="{P["v"]}" {st}/>'
                f'<path d="M-21 -5 L-14 2 L-1 -11" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>'
                f'<path d="M10 2 L10 30 L17 23 L22 34 L28 31 L23 20 L33 19 Z" fill="#fff" {st}/>')
    if kind == 'doc':
        return (f'<path d="M-22 -30 H10 L24 -16 V30 H-22 Z" fill="#fff" {st}/><path d="M10 -30 V-16 H24" fill="none" {st}/>'
                f'<path d="M-12 -8 H14 M-12 4 H14 M-12 16 H4" stroke="{P["v"]}" stroke-width="4" stroke-linecap="round"/>')
    if kind == 'lock':
        return (f'<rect x="-11" y="-3" width="22" height="18" rx="4" fill="{P["ink"]}"/>'
                f'<path d="M-6 -3 V-8 A6 6 0 0 1 6 -8 V-3" fill="none" stroke="{P["ink"]}" stroke-width="3.5"/>')
    raise ValueError(kind)


def technician():
    W, H, T = 1200, 490, 11.0
    css, body = [], []
    body.append(text(40, 58, 'LIVE RUN · NAÏVE TECHNICIAN · SUN NOV 8, 8:44 AM', 14, P['ad'], 't', 750, 'start', 'letter-spacing="2"'))
    body.append(text(38, 96, 'Some faults need hands', 32, P['ink'], 'r', 800))
    steps = [('jam', P['r'], P['r2'], 'Elbow jammed', ["The healer can't clear it,", 'so it parks the arm.'], .6),
             ('agent', P['v'], P['vl'], 'A Naïve agent takes it', ['Diagnoses the fault in', 'one 27 s session.'], 2.2),
             ('mail', '#2F8FD8', '#DCEEFB', 'Emails a technician', ['What to check, starting', 'with the jam.'], 3.8),
             ('cart', P['g'], P['g2'], 'Stages a spare servo', ['$15.99, with price and', 'stock checked in code.'], 5.4),
             ('click', P['ink'], '#E9E6F2', 'A person approves', ['The one human step:', 'spending money.'], 7.0)]
    x0, w, g, y0, h = 40, 200, 30, 136, 222
    for i, (ic, col, light, title, sub, t0) in enumerate(steps):
        x = x0 + i * (w + g)
        cxm = x + w / 2
        body.append(f'<rect x="{x}" y="{y0}" width="{w}" height="{h}" rx="20" fill="#FFFFFF" stroke="{P["line"]}" stroke-width="2"/>')
        body.append(f'<g class="on{i}"><rect x="{x}" y="{y0}" width="{w}" height="{h}" rx="20" fill="{light}" stroke="{col}" stroke-width="3"/></g>')
        win(css, f'on{i}', T, [(t0, 10.4)], .25)
        body.append(f'<circle cx="{cxm}" cy="{y0 + 66}" r="44" fill="#FFFFFF" stroke="{P["line"]}" stroke-width="2"/>')
        body.append(f'<g transform="translate({cxm} {y0 + 66})"><g class="ic{i} fb">{icon(ic)}</g></g>')
        css.append(kf(f'ic{i}', T, [(0, 'transform:scale(1)'), (t0, 'transform:scale(1)'), (t0 + .2, 'transform:scale(1.22)'), (t0 + .45, 'transform:scale(1)'), (T, 'transform:scale(1)')]))
        css.append(anim(f'.ic{i}', f'ic{i}', T, timing='ease-in-out'))
        body.append(f'<g class="ck{i}">{check_badge(x + w - 26, y0 + 26, col if col != P["ink"] else P["v"])}</g>')
        win(css, f'ck{i}', T, [(t0 + .3, 10.4)], .15)
        body.append(text(cxm, y0 + 146, title, 17.5, P['ink'], 'r', 800, 'middle'))
        for j, ln in enumerate(sub):
            body.append(text(cxm, y0 + 174 + j * 21, ln, 15, P['mute'], 't', 500, 'middle'))
        if i < len(steps) - 1:
            ax = x + w + 4
            body.append(f'<path d="M{ax + 4} {y0 + 66} H{ax + g - 10}" stroke="{P["faint"]}" stroke-width="4" stroke-linecap="round"/>'
                        f'<path d="M{ax + g - 16} {y0 + 59} L{ax + g - 9} {y0 + 66} L{ax + g - 16} {y0 + 73}" fill="none" stroke="{P["faint"]}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>')
    # measured numbers
    nums = [('38 s', 'incident to a case waiting for you'), ('27 s', 'one Naïve agent session'), ('$0.08', 'what that session cost'), ('1', 'human click')]
    body.append(f'<rect x="40" y="380" width="1120" height="80" rx="18" fill="{P["ink"]}"/>')
    for k, (n, lab) in enumerate(nums):
        cxm = 40 + 140 + k * 280
        if k:
            body.append(f'<path d="M{40 + k * 280} 396 V444" stroke="#3A3452" stroke-width="2"/>')
        body.append(f'<g class="nm{k} fb">' + text(cxm, 414, n, 26, P['a'] if n == '1' else '#FFFFFF', 'r', 800, 'middle') + '</g>'
                    + text(cxm, 440, lab, 14.5, '#C9C4DD', 't', 550, 'middle'))
        tp = 8.0 + k * .25
        css.append(kf(f'nm{k}', T, [(0, 'transform:scale(1)'), (tp, 'transform:scale(1)'), (tp + .2, 'transform:scale(1.25)'),
                                    (tp + .45, 'transform:scale(1)'), (T, 'transform:scale(1)')]))
        css.append(anim(f'.nm{k}', f'nm{k}', T, timing='ease-in-out'))
    return svg(W, H, 'Some faults need hands',
               'The Naïve Technician, measured in a live run. An elbow jam the healer cannot clear parks the arm. A Naïve agent takes '
               'the case in one 27 s session costing $0.08, emails a technician what to check, and stages a $15.99 spare servo with '
               'price and stock checked in code. A person approves, the only human step. 38 s from incident to a case waiting for you.',
               '\n'.join(css), card(W, H) + ''.join(body), 9.0)


def robot_chip(x, y, name, prefix):
    """A fleet member: a small rounded card with a status light and harness version."""
    return f'''<g transform="translate({x} {y})">
<rect width="196" height="74" rx="16" fill="#FFFFFF" stroke="{P['line']}" stroke-width="2"/>
<g transform="translate(36 37) scale(.36)">{buddy(0, 0, 120, 'content', prefix)}</g>
{text(70, 31, name, 17, P['ink'], 'm', 700)}
<g class="{prefix}v1">{text(70, 55, 'harness v1', 14, P['mute'], 't', 600)}</g>
<g class="{prefix}v2">{text(70, 55, 'harness v2', 14, P['gd'], 't', 750)}</g>
</g>'''


def fleet():
    W, H, T = 1200, 560, 13.0
    css, body = [], []
    body.append(text(40, 58, 'LIVE RUN · FLEET LEARNING · SUN NOV 8, 10:04 AM', 14, '#2F8FD8', 't', 750, 'start', 'letter-spacing="2"'))
    body.append(text(38, 96, 'Every fault happens once', 32, P['ink'], 'r', 800))
    robots = [('sim-1', 132), ('sim-2', 226), ('sim-3', 320)]
    for i, (nm, y) in enumerate(robots):
        body.append(robot_chip(40, y, nm, f'r{i}'))
        win(css, f'r{i}v1', T, [(0, 9.4 + i * .3)], .1)
        win(css, f'r{i}v2', T, [(9.4 + i * .3 + .05, 12.4)], .1)
        body.append(f'<circle cx="214" cy="{y + 14}" r="7" fill="{P["faint"]}"/>')
        body.append(f'<g class="rg{i}"><circle cx="214" cy="{y + 14}" r="7" fill="{P["g"]}"/></g>')
        win(css, f'rg{i}', T, [(9.4 + i * .3, 12.4)], .1)
    body.append(f'<g class="fail"><rect x="244" y="148" width="132" height="30" rx="15" fill="{P["r2"]}"/>{text(310, 168, "heal failed", 14.5, P["rd"], "t", 750, "middle")}</g>')
    win(css, 'fail', T, [(.3, 9.3)], .2)
    body.append(f'<g class="held"><rect x="244" y="336" width="132" height="30" rx="15" fill="{P["g2"]}"/>{text(310, 356, "heal held", 14.5, P["gd"], "t", 750, "middle")}</g>')
    win(css, 'held', T, [(.6, 9.3)], .2)
    body.append(f'<g class="can"><rect x="244" y="242" width="132" height="30" rx="15" fill="{P["a2"]}"/>{text(310, 262, "canary · 32.5 s", 14.5, P["ad"], "t", 750, "middle")}</g>')
    win(css, 'can', T, [(6.0, 7.6)], .2)
    rx, ry = 420, 132
    body.append(f'<rect x="{rx}" y="{ry}" width="300" height="168" rx="20" fill="#FFFFFF" stroke="{P["line"]}" stroke-width="2"/>')
    body.append(f'<g class="rvon"><rect x="{rx}" y="{ry}" width="300" height="168" rx="20" fill="{P["vl"]}" stroke="{P["v"]}" stroke-width="3"/></g>')
    win(css, 'rvon', T, [(1.8, 3.6)], .3)
    body.append(f'<g transform="translate({rx + 46} {ry + 52}) scale(.8)">{icon("agent")}</g>')
    body.append(text(rx + 90, ry + 46, 'Naïve agent session', 18, P['ink'], 'r', 800) + text(rx + 90, ry + 68, 'fleet-doctor · cluster review', 14.5, P['mute'], 't', 550))
    body.append(text(rx + 24, ry + 112, 'Reads the failed run and the', 15.5, P['ink'], 't', 500) + text(rx + 24, ry + 133, 'passing one, writes a skill.', 15.5, P['ink'], 't', 500))
    body.append(f'<g class="rvdone">{text(rx + 24, ry + 158, "22 s · 3¢", 15, P["vd"], "m", 750)}</g>')
    win(css, 'rvdone', T, [(3.4, 12.4)], .2)
    sx, sy = 752, 132
    body.append(f'<g class="skill"><rect x="{sx}" y="{sy}" width="408" height="168" rx="20" fill="#FFFFFF" stroke="{P["v"]}" stroke-width="2.5"/>'
                f'<g transform="translate({sx + 40} {sy + 58}) scale(.85)">{icon("doc")}</g>'
                + text(sx + 80, sy + 44, 'NEW SKILL', 13, P['vd'], 't', 800, 'start', 'letter-spacing="1.8"')
                + text(sx + 80, sy + 70, 'Servo read error: check the retry', 17, P['ink'], 'r', 800)
                + text(sx + 80, sy + 92, 'call site, not the config', 17, P['ink'], 'r', 800)
                + text(sx + 24, sy + 132, 'Cites the failed sim-1 run and the passing sim-3', 14.5, P['mute'], 't', 500)
                + text(sx + 24, sy + 153, 'run. "Not tested on real hardware."', 14.5, P['mute'], 't', 500) + '</g>')
    pop_in(css, 'skill', T, 3.8, 12.4, dx=0, dy=16, dur=.45)
    gx, gy = 420, 336
    gates = [('replay', 'past incidents', 5.0), ('canary', 'sim-2, 30 s window', 7.6), ('promote', 'every so101 arm', 8.4)]
    body.append(text(gx, gy - 6, 'THE GATE, IN PLAIN CODE', 13, P['mute'], 't', 800, 'start', 'letter-spacing="1.8"'))
    for i, (nm, sub, t0) in enumerate(gates):
        x = gx + i * 252
        body.append(f'<rect x="{x}" y="{gy + 8}" width="232" height="70" rx="16" fill="#FFFFFF" stroke="{P["line"]}" stroke-width="2"/>')
        body.append(f'<g class="gt{i}"><rect x="{x}" y="{gy + 8}" width="232" height="70" rx="16" fill="{P["g2"]}" stroke="{P["g"]}" stroke-width="3"/>{check_badge(x + 204, gy + 43, P["g"], 12)}</g>')
        win(css, f'gt{i}', T, [(t0, 12.4)], .2)
        body.append(text(x + 20, gy + 39, nm, 18, P['ink'], 'r', 800) + text(x + 20, gy + 62, sub, 14.5, P['mute'], 't', 550))
        if i < 2:
            body.append(f'<path d="M{x + 236} {gy + 43} H{x + 248}" stroke="{P["faint"]}" stroke-width="4" stroke-linecap="round"/>')

    def packet(cls, pts, t0, t1, col):
        body.append(f'<g class="{cls}"><circle r="12" fill="{col}" opacity=".22"/><circle r="7" fill="{col}"/></g>')
        st = [(0, f'opacity:0;transform:translate({pts[0][0]}px,{pts[0][1]}px)'), (t0, f'opacity:0;transform:translate({pts[0][0]}px,{pts[0][1]}px)'),
              (t0 + .05, f'opacity:1;transform:translate({pts[0][0]}px,{pts[0][1]}px)')]
        L = [hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])]
        acc, tot = 0, sum(L)
        for k in range(len(pts) - 1):
            acc += L[k]
            st.append((t0 + .05 + (t1 - t0 - .05) * acc / tot, f'opacity:1;transform:translate({pts[k + 1][0]}px,{pts[k + 1][1]}px)'))
        st += [(t1 + .1, f'opacity:0;transform:translate({pts[-1][0]}px,{pts[-1][1]}px)'), (T, f'opacity:0;transform:translate({pts[-1][0]}px,{pts[-1][1]}px)')]
        css.append(kf(cls, T, st))
        css.append(anim(f'.{cls}', cls, T))
    body.append(f'<path d="M1072 414 V470 H226 V420" fill="none" stroke="{P["line"]}" stroke-width="4" stroke-dasharray="2 9" stroke-linecap="round"/>')
    packet('pk0', [(236, 169), (400, 169), (420, 200)], 1.0, 1.8, P['r'])
    packet('pk1', [(720, 216), (752, 216)], 3.4, 3.8, P['v'])
    packet('pk2', [(956, 300), (956, 320), (536, 320), (536, 344)], 4.3, 5.0, P['v'])
    packet('pk3', [(310, 278), (236, 263)], 6.0, 6.4, P['a'])
    for i, (nm, y) in enumerate(robots):
        packet(f'pk4{i}', [(1072, 414), (1072, 470), (226, 470), (226, y + 74)], 8.5, 9.4 + i * .3, P['g'])
    body.append(f'<g class="sumf"><rect x="300" y="488" width="600" height="52" rx="26" fill="{P["ink"]}"/>'
                + text(600, 521, 'one lesson → every arm · 57 s · 3¢ · 0 clicks', 19, '#FFFFFF', 'r', 800, 'middle') + '</g>')
    pop_in(css, 'sumf', T, 10.2, 12.4, dx=0, dy=12, dur=.45)
    body.append('<g class="skv">' + text(1160, 521, 'Naïve Skills playbook v1 → v2', 15, P['vd'], 't', 750, 'end') + '</g>')
    pop_in(css, 'skv', T, 10.5, 12.4, dx=0, dy=8)
    body.append(f'<g transform="translate(54 506)">{icon("lock")}</g>' + text(72, 514, 'touches safety?', 14.5, P['ink'], 't', 750)
                + text(72, 533, 'staged for its owner', 14.5, P['mute'], 't', 500))
    return svg(W, H, 'Every fault happens once',
               'Fleet learning, measured in a live run. A heal fails on sim-1 and holds on sim-3. A Naïve agent session (fleet-doctor) '
               'reads both runs in 22 s for 3 cents and writes a skill. The gate replays it, runs a canary on sim-2 for 32.5 s, and '
               'promotes it: harness v2 on every arm, 57 s from lesson to fleet, 0 human clicks. Changes that touch safety are staged '
               'for their owner instead.',
               '\n'.join(css), card(W, H) + ''.join(body), 11.0)


def stats():
    W, H, T = 1200, 360, 10.0
    css, body = [], []
    tiles = [('141 s', P['g'], 'real arm, bad firmware, healed with', 'no internet and no human touch'),
             ('10 / 12', P['v'], 'heals passed by a 3.3 GB local model', 'on 6 planted faults, 2 runs, no cloud'),
             ('38', P['ink'], 'heal tools, each with a risk level', 'from read-only to flash'),
             ('57 s', '#2F8FD8', "from one robot's failed heal to a", 'tested fix on every arm'),
             ('$0.08', P['ad'], 'for a Naïve agent to diagnose a fault,', 'email a technician and stage a part'),
             ('1', P['r'], 'human click in the whole loop,', 'and only to spend money')]
    css.append('.tl{animation-duration:10s;animation-iteration-count:infinite;animation-timing-function:cubic-bezier(.2,.9,.3,1.2)}')
    tight = 'letter-spacing="-1"'
    for i, (n, col, l1, l2) in enumerate(tiles):
        x, y = 30 + (i % 3) * 384, 30 + (i // 3) * 156
        body.append(f'<rect x="{x}" y="{y}" width="370" height="142" rx="20" fill="#FFFFFF" stroke="{P["line"]}" stroke-width="2"/>')
        body.append(f'<defs><clipPath id="c{i}"><rect x="{x}" y="{y + 10}" width="370" height="76"/></clipPath></defs>')
        out0, in0 = 7.6 + i * .08, 8.5 + i * .12  # numbers stay up at t=0, so a still frame is never blank
        num = text(x + 28, y + 74, n, 54, col, 'r', 800, 'start', tight)
        body.append(f'<g clip-path="url(#c{i})"><g class="tl n{i}">{num}</g></g>')
        css.append(kf(f'n{i}', T, [(0, 'transform:translateY(0)'), (out0, 'transform:translateY(0)'), (out0 + .35, 'transform:translateY(-80px)'),
                                   (out0 + .36, 'transform:translateY(80px)'), (in0, 'transform:translateY(80px)'), (in0 + .7, 'transform:translateY(0)'),
                                   (T, 'transform:translateY(0)')]))
        css.append(f'.n{i}{{animation-name:n{i}}}')
        body.append(text(x + 30, y + 106, l1, 15.5, P['mute'], 't', 550) + text(x + 30, y + 127, l2, 15.5, P['mute'], 't', 550))
        body.append(f'<rect class="u{i}" x="{x + 30}" y="{y + 84}" width="54" height="5" rx="2.5" fill="{col}"/>')
        css.append(kf(f'u{i}', T, [(0, 'transform:scaleX(1)'), (out0, 'transform:scaleX(1)'), (out0 + .3, 'transform:scaleX(0)'),
                                   (in0 + .3, 'transform:scaleX(0)'), (in0 + .75, 'transform:scaleX(1)'), (T, 'transform:scaleX(1)')]))
        css.append(f'.u{i}{{animation:u{i} 10s ease-out infinite;transform-box:fill-box;transform-origin:left center}}')
    return svg(W, H, 'Hardware Buddy in numbers',
               '141 s: a real arm on bad firmware healed with no internet and no human touch. 10 of 12 heals passed by a 3.3 GB local '
               'model on 6 planted faults, 2 runs each, no cloud. 38 heal tools, each with a risk level. 57 s from one failed heal to a tested fix on '
               'every arm. $0.08 for a Naïve agent to diagnose a fault, email a technician and stage a part. 1 human click, only to spend money.',
               '\n'.join(css), card(W, H, dots=False) + ''.join(body), 4.0)


def video():
    W, H, T = 1200, 675, 12.0
    css, body = [], []
    body.append(f'''<defs><radialGradient id="vg" cx="50%" cy="46%" r="62%"><stop offset="0" stop-color="#3A2F7A"/><stop offset=".55" stop-color="#231E3C"/><stop offset="1" stop-color="{P['ink']}"/></radialGradient>
<pattern id="vdots" width="28" height="28" patternUnits="userSpaceOnUse"><circle cx="3" cy="3" r="1.4" fill="#FFFFFF" opacity=".07"/></pattern></defs>
<rect width="{W}" height="{H}" rx="28" fill="url(#vg)"/><rect width="{W}" height="{H}" rx="28" fill="url(#vdots)"/>''')
    body.append(f'<g transform="translate(70 74) scale(.42)">{buddy(0, 0, 120, "content", "vb")}</g>')
    css.append(blink_css('.vbeo', 3.4, .6))
    body.append(text(112, 84, 'Hardware Buddy', 26, '#FFF8EE', 'r', 800))
    body.append(f'<rect x="1010" y="54" width="150" height="38" rx="19" fill="#FFFFFF1F" stroke="#FFFFFF40" stroke-width="1.5"/>'
                + text(1085, 79, 'DEMO VIDEO', 14, '#FFF8EE', 't', 800, 'middle', 'letter-spacing="2"'))
    for i in range(2):
        body.append(f'<circle class="pr{i} fb" cx="600" cy="300" r="70" fill="none" stroke="{P["v2"]}" stroke-width="3"/>')
        css.append(kf(f'pr{i}', 2.4, [(0, 'opacity:.8;transform:scale(1)'), (2.4, 'opacity:0;transform:scale(1.9)')]))
        css.append(f'.pr{i}{{animation:pr{i} 2.4s ease-out infinite;animation-delay:{i * 1.2}s}}')
    body.append(f'<g class="pb fb"><circle cx="600" cy="300" r="70" fill="{P["v"]}"/><circle cx="600" cy="300" r="70" fill="none" stroke="#FFFFFF" stroke-opacity=".35" stroke-width="2"/>'
                f'<path d="M584 268 L630 300 L584 332 Z" fill="#FFFFFF" stroke="#FFFFFF" stroke-width="10" stroke-linejoin="round"/></g>')
    css.append(kf('pb', 2.4, [(0, 'transform:scale(1)'), (1.2, 'transform:scale(1.05)'), (2.4, 'transform:scale(1)')]) + '.pb{animation:pb 2.4s ease-in-out infinite}')
    body.append(text(600, 446, 'Watch the demo', 50, '#FFF8EE', 'r', 800, 'middle', 'letter-spacing="-.5"'))
    body.append(text(600, 490, 'A real robot arm breaks, fixes its own firmware with no internet,', 21, '#C9C4DD', 't', 500, 'middle'))
    body.append(text(600, 518, 'and Naïve agents take care of the rest.', 21, '#C9C4DD', 't', 500, 'middle'))
    x0, x1, yb = 70, 1130, 586
    body.append(f'<rect x="{x0}" y="{yb}" width="{x1 - x0}" height="8" rx="4" fill="#FFFFFF2A"/>')
    body.append(f'<rect class="vbar" x="{x0}" y="{yb}" width="{x1 - x0}" height="8" rx="4" fill="{P["v2"]}"/>')
    css.append(kf('vbar', T, [(0, 'transform:scaleX(.002)'), (11.4, 'transform:scaleX(1)'), (T, 'transform:scaleX(1)')]))
    css.append('.vbar{animation:vbar 12s linear infinite;transform-box:fill-box;transform-origin:left center}')
    chapters = [(0.0, 'The problem'), (.30, 'It heals itself'), (.56, 'When it needs hands'), (.76, 'The fleet learns')]
    for f, lab in chapters:
        x = x0 + f * (x1 - x0)
        if f > 0:
            body.append(f'<rect x="{x - 2}" y="{yb - 3}" width="4" height="14" rx="2" fill="{P["ink"]}"/>')
        body.append(text(x + (0 if f == 0 else 8), yb + 38, lab, 16, '#C9C4DD', 't', 600, 'start'))
    body.append(f'<g class="vhead"><circle cx="{x0}" cy="{yb + 4}" r="9" fill="#FFFFFF"/></g>')
    css.append(kf('vhead', T, [(0, 'transform:translateX(0)'), (11.4, f'transform:translateX({x1 - x0}px)'), (T, f'transform:translateX({x1 - x0}px)')]))
    css.append('.vhead{animation:vhead 12s linear infinite}')
    return svg(W, H, 'Watch the Hardware Buddy demo',
               'Video thumbnail: a play button. A real robot arm breaks, fixes its own firmware with no internet, and Naïve agents take '
               'care of the rest. Chapters: the problem, it heals itself, when it needs hands, the fleet learns.',
               '\n'.join(css), ''.join(body), 1.0)


def mark():
    """The Buddy chip on its own, blinking and bobbing, on a transparent background."""
    css = [blink_css('.fmeo', 3.8, .55),
           kf('fbob', 3.0, [(0, 'transform:translateY(0)'), (1.5, 'transform:translateY(-4px)'), (3.0, 'transform:translateY(0)')]),
           '.fbob{animation:fbob 3s ease-in-out infinite}']
    return svg(176, 176, 'Hardware Buddy', 'The Buddy chip: a violet chip with pins and two eyes, blinking.',
               '\n'.join(css), f'<g class="fbob">{buddy(88, 90, 140, "content", "fm")}</g>', 1.0)


def travel(css, body, cls, pts, t0, t1, col, T):
    """A packet that runs along the polyline `pts` between t0 and t1 (seconds), then fades."""
    body.append(f'<g class="{cls}"><circle r="12" fill="{col}" opacity=".22"/><circle r="7" fill="{col}"/></g>')
    L = [hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])]
    tot, acc = sum(L), 0
    p0, pn = pts[0], pts[-1]
    st = [(0, f'opacity:0;transform:translate({p0[0]}px,{p0[1]}px)'), (t0, f'opacity:0;transform:translate({p0[0]}px,{p0[1]}px)'),
          (t0 + .08, f'opacity:1;transform:translate({p0[0]}px,{p0[1]}px)')]
    for k in range(len(pts) - 1):
        acc += L[k]
        st.append((t0 + .08 + (t1 - t0 - .08) * acc / tot, f'opacity:1;transform:translate({pts[k + 1][0]}px,{pts[k + 1][1]}px)'))
    st += [(t1 + .15, f'opacity:0;transform:translate({pn[0]}px,{pn[1]}px)'), (T, f'opacity:0;transform:translate({pn[0]}px,{pn[1]}px)')]
    css.append(kf(cls, T, st))
    css.append(anim(f'.{cls}', cls, T))


def flash(css, cls, T, times, rise=.08, fall=.6):
    """Opacity 0 → 1 → 0 at each arrival time; a new arrival during a fade keeps it lit."""
    st, last = [(0, 'opacity:0')], 0
    for t in sorted(times):
        if t > last:
            st.append((t, 'opacity:0'))
        else:
            st.pop()
        st.append((t + rise, 'opacity:1'))
        last = min(t + rise + fall, T)
        st.append((last, 'opacity:0'))
    st.append((T, 'opacity:0'))
    css.append(kf(cls, T, st))
    css.append(anim(f'.{cls}', cls, T))


def stack():
    W, H, T = 1200, 700, 9.0
    css, body = [], []
    A, B, V, G = P['a'], '#2F8FD8', P['v'], P['g']
    body.append(text(40, 58, 'UNDER THE HOOD', 14, P['vd'], 't', 750, 'start', 'letter-spacing="2"'))
    body.append(text(38, 96, 'The stack, from servo to company', 32, P['ink'], 'r', 800))
    # the four beats of the story, lit in turn
    stages = [('1', 'heal offline', V, (.05, 1.85)), ('2', 'report up', B, (1.85, 3.25)),
              ('3', 'agents act', A, (3.3, 5.65)), ('4', 'ship back', G, (5.7, 8.6))]
    cw, gap = 124, 8
    x0 = 1160 - 4 * cw - 3 * gap
    for i, (n, lab, col, w_) in enumerate(stages):
        x = x0 + i * (cw + gap)

        def chip(fill, stroke, numfill, numtxt, txtcol):
            return (f'<rect x="{x}" y="52" width="{cw}" height="36" rx="18" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
                    f'<circle cx="{x + 19}" cy="70" r="11" fill="{numfill}"/>' + text(x + 19, 75, n, 13, numtxt, 't', 800, 'middle')
                    + text(x + 37, 75, lab, 14, txtcol, 't', 750))
        body.append(chip('#FFFFFF', P['line'], P['line'], P['mute'], P['mute']))
        body.append(f'<g class="sg{i}">' + chip(col, col, '#FFFFFF', col, '#FFFFFF') + '</g>')
        win(css, f'sg{i}', T, [w_])
    bands = [(124, 128, P['a2'], P['ad'], 'NAÏVE · THE COMPANY\'S AGENTS'),
             (290, 128, '#DCEEFB', '#1F6FB0', 'ROMULUS CLOUD · FASTAPI + POSTGRES'),
             (456, 216, P['vl'], P['vd'], 'ON THE BENCH · WORKS OFFLINE')]
    for y, h, fill, col, lab in bands:
        body.append(f'<rect x="30" y="{y}" width="1140" height="{h}" rx="22" fill="{fill}" opacity=".75"/>')
        body.append(text(54, y + 28, lab, 13, col, 't', 800, 'start', 'letter-spacing="1.8"'))
    wires_at = len(body)
    ny, cy_, by1, by2 = 164, 330, 496, 586
    nodes = {  # name: (x, y, width, title, subtitle, accent)
        'tech': (56, ny, 240, 'technician agent', 'email + parts cart', A),
        'doc': (452, ny, 240, 'fleet-doctor agent', 'cluster review', A),
        'board': (762, ny, 250, 'board, inbox', 'Naïve Skills', A),
        'wo': (56, cy_, 186, 'work orders', 'jobs that need hands', B),
        'api': (290, cy_, 238, 'device API', 'romulus-contracts', B),
        'runs': (576, cy_, 236, 'labelled runs', 'timeline, datasets', B),
        'gate': (860, cy_, 286, 'gate', 'replay → sim canary → promote', B),
        'pod': (56, by1, 186, 'STM32 sensor pod', 'HC-SR04 + LIS3DH', V),
        'podd': (290, by1, 186, 'podd', '38 heal tools', V),
        'host': (576, by1, 206, 'arm host', '+ watchdog', V),
        'arm': (860, by1, 186, 'SO-101 arm', 'LeRobot 0.7', V),
        'heal': (290, by2, 214, 'healer agent loop', 'qwen3.5:4b, local', V),
        'hermes': (576, by2, 230, 'Hermes commander', 'so101-arm plugin', V),
    }
    for x, y, w, title, sub, col in nodes.values():
        body.append(f'<rect x="{x}" y="{y}" width="{w}" height="62" rx="14" fill="#FFFFFF" stroke="{P["line"]}" stroke-width="2"/>'
                    f'<rect x="{x}" y="{y + 12}" width="5" height="38" rx="2.5" fill="{col}"/>'
                    + text(x + 20, y + 27, title, 16.5, P['ink'], 'r', 800) + text(x + 20, y + 48, sub, 13.5, P['mute'], 't', 550))
    hx, hy = 372, ny + 31
    body.append(f'<circle cx="{hx}" cy="{hy}" r="38" fill="{P["ink"]}"/>' + text(hx, hy - 4, '1 human', 13.5, '#FFFFFF', 't', 750, 'middle')
                + text(hx, hy + 14, 'click', 13.5, A, 't', 800, 'middle'))
    # (path, label, label point, text-anchor, start s, end s, packet colour, destination), in story order
    flows = [
        ([(242, by1 + 31), (290, by1 + 31)], 'serial', (266, by1 + 22), 'middle', .0, .5, V, 'podd'),
        ([(380, by2), (380, by1 + 62)], 'tools only', (390, by1 + 80), 'start', .4, 1.0, V, 'podd'),
        ([(476, by1 + 31), (576, by1 + 31)], '', None, '', .9, 1.5, V, 'host'),
        ([(782, by1 + 31), (860, by1 + 31)], '', None, '', 1.4, 1.8, V, 'arm'),
        ([(330, by1), (330, cy_ + 62)], 'edge bridge', (340, 442), 'start', 1.8, 2.5, B, 'api'),
        ([(620, by1), (620, 436), (480, 436), (480, cy_ + 62)], 'check-ins, incidents', (628, 452), 'start', 1.9, 2.8, B, 'api'),
        ([(528, cy_ + 31), (576, cy_ + 31)], '', None, '', 2.8, 3.2, B, 'runs'),
        ([(290, cy_ + 31), (242, cy_ + 31)], '', None, '', 2.8, 3.2, B, 'wo'),
        ([(150, cy_), (150, ny + 62)], 'hardware faults', (160, 278), 'start', 3.3, 3.9, A, 'tech'),
        ([(296, ny + 31), (334, ny + 31)], '', None, '', 3.9, 4.3, A, 'human'),
        ([(660, cy_), (660, ny + 62)], 'failure clusters', (670, 278), 'start', 3.3, 3.9, A, 'doc'),
        ([(692, ny + 31), (762, ny + 31)], '', None, '', 4.1, 4.5, A, 'board'),
        ([(560, ny + 62), (560, 268), (960, 268), (960, cy_)], 'rule or skill', (968, 290), 'start', 4.4, 5.6, A, 'gate'),
        ([(1050, cy_ + 62), (1050, 436), (740, 436), (740, by1)], 'new versions', (1058, 420), 'start', 5.7, 6.9, G, 'host'),
        ([(1012, ny + 31), (1150, ny + 31), (1150, by2 + 31), (806, by2 + 31)], 'job cards', (1142, by2 + 22), 'end', 6.4, 8.0, A, 'hermes'),
        ([(680, by2), (680, by1 + 62)], 'named skills', (690, by1 + 80), 'start', 8.0, 8.5, V, 'host'),
    ]
    arrivals = {}
    for pts, lab, lp, anchor, t0, t1, col, dest in flows:
        d = 'M' + ' L'.join(f'{x} {y}' for x, y in pts)
        body.insert(wires_at, f'<path d="{d}" fill="none" stroke="{P["faint"]}" stroke-width="3" stroke-dasharray="1 7" '
                              'stroke-linecap="round" stroke-linejoin="round"/>')
        if lab:
            body.append(halo_text(lp[0], lp[1], lab, 13.5, P['mute'], 700, anchor))
        arrivals.setdefault((dest, col), []).append(t1)
    # each node lights up in the packet's colour when one arrives
    for k, ((dest, col), times) in enumerate(arrivals.items()):
        cls = f'hl{k}'
        if dest == 'human':
            body.append(f'<circle class="{cls}" cx="{hx}" cy="{hy}" r="44" fill="none" stroke="{col}" stroke-width="4"/>')
        else:
            x, y, w = nodes[dest][:3]
            body.append(f'<rect class="{cls}" x="{x - 3}" y="{y - 3}" width="{w + 6}" height="68" rx="16" fill="none" '
                        f'stroke="{col}" stroke-width="3.5"/>')
        flash(css, cls, T, times)
    for i, (pts, _, _, _, t0, t1, col, _) in enumerate(flows):
        travel(css, body, f'f{i}', pts, t0, t1, col, T)
    return svg(W, H, 'The stack, from servo to company',
               'Three layers, animated in four beats. 1, heal offline: on the bench the STM32 sensor pod talks serial to podd and '
               'its 38 heal tools, the local healer agent (qwen3.5:4b) works only through those tools, and the arm host drives the '
               'SO-101. 2, report up: the edge bridge and the arm host send runs, check-ins and incidents to the Romulus cloud '
               '(FastAPI and Postgres), which files labelled runs and work orders. 3, agents act: the Naïve technician agent takes '
               'hardware faults and ends at one human click; the fleet-doctor agent reviews failure clusters and sends a rule or '
               'skill to the gate (replay, sim canary, promote) and to the Naïve board. 4, ship back: the gate sends new versions to '
               'the arm host, and the board sends job cards to the Hermes commander, which drives the arm with named skills.',
               '\n'.join(css), card(W, H) + ''.join(body), 6.0)


BUILDERS = {'hero.svg': hero, 'buddy.svg': mark, 'video.svg': video, 'loops.svg': loops, 'heal.svg': heal,
            'technician.svg': technician, 'fleet.svg': fleet, 'stats.svg': stats, 'stack.svg': stack}

if __name__ == '__main__':
    for name, fn in BUILDERS.items():
        (OUT / name).write_text(fn())
        print('wrote', OUT / name, f'{len(fn()) / 1024:.1f} KB')
