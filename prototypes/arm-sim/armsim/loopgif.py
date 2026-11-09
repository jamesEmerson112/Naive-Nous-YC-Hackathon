"""An animated diagram of the autonomous loop, for the README: an order becomes a Naïve card, Hermes runs the arm,
the arm's readings reach plain code, a stuck sensor parks the arm and heals, and nobody types after the order.

    PYTHONPATH=. .venv/bin/python -m armsim.loopgif                     # writes media/autonomous-loop.gif
    PYTHONPATH=. .venv/bin/python -m armsim.loopgif --at 12.5 20 --out /tmp/stills   # PNG stills, for layout checks

It's a drawing, not a screen recording, and each step in it happened in a run:
- story b --hermes in the sim: a Naïve card drove Hermes, which healed the stuck sensor, finished the job, commented
  RESULT: and moved the card to done (MORNING.md, the story table);
- a Naïve card + Hermes job on the real arm, done 107 s after the claim with nobody typing (HANDOFF-LOCAL.md, Sun
  10:27 AM).
The readings and the watchdog's sentence come from the sim's us_stuck escalation packet
(hermes-manager/samples/esc-us_stuck.json), the tool names from hermes-plugin/so101-arm, the card's title from
armsim/stories.py, and what the host sends Romulus from armsim/romulus.py.
"""
import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]                 # prototypes/arm-sim
OUT = ROOT / "media" / "autonomous-loop.gif"
W, H, S = 960, 540, 2                                       # drawn at S x, then scaled down for smooth edges
FPS = 10
END = 30.0                                                  # the last change; the final frame is then held
HOLD_MS = 4000

BG, GRID = (9, 13, 26), (14, 20, 37)
PANEL, EDGE = (15, 22, 41), (37, 49, 80)
BOX, BOX_EDGE = (21, 30, 54), (47, 62, 98)
TEXT, MUTED, DIM = (232, 238, 255), (150, 164, 200), (98, 110, 146)
CYAN, VIOLET, PINK, BLUE = (56, 214, 255), (167, 139, 250), (255, 111, 174), (99, 142, 255)
GREEN, RED, AMBER, BEAM = (52, 211, 153), (255, 92, 108), (251, 191, 36), (250, 214, 64)
LINK, JOINT = (214, 84, 64), (54, 66, 98)

_FACES = {
    "head": [("/System/Library/Fonts/Avenir Next Condensed.ttc", 2),
             ("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf", 0)],
    "bold": [("/System/Library/Fonts/Avenir Next.ttc", 2), ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0)],
    "body": [("/System/Library/Fonts/Avenir Next.ttc", 5), ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0)],
    "mono": [("/System/Library/Fonts/Menlo.ttc", 0), ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 0)],
    "monob": [("/System/Library/Fonts/Menlo.ttc", 1),
              ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 0)],
}
_fonts = {}


def font(kind, size):
    if (kind, size) not in _fonts:
        for path, index in _FACES[kind]:
            try:
                _fonts[kind, size] = ImageFont.truetype(path, size * S, index=index)
                break
            except OSError:
                continue
        else:
            _fonts[kind, size] = ImageFont.load_default(size * S)
    return _fonts[kind, size]


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def ease(u):
    u = clamp(u)
    return u * u * (3 - 2 * u)


def prog(t, t0, t1):
    return ease((t - t0) / (t1 - t0))


def lerp(a, b, u):
    return a + (b - a) * u


def mix(c1, c2, u):
    return tuple(round(lerp(a, b, u)) for a, b in zip(c1, c2))


def along(path, u):
    """The point a fraction u of the way along a polyline."""
    segs = [(a, b, math.dist(a, b)) for a, b in zip(path, path[1:])]
    left = clamp(u) * sum(s[2] for s in segs)
    for a, b, n in segs:
        if left <= n:
            return (lerp(a[0], b[0], left / n), lerp(a[1], b[1], left / n)) if n else a
        left -= n
    return path[-1]


