"""Real (and live) data for the worker benchmark, test split only: windows of recorded pod data.

- `windows_from_sample(path)`: 5 non-overlapping 60-row windows of Olir's real 300-reading ultrasonic
  sample (firmware/ultrasonic-sample.json), labeled healthy: the "zero alarms on real data" gate.
- `windows_from_podd_logs(var_dir)`: podd snapshots rebuilt from a podd var directory's logs
  (logs/telemetry.jsonl A and D rows, health.jsonl H, events.jsonl E, incidents.jsonl; docs/firmware-apis.md
  §5), one window every `step_s`. Each window is labeled from podd's own incidents open at that moment
  (`label_source: podd`), or healthy when none is open: on real logs a person should confirm those labels.

    python3 -m workerbench.gen_real                                  # the real sample -> real-sample-test.jsonl
    python3 -m workerbench.gen_real --podd-var samples/pod/<night> --source real-podd
    python3 -m workerbench.gen_real --podd-var /tmp/.../pod --source vpod-live --step 3   # a live sim run

The vpod-live slice (split `live`) is an integration check only: it proves the renderer and level 1 read
real podd output. Its labels come from the incidents open at each moment, including false ones (podd's
us_intermittent during a wave), so it is never part of the test split.

Olir's overnight logs go in samples/pod/ once they land (docs/firmware-apis.md:382-392); none are in the
repo yet, so the real-podd slice is empty tonight.
"""
import argparse
import json
import sys
from pathlib import Path

from . import kinds, l1

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
OUT = REPO / "data" / "workerbench"
SAMPLE = REPO / "firmware" / "ultrasonic-sample.json"
# podd incident kinds -> benchmark kinds (via the level-1 alias table)


def _healthy_label(group):
    return {"status": "healthy", "kind": "none", "accept": ["none"], "route": "none", "severity": "info",
            "escalate": False, "observable": True, "hard_negative": group, "evidence_rows": {}, "facts": {},
            "onset_s": None, "since_onset_s": None, "params": {}, "label_source": "real-healthy"}


def windows_from_sample(path=SAMPLE, *, rows_per=60, split="test"):
    rows = json.loads(Path(path).read_text())["rows"]
    items = []
    for k in range(0, len(rows) - rows_per + 1, rows_per):
        D = [dict(r) for r in rows[k:k + rows_per]]
        now = D[-1]["ts"]
        D = [r for r in D if r["ts"] > now - 6.0]
        snap = {"now": now, "D": D, "A": [], "H": [], "E": [], "link": {"state": "up", "valid_ratio": 1.0},
                "skills": [], "incidents": [], "J": [], "holding": False}
        items.append({"id": f"{split}-real-sample-{k:03d}", "split": split, "source": "real-sample", "kinematics": False,
                      "snapshot": snap, "label": _healthy_label("real"),
                      "meta": {"seed": None, "variant": "real-sample", "run_id": Path(path).name, "rows": [k, k + rows_per],
                               "l1": [f["kind"] for f in l1.alarms(l1.l1_findings(snap))]}})
    return items


