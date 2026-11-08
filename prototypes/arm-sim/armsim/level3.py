"""Healer level 3, learned from data (docs/healer-data-levels.md, L3a/L3b): a small classifier that
audits a finished run as a second opinion beside the level-1 rules. Plain CPU, no GPU, no LLM.

    python -m armsim.level3 train --data ../../data/sim-faults/v2      # writes runs/level3.joblib + a report
    python -m armsim.level3 audit runs/<run>.jsonl                     # one run -> verdict JSON

It never moves the arm and never sits in the control loop: the watchdog's rules still park the arm.
L3 reads the whole arm-run/v1 record after a job (the J and D rows) and says "healthy" or which fault
it smells, with a probability. A finding opens nothing by itself; the dispatcher logs it and the
commander may run arm_check (inferred design, docs/healer-data-levels.md).

The gate: zero false alarms on healthy runs. The alarm threshold on P(healthy) is set on a
calibration split (the lowest P(healthy) any healthy calibration run got, minus a margin), and the
report says how many held-out healthy runs it flags.
"""
import argparse
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "runs" / "level3.joblib"
KINDS = ["none", "joint_offset", "joint_reversed", "joint_stuck", "us_frozen", "us_noisy", "us_offset", "us_scale"]
NJ = 6


def _stats(x):
    x = np.asarray(x, dtype=float)
    if x.size == 0:
        return [0.0, 0.0, 0.0, 0.0]
    return [float(np.mean(x)), float(np.std(x)), float(np.max(np.abs(x))), float(np.median(np.abs(x)))]


def load_rows(path):
    J, D, label = [], [], None
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            k = r.get("kind")
            if k == "J":
                J.append(r)
            elif k == "D":
                D.append(r)
            elif k == "run":
                label = (r.get("label") or {}).get("fault")
    return J, D, label or "none"


def features(J, D):
    """A fixed-length vector from one run. Names in FEATURE_NAMES."""
    f = []
    # --- distance sensor (D rows): measured vs the kinematic prediction, jitter, frozen runs
    ok = [r for r in D if r.get("st") == "ok" and r.get("pred_mm") is not None and r.get("mm") is not None]
    res = [r["mm"] - r["pred_mm"] for r in ok]
    ratio = [r["mm"] / r["pred_mm"] for r in ok if r["pred_mm"] and r["pred_mm"] > 20]
    f += _stats(res)
    f += [float(np.median(ratio)) if ratio else 1.0, float(np.std(ratio)) if ratio else 0.0]
    us = [r.get("us") for r in D if r.get("us") is not None]
    dus = np.diff(us) if len(us) > 1 else np.zeros(1)
    f += [float(np.mean(dus == 0)), float(np.std(dus)), _longest_run(dus == 0) / max(1, len(dus))]
    in_range = [r for r in D if r.get("pred_mm") is not None and 20 < r["pred_mm"] < 300]
    f += [float(np.mean([r.get("st") != "ok" for r in in_range])) if in_range else 0.0, float(len(D))]
    if len(ok) > 3:
        p, m = np.array([r["pred_mm"] for r in ok]), np.array([r["mm"] for r in ok])
        c = float(np.corrcoef(p, m)[0, 1]) if np.std(p) > 1e-6 and np.std(m) > 1e-6 else 0.0
        f += [0.0 if math.isnan(c) else c]
    else:
        f += [0.0]
    # --- joints (J rows): tracking error, load, velocity sign vs command
    cmd = np.array([r["cmd"] for r in J], dtype=float) if J else np.zeros((1, NJ))
    pos = np.array([r["pos"] for r in J], dtype=float) if J else np.zeros((1, NJ))
    load = np.array([r.get("load") or [0] * NJ for r in J], dtype=float) if J else np.zeros((1, NJ))
    err = cmd - pos
    for j in range(NJ - 1):                       # the gripper's error is mostly the grasp: leave it out
        e = err[:, j]
        f += [float(np.mean(np.abs(e))), float(np.max(np.abs(e))), float(np.abs(e[-1]))]
        dc, dp = np.diff(cmd[:, j]), np.diff(pos[:, j])
        moving = np.abs(dc) > 1e-3
        f += [float(np.mean(np.sign(dc[moving]) != np.sign(dp[moving]))) if moving.any() else 0.0]
        f += [float(np.max(np.abs(load[:, j])))]
    f += [float(len(J))]
    return f


FEATURE_NAMES = (["res_mean", "res_std", "res_max", "res_med", "ratio_med", "ratio_std",
                  "us_same_frac", "us_diff_std", "us_same_longest", "noecho_inrange", "d_rows", "pred_corr"]
                 + [f"j{j}_{n}" for j in range(NJ - 1) for n in ("err_mean", "err_max", "err_end", "wrong_dir", "load_max")]
                 + ["j_rows"])


def _longest_run(mask):
    best = cur = 0
    for m in mask:
        cur = cur + 1 if m else 0
        best = max(best, cur)
    return best


def dataset(data_dir):
    data_dir = Path(data_dir)
    index = {}
    if (data_dir / "index.jsonl").exists():
        for line in open(data_dir / "index.jsonl"):
            r = json.loads(line)
            index[r["file"]] = r
    X, y, meta = [], [], []
    for p in sorted(data_dir.glob("*.jsonl")):
        if p.name == "index.jsonl":
            continue
        J, D, label = load_rows(p)
        X.append(features(J, D))
        y.append(label)
        m = index.get(p.name, {})
        meta.append({"file": p.name, "l1": bool(m.get("detected")), "outcome": m.get("outcome")})
    return np.array(X), np.array(y), meta


