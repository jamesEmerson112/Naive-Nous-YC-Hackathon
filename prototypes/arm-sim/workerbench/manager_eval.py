"""Manager eval (stub): what it will do once the `manager` Hermes profile and armsim/escalation.py exist.

Design: docs/worker-manager-healer.md §4, "Manager eval (new, small)" (inferred there):
1. Take 30 track-E items from the test split (5 per route: sensor_restart, pod_fw, arm_hardware,
   calibration/wiring, none, plus wrong_action once step 9 exists), and wrap each one as an
   `arm-escalation/v1` packet with armsim/escalation.py (`build_escalation`, step 2): the rendered lite
   window in `window`, the level-1 findings in `findings`, the worker's six-key answer (or null) in
   `worker`, and `allowed_actions(inc, state)` from plain code.
2. POST each packet to the manager, `POST {manager}/v1/runs` with `Idempotency-Key` = the incident id and
   one `session_id` per incident (docs/worker-manager-healer.md §2.3; H:L39524-39560), then poll
   `GET /v1/runs/{id}` every 2 s for at most 90 s.
3. Score each answer:
   - `check_plan(plan, esc, state)` passes (armsim/escalation.py, step 1);
   - `plan.action` matches the gold route's action, `kinds.ROUTE_ACTION`: sensor_restart -> restart_sensor,
     pod_fw -> pod_rollback, arm_hardware / calibration / wiring -> handoff_human, task_redo -> redo_step,
     none -> no_action;
   - latency p50 and p90 (the design expects 20-60 s; measure it).
4. Cost: the manager runs on the ChatGPT plan (gpt-6-sol), so no API key is needed here.

Not built tonight: it needs escalation.py (steps 1-2) and a reachable manager gateway (step 6). Until
then `main()` only prints the plan and the gold actions of the items it would send.

    python3 -m workerbench.manager_eval --dry-run          # which 30 items, and the action each should get
"""
import argparse
import collections
import sys

from . import kinds, run


def pick(items, per_route=5, n=30):
    """30 observable track-E items, spread over the manager routes (deterministic)."""
    by = collections.defaultdict(list)
    for it in sorted(items, key=lambda i: run._h(i["id"])):
        lab = it["label"]
        if lab.get("observable", True):
            by[lab["route"]].append(it)
    out = []
    for route in sorted(by):
        out += by[route][:per_route]
    return out[:n]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Manager eval (stub).")
    ap.add_argument("--split", default="test")
    ap.add_argument("--manager", help="the manager's base URL, e.g. http://<mac>:8642/p/manager (not used yet)")
    ap.add_argument("--dry-run", action="store_true", default=True)
    a = ap.parse_args(argv)
    items = run.load_items([str(run.DATA / f"*-{a.split}.jsonl")], a.split)
    chosen = pick(items)
    print(f"would send {len(chosen)} escalation packets; gold action per route:")
    for route, n in sorted(collections.Counter(i["label"]["route"] for i in chosen).items()):
        print(f"  {route:15s} {n:2d} -> {kinds.ROUTE_ACTION[route]}")
    print("not built yet: needs armsim/escalation.py (design steps 1-2) and the manager gateway (step 6)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
