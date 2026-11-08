"""Render one benchmark item as the text the worker model reads, with an id for every line.

    text, idmap = render(item, profile="full", track="E")

Line format (docs/research/worker-benchmark-data.md §1.2). All times are host seconds relative to the
window start (now - 6 s), so pod lines older than the window are negative; `t=` is the pod's own ms clock.

    WINDOW src=sim-factory span=6.0s kinematics=on holding=no
    D05 +0.5 mm=38 us=220 st=ok pred=37.7          distance, 10 Hz (pred only with kinematics on)
    J01 +0.2 cmd=... pos=... load=... st=...        joints, cut to 5 Hz keeping each bin's worst error
    JS2 elbow_flex cmd=..  pos=.. maxerr=..         lite profile: one summary line per joint instead
    A1 +0..1 n=50 x=-12.10/2.41 ... mag=1001 stale=0  accelerometer, one line per second (mean/std)
    H01 -21.0 t=48211 mode=normal ...               pod health (last 30 s), at most 10 lines
    E01 -89.2 t=0 ev=boot rst=por ...               pod events (last 180 s), the 12 newest
    L link=up bytes_per_s=2480 valid=1.00           serial link
    INC1 src=watchdog kind=us_offset severity=warn summary="..."   track E only: rule findings now

`idmap` maps each id to the rows it stands for ({"src": "D", "ts": [...]}), so gold evidence (stored
as row timestamps) never depends on the profile or the track. Every row of the snapshot maps to a line.
"""
import math
import statistics

from . import l1 as l1mod

SPAN = 6.0
JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
H_FIELDS = ("mode", "boots", "streak", "crashes", "wdg", "rst", "loop_us", "stk_free", "vdda", "acc", "i2c_err",
            "us", "tx_drop", "clk", "acc_hz")
H_CHANGE = ("mode", "boots", "streak", "crashes", "wdg", "rst", "acc", "i2c_err", "us", "tx_drop", "clk", "acc_hz",
            "stk_free")
E_SKIP = ("ts", "type", "t", "ev", "_len")


def _num(x, nd=1):
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _rel(ts, t0):
    return f"{ts - t0:+.1f}"


def holding_from_j(row):
    """The sim's grasp rule (armsim/arm.py grasp_check): gripper > 4.0 open and pushing > 0.25 N*m."""
    return row["pos"][5] > 4.0 and abs(row["load"][5]) > 0.25


def j_bins(J, t0, now, hz=5):
    """[(row shown, [ts of every row in its bin])]: per 1/hz s bin, the row with the largest
    commanded-vs-measured error on joints 0-4 (so a stall never disappears)."""
    bins = {}
    for r in J:
        if not (t0 < r["ts"] <= now + 1e-9):
            continue
        b = int((r["ts"] - t0) * hz - 1e-9)
        e = max(abs(c - p) for c, p in zip(r["cmd"][:5], r["pos"][:5]))
        cur = bins.get(b)
        if cur is None:
            bins[b] = [e, r, [r["ts"]]]
        else:
            cur[2].append(r["ts"])
            if e > cur[0]:
                cur[0], cur[1] = e, r
    return [(v[1], v[2]) for _, v in sorted(bins.items())]


def _sd(xs):
    return statistics.pstdev(xs) if len(xs) > 1 else 0.0


def a_seconds(A, t0, now):
    """[(k, rows)] for each whole second k of the window that has accelerometer rows."""
    out = {}
    for r in A:
        if t0 < r["ts"] <= now + 1e-9:
            out.setdefault(min(int(r["ts"] - t0), int(SPAN) - 1), []).append(r)
    return sorted(out.items())


def _a_line(i, k, rows):
    def ms(v):
        m, s = statistics.fmean(v), _sd(v)
        return f"{m:.2f}/" + ("0" if s == 0 else f"{s:.2f}")
    xs, ys, zs = [r["x"] for r in rows], [r["y"] for r in rows], [r["z"] for r in rows]
    mag = statistics.fmean(math.sqrt(r["x"] ** 2 + r["y"] ** 2 + r["z"] ** 2) for r in rows)
    stale = sum(1 for r in rows if r.get("st") == "stale")
    return f"A{i} +{k}..{k + 1} n={len(rows)} x={ms(xs)} y={ms(ys)} z={ms(zs)} mag={mag:.0f} stale={stale}"


def _h_key(r):
    return tuple(r.get(f) for f in H_CHANGE) + ((r.get("loop_us") or 0) > 20000,
                                                not 3000 <= (r.get("vdda") or 3300) <= 3600)


def h_lines(H, now, every_s=3.0, cap=10):
    """[(row shown, [ts of the rows it stands for])]: one row every 3 s plus any row where a field
    detect.py reads changes; at most `cap` lines (the newest). Rows skipped map to the next shown one."""
    rows = sorted((r for r in H if now - 30 < r["ts"] <= now + 1e-9), key=lambda r: r["ts"])
    shown, last_t, last_k, pend = [], None, None, []
    for r in rows:
        pend.append(r["ts"])
        k = _h_key(r)
        if last_t is None or r["ts"] - last_t >= every_s - 1e-6 or k != last_k or r is rows[-1]:
            shown.append([r, pend])
            pend, last_t, last_k = [], r["ts"], k
    if len(shown) > cap:
        extra = [ts for _, p in shown[:len(shown) - cap + 1] for ts in p]
        shown = shown[len(shown) - cap:]
        shown[0][1] = extra
    return [(r, p) for r, p in shown]


