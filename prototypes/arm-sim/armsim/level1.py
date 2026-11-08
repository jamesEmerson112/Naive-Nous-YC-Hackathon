"""Level 1: plain code turns number streams into one-sentence findings (no LLM).

Findings use the exact shape of Olir's `firmware/healer/healer/detect.py`:
{kind, severity, summary, evidence, hypotheses, next_steps}, over `podd`-style rows where every
record has a host timestamp `ts`. Distance rows are the pod's `D` rows: {ts, t, mm, us, st}.

- `check_ultrasonic_frozen` needs no arm. It mirrors detect.py's `acc_frozen`: a real sensor
  always has a noise floor (the HC-SR04's echo time jitters by about 1 us), so zero variation
  over 3 s means stale data. This is the rule Olir's `check_ultrasonic` is missing.
- `check_distance_residual` uses the arm: it compares what the sensor reads with what forward
  kinematics says it should read (rows with a `pred_mm` field), and names the pattern
  (note, "Olir's residual check works").
"""
import statistics


def _f(kind, severity, summary, evidence=None, hypotheses=None, next_steps=None):
    return {"kind": kind, "severity": severity, "summary": summary, "evidence": evidence or {},
            "hypotheses": hypotheses or [], "next_steps": next_steps or []}


def _std(xs):
    return statistics.pstdev(xs) if len(xs) > 1 else 0.0


def _window(recs, now, seconds):
    return [r for r in recs if r["ts"] >= now - seconds]


def check_ultrasonic_frozen(s, *, seconds=4.0, min_rows=30):
    """Pod-only rule. `s` is a podd-style snapshot: {"now": ts, "D": [rows]}."""
    ok = [d for d in _window(s["D"], s["now"], seconds) if d.get("st") == "ok"]
    if len(ok) < min_rows:
        return []
    us = [d["us"] for d in ok]
    if _std(us) == 0:
        return [_f("us_frozen", "critical",
                   "Ultrasonic output is frozen: %d readings, echo time identical at %d us (zero noise)" % (len(ok), us[0]),
                   {"n": len(ok), "us": us[0], "mm": ok[0]["mm"], "std_us": 0.0},
                   ["the pod is re-sending a cached distance (sensor read loop stalled)",
                    "a jaw or held object sits in the beam at a fixed distance"],
                   ["pod_cmd 'us off' then 'us on' (restart the sensor)", "pod_cmd 'reset'",
                    "re-read at another gripper position"])]
    return []


def check_distance_residual(s, *, seconds=2.0, min_rows=12, moved_mm=10.0):
    """Arm-aware rule. D rows here carry `pred_mm`: the distance the arm's pose predicts."""
    rows = [d for d in _window(s["D"], s["now"], seconds) if d.get("st") == "ok" and d.get("pred_mm") is not None]
    if len(rows) < min_rows:
        return []
    # Stuck: the newest readings all have the same echo time while the arm's pose says the
    # distance changed. Look at the trailing run only, so rows from before a freeze don't hide it.
    tail = [rows[-1]]
    for d in reversed(rows[:-1]):
        if d["us"] != rows[-1]["us"]:
            break
        tail.append(d)
    tail_moved = max(d["pred_mm"] for d in tail) - min(d["pred_mm"] for d in tail)
    if len(tail) >= min_rows and tail_moved >= moved_mm:
        return [_f("us_stuck", "critical",
                   "Distance stuck at %d mm while the arm moved %d mm" % (tail[0]["mm"], round(tail_moved)),
                   {"n": len(tail), "stuck_mm": tail[0]["mm"], "stuck_us": tail[0]["us"], "arm_moved_mm": round(tail_moved),
                    "pred_mm": [round(tail[-1]["pred_mm"]), round(tail[0]["pred_mm"])]},
                   ["the sensor's reading froze (stale data)", "a jaw or held object is in the beam"],
                   ["restart the sensor (pod_cmd 'us off' then 'us on')", "re-read at another gripper position"])]
    if len(tail) >= 5:
        return []      # the reading has stopped changing: let the stuck test above decide
    meas = [float(d["mm"]) for d in rows]
    pred = [float(d["pred_mm"]) for d in rows]
    moved = max(pred) - min(pred)
    if moved < moved_mm:
        return []
    # Fit measured = a * predicted + b, but only when the reading actually follows the pose
    # (correlation >= 0.95); a flat or erratic reading is not a calibration or scale error.
    mp, mm_ = statistics.fmean(pred), statistics.fmean(meas)
    sp, sm = _std(pred), _std(meas)
    if sm < 1.0 or sp == 0:
        return []
    r = sum((p - mp) * (m - mm_) for p, m in zip(pred, meas)) / (len(rows) * sp * sm)
    if r < 0.95:
        return []
    a = r * sm / sp
    b = mm_ - a * mp
    ev = {"n": len(rows), "slope": round(a, 3), "offset_mm": round(b, 1), "r": round(r, 3), "arm_moved_mm": round(moved)}
    if abs(a - 1) > 0.2:
        return [_f("us_scale", "critical", "Reads %.2fx the kinematics distance" % a, ev,
                   ["a clock or units bug in the pod firmware (for example a half-speed clock halves distances)"],
                   ["chip_clock", "fw_rollback", "src_diff"])]
    if abs(b) > 8 and abs(a - 1) <= 0.1:
        return [_f("us_offset", "warn", "Reads %d mm %s at every height" % (abs(round(b)), "long" if b > 0 else "short"), ev,
                   ["the distance calibration offset is wrong", "the sensor mount moved"],
                   ["correct the offset, then re-sweep 5 heights"])]
    return []


def run(s):
    findings = check_ultrasonic_frozen(s) + check_distance_residual(s)
    order = {"critical": 0, "warn": 1, "info": 2}
    return sorted(findings, key=lambda f: order.get(f["severity"], 3))