class Canvas:
    """Drawing in the 960 x 540 layout's units, onto an S-times-larger image."""

    def __init__(self):
        self.im = Image.new("RGB", (W * S, H * S), BG)
        self.d = ImageDraw.Draw(self.im)

    def rect(self, box, fill=None, outline=None, r=10, width=1.0):
        x0, y0, x1, y1 = box
        self.d.rounded_rectangle([x0 * S, y0 * S, x1 * S, y1 * S], radius=r * S, fill=fill, outline=outline,
                                 width=max(1, round(width * S)))

    def line(self, pts, fill, width=1.5):
        self.d.line([(x * S, y * S) for x, y in pts], fill=fill, width=max(1, round(width * S)), joint="curve")

    def dot(self, xy, r, fill):
        x, y = xy
        self.d.ellipse([(x - r) * S, (y - r) * S, (x + r) * S, (y + r) * S], fill=fill)

    def text(self, xy, s, kind="body", size=14, fill=TEXT, anchor="la"):
        self.d.text((xy[0] * S, xy[1] * S), s, font=font(kind, size), fill=fill, anchor=anchor)

    def width(self, s, kind="body", size=14):
        return self.d.textlength(s, font=font(kind, size)) / S

    def wrap(self, s, kind, size, maxw):
        lines, cur = [], ""
        for word in s.split():
            nxt = f"{cur} {word}".strip()
            if cur and self.width(nxt, kind, size) > maxw:
                lines.append(cur)
                cur = word
            else:
                cur = nxt
        return lines + [cur]

    def frame(self):
        return self.im.resize((W, H), Image.LANCZOS)


# ---- layout ---------------------------------------------------------------------------------------------------
LANE_Y0, LANE_Y1 = 74, 392
LANES = [  # x0, x1, title, where it runs (DEMO-STEPS.md, "Where things run")
    (16, 196, "THE ARM", "SO-101, real or its sim twin"),
    (218, 458, "NEXT TO THE ARM", "plain code, no model"),
    (480, 730, "THE AGENTS", "Naïve online · Hermes on the Mac"),
    (752, 944, "ROMULUS", "the cloud: Docker on the Mac"),
]
HOST, WATCH, PODD, CONSOLE = (230, 120, 446, 158), (230, 166, 446, 204), (230, 212, 446, 250), (230, 260, 446, 382)
BOARD, HERMES = (492, 120, 718, 210), (492, 218, 718, 382)
CHECKIN, INCIDENTS, ORDERS = (764, 120, 932, 176), (764, 184, 932, 300), (764, 308, 932, 382)
COLS = {"todo": 528, "doing": 605, "done": 682}           # the board's column centres
BUS_Y = 404
P_TOOLS = [(492, 300), (469, 300), (469, 139), (446, 139)]                       # Hermes <-> arm host
P_JOINTS, P_POD = [(188, 139), (230, 139)], [(188, 231), (230, 231)]            # the arm -> host, podd
P_REPORT = [(338, 382), (338, BUS_Y), (741, BUS_Y), (741, 242), (764, 242)]      # arm host -> Romulus
P_CHECKIN = [(338, 382), (338, BUS_Y), (741, BUS_Y), (741, 148), (764, 148)]

# ---- the story, in seconds ------------------------------------------------------------------------------------
CAPTIONS = [  # t0, t1, step, colour of who acts, caption
    (0.0, 1.4, "", DIM, "One order, one stuck sensor, nobody typing: how the data moves."),
    (1.4, 4.6, "1", PINK, "An order lands on the Naïve board as an ARM JOB card."),
    (4.6, 7.6, "2", PINK, "The dispatcher claims the card and hands it to Hermes. From here on, nobody types."),
    (7.6, 11.5, "3", VIOLET, "Hermes runs the arm one skill at a time and never sends joint angles. "
                             "The wrist distance streams to plain code through podd."),
    (11.5, 14.8, "4", RED, "The watchdog (plain code, no model) sees “Distance stuck at 130 mm while the arm "
                           "moved 29 mm” and parks the arm."),
    (14.8, 17.6, "5", VIOLET, "Hermes gets the incident back. A guard refuses any move while it's open, so Hermes "
                              "reads the arm and calls arm_restart_sensor."),
    (17.6, 21.4, "6", GREEN, "podd restarts the sensor, and the watchdog checks it at home: fresh readings, "
                             "podd agrees. Healed."),
    (21.4, 26.4, "7", VIOLET, "Hermes redoes the job, posts RESULT: on the card and moves it to done."),
    (26.4, 29.4, "8", BLUE, "Romulus gets the incident and its log window, the evidence for later. "
                            "No work order: no person was needed."),
    (29.4, 99.0, "", GREEN, "Nobody types anything after the order. No model sits in the real-time loop."),
]
PHASES = [(0.0, "IDLE", DIM), (4.8, "WORKING", CYAN), (11.6, "PARKED", RED), (17.0, "HEALING", AMBER),
          (20.0, "HEALED", GREEN), (21.6, "WORKING", CYAN), (26.6, "DONE", GREEN)]
