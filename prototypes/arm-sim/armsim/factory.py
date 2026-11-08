"""The fault factory: many sim runs, each with one planted fault (or none), labeled, in the same
`arm-run/v1` shape the real-arm logger writes. It feeds level 3, and it scores level 1's rules.

    python -m armsim.factory --n 200 --workers 8 --out ../../data/sim-faults/batch1

Plain MuJoCo on CPU, one process per worker. Each run picks a task (pick up the block, or wave),
plants its fault just before a random step, and runs the same watchdog the arm host runs.
"""
import argparse
import collections
import json
import multiprocessing as mp
import os
import random
import time
from pathlib import Path

PLANS = {"pick": ["go_home", "reach", "approach", "grip", "lift"], "wave": ["go_home", "wave", "go_home"]}
JOINT_NAMES = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]


def sample_fault(rng):
    kind = rng.choice(["none", "us_frozen", "us_offset", "us_scale", "us_noisy",
                       "joint_stuck", "joint_offset", "joint_reversed"])
    params = {
        "none": {},
        "us_frozen": {},
        "us_offset": {"mm": round(rng.choice([-1, 1]) * rng.uniform(10, 25), 1)},
        "us_scale": {"factor": rng.choice([0.5, 0.75, 1.5])},
        "us_noisy": {"us": round(rng.uniform(8, 20), 1)},
        "joint_stuck": {"joint": rng.choice([1, 2, 3])},
        "joint_offset": {"joint": rng.choice([1, 2, 3]), "deg": round(rng.choice([-1, 1]) * rng.uniform(4, 10), 1)},
        "joint_reversed": {"joint": 3},
    }[kind]
    return kind, params


def one_run(job):
    idx, seed, out_dir = job
    os.environ["ARMSIM_SPEAK"] = "off"
    from . import prims
    from .arm import JOINTS, SimArm
    from .host import Skills, Watchdog
    from .record import RunRecorder

    rng = random.Random(seed)
    kind, params = sample_fault(rng)
    task = rng.choice(list(PLANS))
    plan = PLANS[task]
    onset_step = rng.randrange(1, len(plan))
    rec = RunRecorder(out_dir, task=task, joints=JOINTS, label={"fault": None if kind == "none" else kind, "params": params})
    arm = SimArm(recorder=rec, start=prims.REST, seed=seed)
    skills, dog = Skills(arm), Watchdog(arm, rec)
    onset, steps, outcome = None, [], "ok"
    for i, name in enumerate(plan):
        if i == onset_step and kind != "none":
            arm.inject(kind, **params)
            onset = round(arm.t, 2)
        try:
            r = getattr(skills, name)()
        except Exception as exc:              # one bad run must not stop the batch
            steps.append({"skill": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            outcome = f"error_{name}"
            break
        if not r.get("halted"):
            dog.arrival(r)                       # a move that didn't arrive opens joint_stall
        ok = r.get("arrived", True) and (name not in ("grip", "lift") or r.get("grasp", {}).get("holding"))
        steps.append({"skill": name, "ok": bool(ok), "halted": r.get("halted"), "aborted": r.get("aborted")})
        if r.get("halted") or dog.open():
            skills.park()
            outcome = "stopped_by_watchdog"
            break
        if not ok:
            outcome = f"failed_{name}"
            break
    detected = [i["kind"] for i in dog.incidents]
    label = {"fault": None if kind == "none" else kind, "params": params, "onset_s": onset, "onset_step": plan[onset_step]}
    rec.close(outcome, findings=[{k: i[k] for k in ("kind", "severity", "summary")} for i in dog.incidents], label=label)
    return {"idx": idx, "run_id": rec.run_id, "file": rec.path.name, "task": task, **label,
            "outcome": outcome, "detected": detected, "steps": steps}


def main():
    ap = argparse.ArgumentParser(description="Generate labeled sim runs with planted faults.")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[3] / "data" / "sim-faults" / time.strftime("batch-%Y%m%d-%H%M")))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(i, a.seed * 100_000 + i, str(out)) for i in range(a.n)]
    t0 = time.time()
    with mp.get_context("spawn").Pool(a.workers) as pool, (out / "index.jsonl").open("w") as index:
        rows = []
        for row in pool.imap_unordered(one_run, jobs):
            rows.append(row)
            index.write(json.dumps(row) + "\n")
            index.flush()
    secs = time.time() - t0
    by = collections.defaultdict(list)
    for r in rows:
        by[r["fault"] or "none"].append(r)
    print(f"{len(rows)} runs in {secs:.0f} s ({secs / max(1, len(rows)):.1f} s per run, {a.workers} workers) -> {out}")
    print(f"{'planted fault':15s} {'runs':>4s}  {'caught by level 1':>17s}  {'task failed':>11s}  {'looked fine':>11s}")
    for kind in sorted(by):
        rs = by[kind]
        caught = sum(1 for r in rs if r["detected"])
        failed = sum(1 for r in rs if not r["detected"] and r["outcome"] != "ok")
        fine = sum(1 for r in rs if not r["detected"] and r["outcome"] == "ok")
        print(f"{kind:15s} {len(rs):4d}  {caught:17d}  {failed:11d}  {fine:11d}")


if __name__ == "__main__":
    main()