def train(data_dir, out=MODEL, seed=7):
    import joblib
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.model_selection import train_test_split

    X, y, meta = dataset(data_dir)
    idx = np.arange(len(y))
    tr, rest = train_test_split(idx, test_size=0.4, stratify=y, random_state=seed)
    cal, te = train_test_split(rest, test_size=0.5, stratify=y[rest], random_state=seed)
    clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.08, max_leaf_nodes=31,
                                         class_weight="balanced", random_state=seed)
    clf.fit(X[tr], y[tr])
    none_col = list(clf.classes_).index("none")
    p_none_cal = clf.predict_proba(X[cal])[:, none_col]
    healthy_cal = p_none_cal[y[cal] == "none"]
    threshold = float(max(0.0, healthy_cal.min() - 0.02)) if healthy_cal.size else 0.5   # alarm when P(none) < this

    proba = clf.predict_proba(X[te])
    p_none = proba[:, none_col]
    alarm = p_none < threshold
    others = proba.copy()
    others[:, none_col] = -1
    kind_pred = clf.classes_[others.argmax(1)]
    rows = []
    for k in KINDS:
        sel = y[te] == k
        n = int(sel.sum())
        if not n:
            continue
        l1 = np.array([meta[i]["l1"] for i in te])[sel]
        l3 = alarm[sel]
        if k == "none":
            rows.append({"fault": k, "n": n, "l1_alarms": int(l1.sum()), "l3_alarms": int(l3.sum()),
                         "either": int((l1 | l3).sum())})
        else:
            rows.append({"fault": k, "n": n, "l1_caught": int(l1.sum()), "l3_caught": int(l3.sum()),
                         "either": int((l1 | l3).sum()), "l3_kind_right": int((l3 & (kind_pred[sel] == k)).sum())})
    top = sorted(zip(FEATURE_NAMES, _importance(clf, X[te], y[te], seed)), key=lambda t: -t[1])[:8]
    report = {"data": str(data_dir), "runs": int(len(y)), "train": int(len(tr)), "calibrate": int(len(cal)),
              "test": int(len(te)), "threshold_p_none": round(threshold, 4), "per_fault": rows,
              "top_features": [[n, round(v, 4)] for n, v in top]}
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"clf": clf, "threshold": threshold, "features": FEATURE_NAMES}, out)
    (out.with_suffix(".report.json")).write_text(json.dumps(report, indent=1))
    return report


def _importance(clf, X, y, seed):
    try:
        from sklearn.inspection import permutation_importance
        r = permutation_importance(clf, X, y, n_repeats=3, random_state=seed, n_jobs=1)
        return list(r.importances_mean)
    except Exception:
        return [0.0] * X.shape[1]


def audit(run_path, model=MODEL):
    """One finished run -> {"verdict": "healthy"|"suspect", "kind", "p_healthy", "threshold"}."""
    import joblib
    m = joblib.load(model)
    J, D, _ = load_rows(run_path)
    proba = m["clf"].predict_proba(np.array([features(J, D)]))[0]
    classes = list(m["clf"].classes_)
    p_none = float(proba[classes.index("none")])
    ranked = sorted(((c, float(p)) for c, p in zip(classes, proba) if c != "none"), key=lambda t: -t[1])
    suspect = p_none < m["threshold"]
    return {"verdict": "suspect" if suspect else "healthy", "kind": ranked[0][0] if suspect else None,
            "p_healthy": round(p_none, 3), "threshold": round(m["threshold"], 3),
            "top": [[c, round(p, 3)] for c, p in ranked[:3]]}


def print_report(rep):
    print(f"level 3: {rep['runs']} runs ({rep['train']} train / {rep['calibrate']} calibrate / {rep['test']} test), "
          f"alarm when P(healthy) < {rep['threshold_p_none']}")
    print(f"{'planted fault':16s} {'n':>4s} {'level 1':>8s} {'level 3':>8s} {'L1 or L3':>9s} {'L3 kind ok':>10s}")
    for r in rep["per_fault"]:
        if r["fault"] == "none":
            print(f"{'none (alarms)':16s} {r['n']:4d} {r['l1_alarms']:8d} {r['l3_alarms']:8d} {r['either']:9d}")
        else:
            print(f"{r['fault']:16s} {r['n']:4d} {r['l1_caught']:8d} {r['l3_caught']:8d} {r['either']:9d} {r['l3_kind_right']:10d}")
    print("top features:", ", ".join(f"{n} {v}" for n, v in rep["top_features"]))


def main():
    ap = argparse.ArgumentParser(description="Healer level 3: train or run the run-audit classifier.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--data", default=str(ROOT.parents[1] / "data" / "sim-faults" / "v2"))
    t.add_argument("--out", default=str(MODEL))
    a = sub.add_parser("audit")
    a.add_argument("run")
    a.add_argument("--model", default=str(MODEL))
    args = ap.parse_args()
    if args.cmd == "train":
        print_report(train(args.data, args.out))
    else:
        print(json.dumps(audit(args.run, args.model)))


if __name__ == "__main__":
    main()