SKILLS = [(0.0, "waiting"), (7.9, "go_home"), (9.7, "reach"), (11.6, "park"), (13.0, "parked"),
          (16.5, "restart_sensor"), (18.0, "verify"), (20.0, "waiting"), (21.7, "reach"), (22.6, "approach"),
          (23.4, "grip"), (24.0, "lift"), (24.6, "release"), (25.1, "go_home"), (26.0, "waiting")]
WATCHDOG = [(0.0, "watching", GREEN), (11.5, "us_stuck: park", RED), (17.0, "checking", AMBER),
            (19.8, "verified", GREEN), (21.6, "watching", GREEN)]
PODD_STATE = [(0.0, "up", GREEN), (16.9, "us off, us on", AMBER), (18.2, "up", GREEN)]
CALLS = [  # Hermes's so101-arm tool calls: name, call, result, what came back, colour
    ("arm_go_home", 7.8, 9.2, "ok", TEXT), ("arm_reach", 9.6, 11.8, "parked", RED),
    ("arm_status", 15.0, 15.6, "us_stuck", AMBER), ("arm_restart_sensor", 16.4, 20.0, "ok", GREEN),
    ("arm_reach", 21.6, 22.4, "ok", TEXT), ("arm_approach", 22.5, 23.2, "ok", TEXT),
    ("arm_grip", 23.3, 23.8, "holding", TEXT), ("arm_lift", 23.9, 24.5, "ok", TEXT),
    ("arm_release", 24.6, 24.9, "ok", TEXT), ("arm_go_home", 25.0, 25.9, "ok", TEXT),
]
ROWS = [  # wrist distance rows from the us_stuck packet: time shown, row id, measured mm, predicted mm
    (8.4, "D22", 124, 124.4), (8.8, "D24", 128, 128.5), (9.2, "D26", 130, 130.0),
    (10.2, "D49", 130, 132.9), (10.6, "D53", 130, 137.4), (11.0, "D55", 130, 108.6), (11.4, "D59", 130, 104.7),
]
STUCK_FROM, CAUGHT, HEALED_AT = 10.2, 11.5, 19.8
NOTES = [(11.6, "INC us_stuck  critical", RED), (19.0, "verify: home      ok", GREEN),
         (19.4, "fresh readings    ok", GREEN), (19.8, "podd agrees       ok", GREEN)]
CHECKINS = [(2.0, "health ok", GREEN), (12.6, "health failing", RED), (20.6, "health ok", GREEN)]
CARD_MOVES = [(4.8, 5.6, "todo", "doing"), (26.6, 27.2, "doing", "done")]
CARD_IN, CLAIMED, RESULT_AT, REPORT = 2.0, 4.8, 26.6, (26.8, 28.0)

# ---- the arm (side view, 2-link IK with the gripper pointing down) ---------------------------------------------
SHOULDER, L1, L2, L3 = (58, 296), 82, 74, 30
TABLE_Y, BLOCK_X, BLOCK = 322, 146, 18
POSES = {"rest": (96, 272), "home": (122, 192), "reach": (146, 248), "approach": (146, 318), "lift": (146, 280)}
MOVES = [(8.2, 9.2, "rest", "home"), (9.9, 11.4, "home", "reach"), (12.0, 13.0, "reach", "rest"),
         (18.0, 19.0, "rest", "home"), (21.7, 22.4, "home", "reach"), (22.6, 23.2, "reach", "approach"),
         (24.0, 24.5, "approach", "lift"), (25.1, 25.9, "lift", "home")]