def e_lines(E, now, cap=12):
    rows = sorted((r for r in E if now - 180 < r["ts"] <= now + 1e-9), key=lambda r: r["ts"])
    return rows[-cap:]


def _fmt_e(e):
    parts = []
    for k, v in e.items():
        if k in E_SKIP:
            continue
        if isinstance(v, str) and (" " in v or not v):
            v = '"%s"' % v
        parts.append(f"{k}={v}")
    return " ".join(parts)


def render(item, *, profile="full", track="E"):
    s = item["snapshot"]
    now = s["now"]
    t0 = now - SPAN
    kin = bool(item.get("kinematics"))
    idmap, out = {}, []
    J = [r for r in (s.get("J") or []) if t0 < r["ts"] <= now + 1e-9]
    hold = s.get("holding")
    if hold is None:
        hold = holding_from_j(J[-1]) if J else False
    out.append(f"WINDOW src={item.get('source', '?')} span={SPAN:.1f}s kinematics={'on' if kin else 'off'} "
               f"holding={'yes' if hold else 'no'}")
    D = [r for r in (s.get("D") or []) if t0 < r["ts"] <= now + 1e-9]
    w = 2 if len(D) < 100 else 3
    for i, r in enumerate(D, 1):
        lid = f"D{i:0{w}d}"
        line = f"{lid} {_rel(r['ts'], t0)} mm={_num(r.get('mm'))} us={_num(r.get('us'))} st={r.get('st')}"
        if kin:
            line += f" pred={_num(r.get('pred_mm'))}"
        out.append(line)
        idmap[lid] = {"src": "D", "ts": [r["ts"]]}
    if J and profile == "full":
        bins = j_bins(J, t0, now)
        w = 2 if len(bins) < 100 else 3
        f = lambda xs, n=1: ",".join(f"{x:.{n}f}" for x in xs)  # noqa: E731
        for i, (r, tss) in enumerate(bins, 1):
            lid = f"J{i:0{w}d}"
            out.append(f"{lid} {_rel(r['ts'], t0)} cmd={f(r['cmd'])} pos={f(r['pos'])} load={f(r['load'], 2)} "
                       f"st={','.join(str(x) for x in r['status'])}")
            idmap[lid] = {"src": "J", "ts": tss}
    elif J:
        for k, name in enumerate(JOINTS):
            err = [(abs(r["cmd"][k] - r["pos"][k]), r["ts"]) for r in J]
            e, et = max(err)
            cmd, pos = [r["cmd"][k] for r in J], [r["pos"][k] for r in J]
            lid = f"JS{k}"
            out.append(f"{lid} {name} cmd={min(cmd):.1f}..{max(cmd):.1f} pos={min(pos):.1f}..{max(pos):.1f} "
                       f"maxerr={e:.1f}@{_rel(et, t0)} load={max(abs(r['load'][k]) for r in J):.2f} "
                       f"st={max(r['status'][k] for r in J)}")
            idmap[lid] = {"src": "J", "ts": [r["ts"] for r in J], "joint": k}
    for i, (k, rows) in enumerate(a_seconds(s.get("A") or [], t0, now), 1):
        lid = f"A{i}"
        out.append(_a_line(i, k, rows))
        idmap[lid] = {"src": "A", "ts": [r["ts"] for r in rows]}
    for i, (r, tss) in enumerate(h_lines(s.get("H") or [], now), 1):
        lid = f"H{i:02d}"
        fields = " ".join(f"{k}={r[k]}" for k in H_FIELDS if k in r)
        out.append(f"{lid} {_rel(r['ts'], t0)} t={r.get('t')} {fields}")
        idmap[lid] = {"src": "H", "ts": tss}
    for i, e in enumerate(e_lines(s.get("E") or [], now), 1):
        lid = f"E{i:02d}"
        out.append(f"{lid} {_rel(e['ts'], t0)} t={e.get('t')} ev={e.get('ev')} {_fmt_e(e)}".rstrip())
        idmap[lid] = {"src": "E", "ts": [e["ts"]]}
    link = s.get("link")
    if link:
        line = f"L link={link.get('state')} bytes_per_s={link.get('bytes_per_s', '-')} valid={link.get('valid_ratio', 1.0):.2f}"
        if link.get("silent_s") is not None:
            line += f" silent_s={link['silent_s']:.0f}"
        out.append(line)
        idmap["L"] = {"src": "L", "ts": [now]}
    if track == "E":
        found = l1mod.alarms(l1mod.l1_findings(s))
        for i, fnd in enumerate(found[:8], 1):
            lid = f"INC{i}"
            summ = str(fnd.get("summary", "")).replace('"', "'")
            out.append(f'{lid} src={fnd["src"]} kind={fnd["kind"]} severity={fnd["severity"]} summary="{summ}"')
            idmap[lid] = {"src": "INC", "ts": []}
    return "\n".join(out), idmap
