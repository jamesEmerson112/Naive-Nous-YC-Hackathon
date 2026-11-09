"""Arm-fault benchmark items from the MuJoCo sim: one planted fault per run, cut into 6 s windows.

`run_capture(seed)` is a fork of `armsim/factory.py:one_run` (factory.py stays unchanged). It runs the
same plan with the same random draws and the same watchdog, so seed 1 gives the v2 runs and seed 99 the
v3-heldout runs (`seed*100000+i`, factory.py:92). It also keeps what the factory drops: every distance
row with its full-precision `ts` and the grasp state the watchdog saw at that row, each skill's result
(with the load at its end, for `check_arrival`), and each incident's exact time (factory.py:69-71, 78).
Healthy runs get extra motion only after their plan is done (release, home, a 6.5 s hold), so the plan
part stays identical to the factory's.

Windows (docs/research/worker-benchmark-data.md §2.2): fault runs end at onset +1.5, +3, +5 s (kept only
up to the first incident) and at the first incident; one healthy pre-onset window when onset >= 6.5 s.
Healthy runs give hard negatives: holding the block, at home (every row out_of_range), at peak joint
speed, and the end of the wave. Window ends snap to a distance row, so level 1 replays exactly.

Needs the arm-sim .venv (MuJoCo). From prototypes/arm-sim:

    PYTHONPATH=. .venv/bin/python -m workerbench.gen_arm --seed 99 --n 600 --split test --workers 3
    PYTHONPATH=. .venv/bin/python -m workerbench.gen_arm --seed 1 --n 2000 --split dev --workers 3

It writes data/workerbench/sim-factory-<split>.jsonl and checks itself: level 1 replayed on each
incident window must name the watchdog's incident, nothing may fire before it, and the run-level tally
must match data/sim-faults/v2 (seed 1) or v3-heldout (seed 99) when that index is present.
"""
import argparse
import collections
import hashlib
import json
import multiprocessing as mp
import os
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
OUT = REPO / "data" / "workerbench"
SPAN = 6.0
JOINT_NAMES = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
INDEX = {1: REPO / "data" / "sim-faults" / "v2" / "index.jsonl",
         99: REPO / "data" / "sim-faults" / "v3-heldout" / "index.jsonl"}


class _Rec:
    """Stands in for RunRecorder: keeps joint rows in memory, writes nothing."""

    def __init__(self, run_id):
        self.run_id, self.J = run_id, []

    def joints(self, t, cmd, pos, load, status):
        self.J.append({"ts": round(t, 4), "cmd": [round(float(x), 2) for x in cmd],
                       "pos": [round(float(x), 2) for x in pos], "load": [round(float(x), 3) for x in load],
                       "status": [int(s) for s in status]})

    def distance(self, t, row):
        pass


_RESULT_KEYS = ("arrived", "worst_joint", "max_err_deg", "grasp", "aborted", "halted", "finding", "seconds")