def _jsonl(path):
    out = []
    if not path.exists():
        return out
    for line in path.open(errors="replace"):
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def windows_from_podd_logs(var_dir, *, step_s=30.0, source="real-podd", split="test", warmup_s=30.0):
    """Rebuild podd snapshots (podd.py:263-272 windows: D, A 6 s; H 30 s; E 180 s) every `step_s`."""
    logs = Path(var_dir) / "logs"
    tel = _jsonl(logs / "telemetry.jsonl")
    D = sorted((r for r in tel if r.get("type") == "D"), key=lambda r: r["ts"])
    A = sorted((r for r in tel if r.get("type") == "A"), key=lambda r: r["ts"])
    H = sorted((r for r in _jsonl(logs / "health.jsonl") if r.get("type") == "H"), key=lambda r: r["ts"])
    ev = _jsonl(logs / "events.jsonl")
    E = sorted((r for r in ev if r.get("type") == "E"), key=lambda r: r["ts"])
    links = sorted((r for r in ev if r.get("type") == "host" and str(r.get("ev", "")).startswith("link")),
                   key=lambda r: r["ts"])
    incs = _jsonl(logs / "incidents.jsonl")
    # the arm host's own watchdog incidents, noted in podd's event log (armsim/host.py Watchdog._open)
    for r in ev:
        if r.get("type") == "host" and r.get("ev") in ("arm_incident", "arm_resolved", "arm_handoff"):
            incs.append(dict(r, id="arm-" + str(r.get("id")), severity=r.get("severity", "critical"), src="watchdog"))
    if not D and not H:
        return []
    t0 = min(r["ts"] for r in (D[:1] + H[:1])) + warmup_s
    t1 = max(r["ts"] for r in (D[-1:] + H[-1:]))
    items, now = [], t0
    while now <= t1:
        snap = {"now": now, "D": [r for r in D if now - 6 < r["ts"] <= now],
                "A": [r for r in A if now - 6 < r["ts"] <= now], "H": [r for r in H if now - 30 < r["ts"] <= now],
                "E": [r for r in E if now - 180 < r["ts"] <= now], "link": {"state": "up", "valid_ratio": 1.0},
                "skills": [], "incidents": [], "J": [], "holding": False}
        last_link = [r for r in links if r["ts"] <= now]
        if last_link and last_link[-1].get("state"):
            snap["link"] = {"state": last_link[-1]["state"], "valid_ratio": last_link[-1].get("valid_ratio", 1.0)}
        open_now = _open_incidents(incs, now)
        snap["incidents"] = [{"kind": i.get("kind"), "severity": i.get("severity"), "summary": i.get("summary"),
                              "sim_t": None, "src": i.get("src", "podd")} for i in open_now]
        alarm = [i for i in open_now if i.get("severity") in ("warn", "critical")]
        if alarm:
            name = alarm[0]["kind"]
            kind = kinds.l1_primary(name)
            label = {"status": "fault", "kind": kind, "accept": sorted(kinds.l1_kinds(name)), "route": kinds.ROUTE[kind],
                     "severity": kinds.GOLD_SEVERITY[kind], "escalate": kinds.GOLD_SEVERITY[kind] != "info",
                     "observable": True, "hard_negative": None, "evidence_rows": {}, "facts": {}, "onset_s": None,
                     "since_onset_s": None, "params": {}, "label_source": "podd"}
        else:
            label = dict(_healthy_label(None if source == "vpod-live" else "real"), label_source="podd")
        items.append({"id": f"{split}-{source}-{Path(var_dir).name}-{int(now)}", "split": split, "source": source,
                      "kinematics": False, "snapshot": snap, "label": label,
                      "meta": {"seed": None, "variant": source, "run_id": str(var_dir),
                               "l1": [f["kind"] for f in l1.alarms(l1.l1_findings(snap))]}})
        now += step_s
    return items


def _open_incidents(records, now):
    """Incidents open at `now` from incidents.jsonl open/resolve records (keyed by id)."""
    state = {}
    for r in sorted(records, key=lambda r: r.get("ts", 0)):
        if r.get("ts", 0) > now:
            break
        iid = r.get("id") or r.get("kind")
        if r.get("status") in ("resolved", "handed_off") or r.get("ev") in ("arm_resolved", "arm_handoff"):
            state.pop(iid, None)
        else:
            state[iid] = r
    return list(state.values())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sample", default=str(SAMPLE))
    ap.add_argument("--podd-var", default=None, help="a podd var dir (with logs/) to cut into windows")
    ap.add_argument("--source", default="real-podd", help="real-podd or vpod-live")
    ap.add_argument("--step", type=float, default=30.0)
    ap.add_argument("--split", default=None, help="default: test for real-podd, live for vpod-live (labels unchecked)")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.podd_var:
        split = a.split or ("live" if a.source == "vpod-live" else "test")
        items = windows_from_podd_logs(a.podd_var, step_s=a.step, source=a.source, split=split)
        path = out / f"{a.source}-{split}.jsonl"
    else:
        items = windows_from_sample(a.sample)
        path = out / "real-sample-test.jsonl"
    with path.open("w") as f:
        for it in items:
            f.write(json.dumps(it, separators=(",", ":")) + "\n")
    fired = sum(1 for i in items if i["meta"]["l1"])
    faults = sum(1 for i in items if i["label"]["status"] == "fault")
    print(f"wrote {len(items)} items -> {path} ({faults} labeled fault by podd incidents, level 1 alarmed on {fired})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