GRIP, RELEASE = (23.4, 23.8), (24.6, 24.8)


def latest(items, t, default=None):
    """The last (t0, ...) entry that has started by t."""
    out = default
    for it in items:
        if t >= it[0]:
            out = it
    return out


def tip_at(t):
    pose = POSES["rest"]
    for t0, t1, a, b in MOVES:
        if t >= t1:
            pose = POSES[b]
        elif t >= t0:
            u = prog(t, t0, t1)
            return lerp(POSES[a][0], POSES[b][0], u), lerp(POSES[a][1], POSES[b][1], u)
        else:
            break
    return pose


def ik(tip):
    wx, wy = tip[0], tip[1] - L3
    dx, dy = wx - SHOULDER[0], wy - SHOULDER[1]
    d = clamp(math.hypot(dx, dy), abs(L1 - L2) + 1e-3, L1 + L2 - 1e-3)
    a, b = math.atan2(dy, dx), math.acos((L1 * L1 + d * d - L2 * L2) / (2 * L1 * d))
    elbows = [(SHOULDER[0] + L1 * math.cos(th), SHOULDER[1] + L1 * math.sin(th)) for th in (a - b, a + b)]
    return min(elbows, key=lambda e: e[1]), (wx, wy)      # elbow up


def jaw_at(t):
    """Half the jaw opening: 14 open, 10 closed on the block."""
    if t < GRIP[0] or t >= RELEASE[1]:
        return 14.0
    if t < GRIP[1]:
        return lerp(14, 10, prog(t, *GRIP))
    if t < RELEASE[0]:
        return 10.0
    return lerp(10, 14, prog(t, *RELEASE))


def block_top(t, tip):
    if GRIP[1] <= t < RELEASE[0] + 0.1:
        return tip[1] - 14
    if t >= RELEASE[0] + 0.1:
        lifted = POSES["lift"][1] - 14
        return lerp(lifted, TABLE_Y - BLOCK, clamp((t - RELEASE[0] - 0.1) / 0.25) ** 2)
    return TABLE_Y - BLOCK


def draw_arm(c, t):
    tip = tip_at(t)
    elbow, wrist = ik(tip)
    top = block_top(t, tip)
    bx = tip[0] if GRIP[1] <= t < RELEASE[0] + 0.1 else BLOCK_X
    c.line([(26, TABLE_Y), (186, TABLE_Y)], (58, 72, 108), 2)
    c.rect((bx - 9, top, bx + 9, top + BLOCK), fill=(45, 170, 120), outline=(120, 230, 180), r=2, width=1)
    c.rect((38, 302, 78, TABLE_Y), fill=JOINT, outline=EDGE, r=5)
    c.line([SHOULDER, elbow], LINK, 11)
    c.line([elbow, wrist], LINK, 10)
    j = jaw_at(t)
    bar = tip[1] - 16
    c.line([wrist, (tip[0], bar)], LINK, 7)
    c.line([(tip[0] - j - 2, bar), (tip[0] + j + 2, bar)], (230, 120, 96), 3)
    for side in (-1, 1):
        c.line([(tip[0] + side * j, bar), (tip[0] + side * j, tip[1] + 2)], (230, 120, 96), 3)
    for p, r in ((SHOULDER, 7), (elbow, 6), (wrist, 5)):
        c.dot(p, r, JOINT)
        c.dot(p, r - 3, (120, 136, 176))
    sx, sy = tip[0] + 15, tip[1] - 19                      # the ultrasonic sensor on the wrist
    c.rect((sx - 4, sy - 3, sx + 4, sy + 3), fill=BEAM, r=1)
    if not (16.9 <= t < 18.2):                             # off while podd restarts it
        stuck = STUCK_FROM <= t < 16.9
        floor = top if abs(sx - bx) < 12 else TABLE_Y
        c.line([(sx, sy + 3), (sx, floor)], RED if stuck else BEAM, 1.2)
        row = latest(ROWS, t)
        if row and t < 16.9:
            c.text((sx + 5, (sy + floor) / 2), f"{row[2]} mm", "monob", 13, RED if stuck else BEAM, "lm")