def run_capture(seed, idx=0):
    """One fault-factory run (factory.one_run's draws, in its order), fully captured in memory."""
    os.environ["ARMSIM_SPEAK"] = "off"
    import random
    from armsim import prims
    from armsim.arm import JOINTS, SimArm
    from armsim.factory import PLANS, sample_fault
    from armsim.host import Skills, Watchdog

    rng = random.Random(seed)
    kind, params = sample_fault(rng)
    task = rng.choice(list(PLANS))
    plan = PLANS[task]
    onset_step = rng.randrange(1, len(plan))
    rec = _Rec(f"wb-{seed}")
    arm = SimArm(recorder=rec, start=prims.REST, seed=seed)
    skills, dog = Skills(arm), Watchdog(arm, rec)
    D = []
    arm.watchers.append(lambda a: D.append({**a.d_rows[-1], "hold": bool(a.grasp_check()["holding"])}))
    dog.on_open.append(lambda inc: inc.__setitem__("t_exact", arm.t))
    onset = onset_t = None
    steps, sk, outcome = [], [], "ok"

    def do(name, fn):
        t0 = arm.t
        r = fn()
        load = arm.load_nm(r["worst_joint"]) if r.get("worst_joint") else None
        sk.append({"name": name, "t0": t0, "t1": arm.t, "load_nm": load,
                   "result": {k: r[k] for k in _RESULT_KEYS if k in r}})
        return r

    for i, name in enumerate(plan):
        if i == onset_step and kind != "none":
            arm.inject(kind, **params)
            onset, onset_t = round(arm.t, 2), arm.t
        try:
            r = do(name, getattr(skills, name))
        except Exception as exc:                 # as the factory: one bad run must not stop the batch
            steps.append({"skill": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            outcome = f"error_{name}"
            break
        if not r.get("halted"):
            dog.arrival(r)
        ok = r.get("arrived", True) and (name not in ("grip", "lift") or r.get("grasp", {}).get("holding"))
        steps.append({"skill": name, "ok": bool(ok)})
        if r.get("halted") or dog.open():
            outcome = "stopped_by_watchdog"
            break
        if not ok:
            outcome = f"failed_{name}"
            break
    plan_end = arm.t
    detected = [i["kind"] for i in dog.incidents]
    phases = {}
    if outcome == "stopped_by_watchdog":
        skills.park()
    elif kind == "none" and outcome == "ok":
        # Hard-negative phases, after the plan so the plan matches the factory run.
        if task == "pick":
            phases["holding"] = sk[-1]["t1"]                     # end of the lift, block in the jaws
            do("release", skills.release)
            do("go_home", skills.go_home)
        t0 = arm.t
        arm.hold(6.5)
        phases["home"] = arm.t if not dog.incidents else None
        phases["home_from"] = t0
        if task == "wave":
            phases["motion"] = next(s["t1"] for s in sk if s["name"] == "wave")
        # peak joint speed during the plan (joints 0-4)
        best, best_t = -1.0, None
        J = [j for j in rec.J if j["ts"] <= plan_end]
        for a, b in zip(J, J[1:]):
            dt = b["ts"] - a["ts"]
            if dt <= 0 or b["ts"] < 1.0:
                continue
            v = max(abs(b["pos"][k] - a["pos"][k]) / dt for k in range(5))
            if v > best:
                best, best_t = v, b["ts"]
        phases["peak_speed"] = best_t
        phases["peak_deg_s"] = round(best, 1)
    incidents = [{"kind": i["kind"], "severity": i["severity"], "summary": i["summary"], "sim_t": i["sim_t"],
                  "t": i.get("t_exact", i["sim_t"])} for i in dog.incidents]
    return {"idx": idx, "seed": seed, "task": task, "kind": kind, "params": params, "plan": plan,
            "onset_s": onset, "onset_t": onset_t, "onset_step": plan[onset_step], "outcome": outcome,
            "detected": detected, "steps": steps, "plan_end": plan_end, "D": D, "J": rec.J, "skills": sk,
            "incidents": incidents, "phases": phases}


# ------------------------------------------------------------------------------- windows and labels

def snap_end(D, t):
    """The newest distance row's ts at or before t (the watchdog judges once per D row)."""
    best = None
    for r in D:
        if r["ts"] <= t + 1e-9:
            best = r["ts"]
        else:
            break
    return best


def window_ends(run):
    """[(end, role)]: role is 'fault', 'incident', 'pre_onset' or a hard-negative phase name."""
    D = run["D"]
    if not D:
        return []
    out = []
    first = next((i for i in run["incidents"] if i["t"] <= run["plan_end"] + 1e-9), None)
    if run["kind"] == "none":
        for ph in ("holding", "home", "peak_speed", "motion"):
            t = run["phases"].get(ph)
            if t is not None:
                e = snap_end(D, t)
                if e is not None and e >= SPAN / 2:      # at least 3 s of data in the window
                    out.append((e, ph))
        return out
    limit = first["t"] if first else run["plan_end"]
    if first:
        out.append((first["t"], "incident"))
    for dt in (1.5, 3.0, 5.0):
        e = snap_end(D, run["onset_t"] + dt)
        if e is not None and e <= limit + 1e-9 and all(abs(e - x) >= 0.5 for x, _ in out):
            out.append((e, "fault"))
    if run["onset_t"] >= 6.5:
        e = snap_end(D, run["onset_t"])
        if e is not None and all(abs(e - x) >= 0.5 for x, _ in out):
            out.append((e, "pre_onset"))
    return sorted(out)


def _decimate_j(J, hz=10):
    """J at 10 Hz for storage: in each bin keep the row with the largest cmd-vs-pos error (joints 0-4)."""
    bins = collections.OrderedDict()
    for r in J:
        b = int(r["ts"] * hz + 1e-9)
        e = max(abs(c - p) for c, p in zip(r["cmd"][:5], r["pos"][:5]))
        if b not in bins or e > bins[b][0]:
            bins[b] = (e, r)
    return [v[1] for v in bins.values()]


def snapshot(run, end, kinematics=True):
    t0 = end - SPAN
    D = []
    for r in run["D"]:
        if t0 < r["ts"] <= end + 1e-9:
            row = {"ts": r["ts"], "mm": r["mm"], "us": r["us"], "st": r["st"],
                   "pred_mm": r.get("pred_mm") if kinematics else None, "hold": r["hold"]}
            D.append(row)
    J = _decimate_j([r for r in run["J"] if t0 < r["ts"] <= end + 1e-9])
    skills = []
    for s in run["skills"]:
        if s["t1"] <= t0 or s["t0"] > end + 1e-9:
            continue
        done = s["t1"] <= end + 1e-9
        skills.append({"name": s["name"], "t0": round(s["t0"], 3), "t1": s["t1"] if done else None,
                       "load_nm": s["load_nm"] if done else None, "result": s["result"] if done else None})
    incs = [{k: i[k] for k in ("kind", "severity", "summary", "sim_t")} for i in run["incidents"]
            if i["t"] <= end + 1e-9]
    return {"now": end, "D": D, "J": J, "A": [], "H": [], "E": [], "link": None, "skills": skills,
            "incidents": incs, "holding": bool(D[-1]["hold"]) if D else False}


def _after(rows, onset):
    return [r for r in rows if r["ts"] > onset + 1e-9]


def _robust_sd(xs):
    med = statistics.median(xs)
    return 1.4826 * statistics.median(abs(x - med) for x in xs)


def observable(kind, params, D, J, onset, kinematics, hidden_pred=None):
    """(visible?, info) for a planted fault, judged on rows after onset (data report §2.2 table).
    `hidden_pred` maps ts -> the pose's prediction even when kinematics are off (to tell still from moving)."""
    Da = _after(D, onset)
    info = {}
    if kind in ("us_frozen",):
        if not Da:
            return False, info
        last = (Da[-1]["us"], Da[-1]["st"], Da[-1]["mm"])
        run = [r for r in Da if (r["us"], r["st"], r["mm"]) == last]
        same = len(run)
        in_range = sum(1 for r in run if r.get("pred_mm") is not None and 30 <= r["pred_mm"] <= 400)
        info = {"n_identical": same, "stuck_us": last[0], "stuck_mm": last[2], "st": last[1], "rows": run}
        has_echo = last[0] is not None
        if kinematics:
            vis = same >= 30 and (has_echo or in_range >= 12)
        else:
            vis = same >= 30 and has_echo         # zero jitter on a real echo time (inferred: also out_of_range)
        return vis, info
    if kind in ("us_offset", "us_scale", "joint_offset"):
        if not kinematics:
            return False, info
        rows = [r for r in Da if r["st"] == "ok" and r.get("pred_mm") is not None and 30 <= r["pred_mm"] <= 400]
        strict = len(rows) >= 12
        if not strict:
            # Too few rows in 30-400 mm for the table's rule, but level 1's residual uses any ok row with a
            # prediction: keep evidence and facts from those, in case level 1 fires (then it is observable).
            rows = [r for r in Da if r["st"] == "ok" and r.get("pred_mm") is not None]
            if not rows:
                return False, info
        res = [r["mm"] - r["pred_mm"] for r in rows]
        gap = statistics.median(res)
        ratio = statistics.median(r["mm"] / r["pred_mm"] for r in rows if r["pred_mm"] > 0)
        travel = max(r["pred_mm"] for r in rows) - min(r["pred_mm"] for r in rows)
        info = {"gap_mm": round(gap, 1), "ratio": round(ratio, 3), "travel_mm": round(travel, 1),
                "rows": [r for r in rows if abs(r["mm"] - r["pred_mm"]) >= 8] or rows,
                "pred_mm": round(statistics.median(r["pred_mm"] for r in rows), 1)}
        return strict and statistics.median(abs(x) for x in res) >= 8, info
    if kind == "joint_reversed":
        if not kinematics:
            return False, info
        rows = [r for r in Da if r.get("pred_mm") is not None and 30 <= r["pred_mm"] <= 400]
        bad = [r for r in rows if r["st"] != "ok" or abs(r["mm"] - r["pred_mm"]) >= 8]
        ok = [r for r in rows if r["st"] == "ok"]
        info = {"rows": bad, "no_echo_n": sum(1 for r in rows if r["st"] != "ok"), "n": len(rows),
                "pred_mm": round(statistics.median(r["pred_mm"] for r in rows), 1) if rows else None,
                "gap_mm": round(statistics.median(r["mm"] - r["pred_mm"] for r in ok), 1) if ok else None}
        return len(bad) >= 12, info
    if kind == "us_noisy":
        if kinematics:
            rows = [r for r in Da if r["st"] == "ok" and r.get("pred_mm") is not None]
            if len(rows) < 12:
                return False, info
            res = [r["us"] - r["pred_mm"] * 2000 / 343 for r in rows]
        else:
            hp = hidden_pred or {}
            rows = [r for r in Da if r["st"] == "ok" and hp.get(r["ts"]) is not None]
            if len(rows) < 12 or max(hp[r["ts"]] for r in rows) - min(hp[r["ts"]] for r in rows) > 2.0:
                return False, info
            res = [r["us"] for r in rows]
        sd = _robust_sd(res)
        med = statistics.median(res)
        info = {"std_us": round(sd, 1), "std_mm": round(sd * 343 / 2000, 2),
                "rows": [r for r, x in zip(rows, res) if abs(x - med) > 8] or rows}
        return sd >= 4.0, info
    if kind == "joint_stuck":
        j = params["joint"]
        Ja = _after(J, onset)
        off = [r for r in Ja if abs(r["cmd"][j] - r["pos"][j]) >= 3.0]
        span = (off[-1]["ts"] - off[0]["ts"]) if len(off) >= 2 else 0.0
        info = {"rows_j": off, "short_by_deg": round(max((abs(r["cmd"][j] - r["pos"][j]) for r in Ja), default=0), 1),
                "joint": JOINT_NAMES[j], "joint_idx": j}
        return span >= 0.5, info
    return False, info


def facts(kind, params, info):
    """Gold facts for the message rubric (score.message_score): numbers a good message would quote."""
    f = {}
    if kind == "us_frozen":
        f = {"stuck_us": info.get("stuck_us"), "stuck_mm": info.get("stuck_mm"), "n_identical": info.get("n_identical")}
    elif kind in ("us_offset", "us_scale", "joint_offset"):
        f = {"gap_mm": info.get("gap_mm"), "ratio": info.get("ratio"), "pred_mm": info.get("pred_mm")}
        if kind == "us_offset":
            f["offset_mm"] = params["mm"]
        if kind == "us_scale":
            f["factor_ratio"] = params["factor"]
        if kind == "joint_offset":
            f.update(joint=JOINT_NAMES[params["joint"]], joint_idx=params["joint"], offset_deg=params["deg"])
    elif kind == "joint_reversed":
        f = {"joint": JOINT_NAMES[params["joint"]], "joint_idx": params["joint"], "no_echo_n": info.get("no_echo_n"),
             "pred_mm": info.get("pred_mm"), "gap_mm": info.get("gap_mm")}
    elif kind == "us_noisy":
        f = {"std_us": info.get("std_us"), "std_mm": info.get("std_mm"), "noise_us": params["us"]}
    elif kind == "joint_stuck":
        f = {"joint": info.get("joint"), "joint_idx": info.get("joint_idx"), "short_by_deg": info.get("short_by_deg")}
    return {k: v for k, v in f.items() if v is not None}


def _id(run, k, role, kin):
    return f"arm-{run['seed']}-w{k}-{role}-{'k1' if kin else 'k0'}"


def make_items(run, split, kinematics=(True,)):
    """Every candidate item of one run (selection by kind and l1 result happens in main())."""
    from workerbench import kinds, l1
    items = []
    hidden = {r["ts"]: r.get("pred_mm") for r in run["D"]}
    for k, (end, role) in enumerate(window_ends(run)):
        for kin in kinematics:
            snap = snapshot(run, end, kinematics=kin)
            if len(snap["D"]) < 10:
                continue
            fault = run["kind"] != "none" and role in ("fault", "incident")
            meta = {"seed": run["seed"], "idx": run["idx"], "task": run["task"], "variant": run["kind"],
                    "run_id": f"factory-{run['seed']}", "role": role, "onset_step": run["onset_step"],
                    "detected": run["detected"]}
            if fault:
                kind = run["kind"]
                vis, info = observable(kind, run["params"], snap["D"], snap["J"], run["onset_t"], kin, hidden)
                accept = {kind}
                if kind in ("us_offset", "us_scale") and info.get("travel_mm", 99) < 20:
                    accept = {"us_offset", "us_scale"}     # at one height an offset and a scale look the same
                ev = {}
                if info.get("rows"):
                    ev["D"] = [r["ts"] for r in info["rows"]]
                if info.get("rows_j"):
                    ev["J"] = [r["ts"] for r in info["rows_j"]]
                sev = kinds.GOLD_SEVERITY[kind]
                label = {"status": "fault", "kind": kind, "accept": sorted(accept), "route": kinds.ROUTE[kind],
                         "severity": sev, "escalate": sev != "info", "observable": bool(vis),
                         "hard_negative": None, "evidence_rows": ev, "facts": facts(kind, run["params"], info),
                         "onset_s": run["onset_s"], "since_onset_s": round(end - run["onset_t"], 2),
                         "params": run["params"], "label_source": "planted"}
            else:
                label = {"status": "healthy", "kind": "none", "accept": ["none"], "route": "none",
                         "severity": "info", "escalate": False, "observable": True,
                         "hard_negative": None if role == "pre_onset" else role, "evidence_rows": {}, "facts": {},
                         "onset_s": run["onset_s"], "since_onset_s": None, "params": run["params"],
                         "label_source": "planted"}
            l1_all = [f["kind"] for f in l1.alarms(l1.l1_findings(snap, which="all"))]
            l1_arm = [f["kind"] for f in l1.alarms(l1.l1_findings(snap, which="arm"))]
            if fault and l1_arm:
                label["observable"] = True               # a rule saw it, so it is visible
            meta.update(l1=l1_all, l1_arm=l1_arm)
            items.append({"id": f"{split}-" + _id(run, k, role, kin), "split": split, "source": "sim-factory",
                          "kinematics": kin, "snapshot": snap, "label": label, "meta": meta})
    return items


def _job(args):
    idx, seed, split, kin = args
    run = run_capture(seed, idx)
    items = make_items(run, split, kin)
    # self-check, kinematics on: the incident window names the watchdog's incident; nothing earlier fires
    checks = {"inc_ok": 0, "inc_bad": [], "early_fire": []}
    for it in items:
        if not it["kinematics"]:
            continue
        role = it["meta"]["role"]
        if role == "incident":
            first = run["incidents"][0]["kind"]
            got = it["meta"]["l1_arm"][:1]
            if got == [first]:
                checks["inc_ok"] += 1
            else:
                checks["inc_bad"].append((it["id"], first, got))
        elif role in ("fault", "pre_onset") and it["meta"]["l1_arm"]:
            checks["early_fire"].append((it["id"], it["meta"]["l1_arm"]))
    summary = {k: run[k] for k in ("idx", "seed", "task", "kind", "onset_s", "outcome", "detected")}
    summary["detected_plan"] = [i["kind"] for i in run["incidents"] if i["t"] <= run["plan_end"] + 1e-9]
    return summary, items, checks


def _h(s):
    return hashlib.sha1(s.encode()).hexdigest()


def select(items, *, per_kind, latent_per_kind, healthy, kin_off_per_kind, kin_off_healthy):
    """Deterministic caps (sha1 of the id): per arm kind, observable windows with at least half missed
    by level 1; a few latent ones; healthy windows (all hard negatives first); a small kinematics-off slice."""
    on = [i for i in items if i["kinematics"]]
    off = [i for i in items if not i["kinematics"]]
    out = []
    by = collections.defaultdict(list)
    for it in sorted(on, key=lambda i: _h(i["id"])):
        by[it["label"]["kind"]].append(it)
    for kind, its in sorted(by.items()):
        if kind == "none":
            hard = [i for i in its if i["label"]["hard_negative"]]
            clean = [i for i in its if not i["label"]["hard_negative"]]
            out += (hard + clean)[:healthy]
            continue
        obs = [i for i in its if i["label"]["observable"]]
        missed = [i for i in obs if not i["meta"]["l1"]]
        caught = [i for i in obs if i["meta"]["l1"]]
        n_m = min(len(missed), max(per_kind // 2, per_kind - len(caught)))
        out += missed[:n_m] + caught[:per_kind - n_m]
        out += [i for i in its if not i["label"]["observable"]][:latent_per_kind]
    byo = collections.defaultdict(list)
    for it in sorted(off, key=lambda i: _h(i["id"])):
        byo[it["label"]["kind"]].append(it)
    for kind, its in sorted(byo.items()):
        out += its[:kin_off_healthy if kind == "none" else kin_off_per_kind]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=99)
    ap.add_argument("--n", type=int, default=600)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--split", default="test")
    ap.add_argument("--per-kind", type=int, default=60)
    ap.add_argument("--latent", type=int, default=12, help="latent (invisible) fault windows kept per kind")
    ap.add_argument("--healthy", type=int, default=300, help="cap on kinematics-on healthy windows")
    ap.add_argument("--kinematics", default="on,off")
    ap.add_argument("--kin-off-per-kind", type=int, default=10)
    ap.add_argument("--kin-off-healthy", type=int, default=30)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    kin = tuple(m == "on" for m in a.kinematics.split(","))
    jobs = [(i, a.seed * 100_000 + i, a.split, kin) for i in range(a.n)]
    t0 = time.time()
    runs, items = [], []
    checks = {"inc_ok": 0, "inc_bad": [], "early_fire": []}
    with mp.get_context("spawn").Pool(a.workers) as pool:
        for summary, its, ch in pool.imap_unordered(_job, jobs, chunksize=4):
            runs.append(summary)
            items += its
            checks["inc_ok"] += ch["inc_ok"]
            checks["inc_bad"] += ch["inc_bad"]
            checks["early_fire"] += ch["early_fire"]
    runs.sort(key=lambda r: r["idx"])
    print(f"{len(runs)} runs in {time.time() - t0:.0f} s, {len(items)} candidate windows")
    print(f"self-check: incident windows where level 1 replays the watchdog's incident: {checks['inc_ok']}, "
          f"mismatches: {len(checks['inc_bad'])}, level 1 firing before the incident: {len(checks['early_fire'])}")
    for x in (checks["inc_bad"] + checks["early_fire"])[:10]:
        print("  ", x)
    # run-level tally, and the comparison with the factory's own index for this seed
    tally = collections.defaultdict(lambda: [0, 0])
    for r in runs:
        tally[r["kind"]][0] += 1
        tally[r["kind"]][1] += bool(r["detected_plan"])
    print("planted fault    runs  level 1 alarmed (run level, plan only)")
    for k in sorted(tally):
        print(f"  {k:15s} {tally[k][0]:4d}  {tally[k][1]:4d}")
    idx_path = INDEX.get(a.seed)
    if idx_path and idx_path.exists():
        ref = {json.loads(l)["idx"]: json.loads(l) for l in idx_path.open()}
        same = sum(1 for r in runs if r["idx"] in ref and ref[r["idx"]]["detected"] == r["detected_plan"]
                   and (ref[r["idx"]]["fault"] or "none") == r["kind"])
        diff = [r["idx"] for r in runs if r["idx"] in ref and ref[r["idx"]]["detected"] != r["detected_plan"]]
        print(f"vs {idx_path.relative_to(REPO)}: {same} of {sum(1 for r in runs if r['idx'] in ref)} runs identical "
              f"(fault kind and detected list); differing idx: {diff[:20]}")
    chosen = select(items, per_kind=a.per_kind, latent_per_kind=a.latent, healthy=a.healthy,
                    kin_off_per_kind=a.kin_off_per_kind, kin_off_healthy=a.kin_off_healthy)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"sim-factory-{a.split}.jsonl"
    with path.open("w") as f:
        for it in sorted(chosen, key=lambda i: i["id"]):
            f.write(json.dumps(it, separators=(",", ":")) + "\n")
    c = collections.Counter((i["label"]["kind"], i["kinematics"], i["label"]["observable"], bool(i["meta"]["l1"]))
                            for i in chosen)
    print(f"wrote {len(chosen)} items -> {path}")
    print("kind             kin  observable  l1-fired  n")
    for (k, kn, ob, fired), n in sorted(c.items()):
        print(f"  {k:15s} {'on ' if kn else 'off'}  {str(ob):10s}  {str(fired):8s}  {n}")
    hn = collections.Counter(i["label"]["hard_negative"] or "clean" for i in chosen if i["label"]["status"] == "healthy")
    print("healthy groups:", dict(hn))
    return 0 if not checks["inc_bad"] else 1


if __name__ == "__main__":
    sys.exit(main())