# ---- the frame ----------------------------------------------------------------------------------------------------
def panel(c, x0, x1, title, where):
    c.rect((x0, LANE_Y0, x1, LANE_Y1), fill=PANEL, outline=EDGE, r=14)
    c.text((x0 + 12, LANE_Y0 + 9), title, "head", 16, TEXT)
    c.text((x0 + 12, LANE_Y0 + 29), where, "body", 13, MUTED)


def box(c, b, title, right="", right_color=MUTED, edge=BOX_EDGE, glow=None):
    if glow:
        c.rect((b[0] - 3, b[1] - 3, b[2] + 3, b[3] + 3), outline=mix(glow, PANEL, 0.55), r=11, width=2)
    c.rect(b, fill=BOX, outline=edge, r=9, width=1.2)
    c.text((b[0] + 10, b[1] + 19 if b[3] - b[1] <= 40 else b[1] + 15), title, "bold", 14, TEXT, "lm")
    if right:
        c.text((b[2] - 10, b[1] + 19 if b[3] - b[1] <= 40 else b[1] + 15), right, "mono", 13, right_color, "rm")


def lines_in(c, b, items, top=32, step=20, size=14):
    """The last lines that fit in a box, oldest at the top."""
    fit = int((b[3] - b[1] - top) // step)
    for k, (s, col) in enumerate(items[-fit:]):
        c.text((b[0] + 10, b[1] + top + k * step), s, "mono", size, col)


def moving_dot(c, path, t, t0, t1, color, r=4.5):
    if t0 <= t <= t1:
        u = (t - t0) / (t1 - t0)
        for k, fade in ((3, 0.75), (2, 0.55), (1, 0.3), (0, 0.0)):    # a short tail
            c.dot(along(path, max(0.0, u - k * 0.05)), r - k * 0.8, mix(color, PANEL, fade))


def draw(t):
    c = Canvas()
    for x in range(0, W, 32):
        c.line([(x, 0), (x, H)], GRID, 1)
    for y in range(0, H, 32):
        c.line([(0, y), (W, y)], GRID, 1)

    # header
    c.text((18, 12), "From the arm to the agents, and back", "head", 27, TEXT)
    c.text((19, 46), "A Naïve card, Hermes, plain-code checks and Romulus: one self-healing job, end to end",
           "body", 14, MUTED)
    c.rect((736, 12, 944, 60), fill=mix(PANEL, GREEN, 0.10), outline=mix(GREEN, PANEL, 0.35), r=12, width=1.2)
    c.text((752, 36), "PEOPLE AFTER THE ORDER", "head", 15, MUTED, "lm")
    c.text((930, 36), "0", "head", 34, GREEN, "rm")

    for x0, x1, title, where in LANES:
        panel(c, x0, x1, title, where)

    # connectors, drawn faintly so the links read even when nothing moves
    for path in (P_TOOLS, P_JOINTS, P_POD, P_REPORT, P_CHECKIN[-2:]):
        c.line(path, mix(EDGE, BG, 0.1), 1.5)

    # the arm
    draw_arm(c, t)
    _, phase, color = latest(PHASES, t)
    c.rect((34, 344, 178, 380), fill=mix(PANEL, color, 0.16), outline=color, r=18, width=1.5)
    c.text((106, 362), phase, "head", 18, color, "mm")

    # next to the arm
    _, skill = latest(SKILLS, t)
    busy = skill not in ("waiting", "parked")
    box(c, HOST, "arm host", skill, CYAN if busy else MUTED, glow=CYAN if busy else None)
    _, wd, wcol = latest(WATCHDOG, t)
    alarm = wcol is not GREEN
    box(c, WATCH, "watchdog", wd, wcol, edge=wcol if alarm else BOX_EDGE, glow=wcol if alarm else None)
    _, ps, pcol = latest(PODD_STATE, t)
    box(c, PODD, "podd", ps, pcol, edge=pcol if pcol is AMBER else BOX_EDGE, glow=AMBER if pcol is AMBER else None)
    box(c, CONSOLE, "readings", "mm   pred", MUTED)
    shown = [(rt, f"{rid} {mm:>4} mm {pred:>6}", RED if (mm and rt >= STUCK_FROM and t >= CAUGHT) else TEXT)
             for rt, rid, mm, pred in ROWS if t >= rt]
    shown += [(nt, s, mix(col, PANEL, 0.45) if s.startswith("INC") and t >= HEALED_AT else col)
              for nt, s, col in NOTES if t >= nt]
    lines_in(c, CONSOLE, [(s, col) for _, s, col in sorted(shown, key=lambda x: x[0])])
    streaming = 7.8 <= t < 16.9 or 18.2 <= t < 26.0
    if streaming:
        for k in range(3):                                 # readings flowing into podd and the host
            moving_dot(c, P_POD, t, *(lambda s: (s, s + 0.45))(math.floor(t / 0.3) * 0.3 - k * 0.15),
                       RED if STUCK_FROM <= t < 16.9 else BEAM, 3.2)
        if any(t0 <= t <= t1 for t0, t1, *_ in MOVES):
            moving_dot(c, P_JOINTS, t, math.floor(t / 0.25) * 0.25, math.floor(t / 0.25) * 0.25 + 0.4, CYAN, 3.2)

    # the agents: the Naïve board
    claimed = t >= CLAIMED
    box(c, BOARD, "Naïve board", "Arm jobs", PINK)
    for name, x in COLS.items():
        c.text((x, BOARD[1] + 38), name, "body", 13, MUTED, "mm")
    for x in (566, 643):
        c.line([(x, BOARD[1] + 30), (x, BOARD[3] - 8)], BOX_EDGE, 1)
    if t >= CARD_IN:
        col = "todo"
        cx = COLS["todo"]
        for t0, t1, a, b in CARD_MOVES:
            if t >= t1:
                col, cx = b, COLS[b]
            elif t >= t0:
                cx = lerp(COLS[a], COLS[b], prog(t, t0, t1))
        done = col == "done"
        fade = clamp((t - CARD_IN) / 0.5)
        edge = GREEN if done else PINK
        c.rect((cx - 34, 168, cx + 34, 202), fill=mix(BOX, mix(PANEL, edge, 0.22), fade),
               outline=mix(BOX, edge, fade), r=6, width=1.4)
        c.text((cx, 178), "ARM JOB", "head", 14, mix(BOX, TEXT, fade), "mm")
        c.text((cx, 193), "RESULT" if t >= RESULT_AT else ("claimed" if claimed else "pick up"),
               "body", 12, mix(BOX, GREEN if t >= RESULT_AT else MUTED, fade), "mm")

    # the agents: Hermes
    hermes_on = 6.4 <= t < 26.8
    box(c, HERMES, "Hermes", "LLM commander", VIOLET, edge=VIOLET if hermes_on else BOX_EDGE,
        glow=VIOLET if hermes_on else None)
    talk = []
    if t >= 6.5:
        talk.append(("card: pick up the block", PINK))
    for name, t0, t1, res, col in CALLS:
        if t >= t0:
            pad = name.ljust(13) if len(name) < 13 else name + " "
            talk.append((pad + (res if t >= t1 else "…"), col if t >= t1 else DIM))
    if t >= RESULT_AT:
        talk.append(("RESULT: posted, done", PINK))
    lines_in(c, HERMES, talk)
    moving_dot(c, [(605, 186), (605, 236)], t, 5.6, 6.4, PINK)                      # the card goes to Hermes
    moving_dot(c, [(605, 236), (COLS["doing"], 186)], t, 26.0, 26.6, PINK)           # RESULT: back to the card
    for name, t0, t1, *_ in CALLS:
        moving_dot(c, P_TOOLS, t, t0, t0 + 0.4, VIOLET)                             # the call
        moving_dot(c, P_TOOLS[::-1], t, t1 - 0.4, t1, CYAN)                         # what came back

    # Romulus
    arrived = [ci for ci in CHECKINS if t >= ci[0] + 1.0]          # a check-in shows once its dot lands
    pulse = arrived and t - arrived[-1][0] < 1.6
    box(c, CHECKIN, "check-in every 10 s", glow=BLUE if pulse else None)
    if arrived:
        c.text((CHECKIN[0] + 10, CHECKIN[1] + 39), arrived[-1][1], "mono", 14, arrived[-1][2], "lm")
    for t0, *_ in CHECKINS:
        moving_dot(c, P_CHECKIN, t, t0, t0 + 1.0, BLUE)
    got = t >= REPORT[1]
    box(c, INCIDENTS, "incidents", glow=BLUE if got and t < REPORT[1] + 1.2 else None)
    if got:
        c.text((INCIDENTS[0] + 10, INCIDENTS[1] + 40), "us_stuck resolved", "mono", 14, GREEN, "lm")
        for k, s in enumerate(("+ its log window:", "the distance rows,", "measured + predicted")):
            c.text((INCIDENTS[0] + 10, INCIDENTS[1] + 62 + k * 17), s, "body", 13, MUTED, "lm")
    else:
        c.text((INCIDENTS[0] + 10, INCIDENTS[1] + 40), "none yet", "mono", 14, DIM, "lm")
    moving_dot(c, P_REPORT, t, *REPORT, BLUE, 5)
    box(c, ORDERS, "work orders")
    c.text((ORDERS[0] + 10, ORDERS[1] + 40), "none needed" if got else "only on a hand-off", "mono", 14,
           GREEN if got else DIM, "lm")

    # caption
    _, _, step, scol, words = latest(CAPTIONS, t)
    final = step == "" and t >= CAPTIONS[-1][0]
    c.rect((16, 418, 944, 524), fill=mix(PANEL, scol, 0.10 if final else 0.04), outline=mix(scol, PANEL, 0.45),
           r=14, width=1.2)
    x = 34
    if step:
        c.dot((50, 448), 17, mix(PANEL, scol, 0.25))
        c.text((50, 448), step, "head", 20, scol, "mm")
        x = 80
    size = 21 if final else 19
    for k, s in enumerate(c.wrap(words, "bold" if final else "body", size, 944 - x - 24)):
        c.text((x, 436 + k * 28), s, "bold" if final else "body", size, GREEN if final else TEXT)
    if final:
        c.text((x, 500), "Drawn from real runs: story b with Hermes in the sim, and a Naïve card on the real arm.",
               "body", 14, MUTED)
    return c.frame()


def palette_from(times):
    """One palette for every frame, from a strip of frames that hold every state's colours."""
    sample = [draw(t) for t in times]
    strip = Image.new("RGB", (W, H * len(sample)))
    for k, im in enumerate(sample):
        strip.paste(im, (0, k * H))
    return strip.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)


def build(out, fps):
    pal = palette_from([0.0, 3.0, 6.0, 10.5, 12.5, 16.0, 17.5, 20.5, 23.5, 27.5, END])
    n = int(END * fps) + 1
    frames = [draw(k / fps).quantize(palette=pal, dither=Image.Dither.NONE) for k in range(n)]
    ms = [round(1000 / fps)] * (n - 1) + [HOLD_MS]
    out.parent.mkdir(parents=True, exist_ok=True)
    # optimize=True: Pillow stores each frame as the box that changed, with unchanged pixels transparent (the
    # palette keeps one index free for that).
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=ms, loop=0, optimize=True)
    return out


def main():
    ap = argparse.ArgumentParser(prog="python -m armsim.loopgif", description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=None, help=f"the GIF (default {OUT.relative_to(ROOT)}), or a folder for --at")
    ap.add_argument("--fps", type=int, default=FPS)
    ap.add_argument("--at", type=float, nargs="+", metavar="SECONDS", help="write PNG stills at these times instead")
    a = ap.parse_args()
    if a.at:
        folder = Path(a.out or ".")
        folder.mkdir(parents=True, exist_ok=True)
        for t in a.at:
            p = folder / f"loop-{t:05.1f}.png"
            draw(t).save(p)
            print(p)
        return
    out = build(Path(a.out) if a.out else OUT, a.fps)
    print(f"{out}  {out.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
