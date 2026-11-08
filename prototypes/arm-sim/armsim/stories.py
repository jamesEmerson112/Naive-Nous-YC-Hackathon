"""The demo stories, one command each, against a running arm host (demo/up.sh starts everything).
Tonight they run in sim; on the bench, `--real` waits for the fault to be planted by hand (Olir's bad
pod image, or a servo unplugged) instead of injecting it.

    python -m armsim.stories a            # the system collapses: bad pod image 02 -> pod heals -> arm resumes
    python -m armsim.stories b            # job -> work -> break -> heal: stuck sensor -> restart -> retry
    python -m armsim.stories b --hermes   # the job comes from a Naïve board card and Hermes does it
    python -m armsim.stories stall        # a dead joint -> handed to a person -> Romulus work order
"""
import argparse
import json
import os
import subprocess
import time
import urllib.request

from .client import call

BOARD = "brd_v2zcnyzc559hv8awzc4hqvgcsm"
T0 = time.time()
COMMANDER = (
    "You are the arm commander for our robot-arm company. Our jobs live on the Naïve board {board}. List its "
    "cards, find the open card whose title starts with 'ARM JOB', read it and its comments, and do the job with "
    "the so101_arm tools. Rules: only the arm_* skills move the arm. Use the healer tools only after a skill "
    "reports an incident or a tool call is blocked: then call arm_status, fix the cause (arm_restart_sensor for "
    "a stuck or frozen distance; arm_check for anything else) and retry that step once. When the job is done, "
    "add a comment starting 'RESULT:' that says what you did and any fault you healed, then move the card to "
    "done. If you can't finish, comment 'BLOCKED:' with the reason and leave the card open.")


def say(text):
    print(f"[{time.time() - T0:5.1f}s] {text}", flush=True)


def skill(name):
    r = call("skill", name=name)
    if not r.get("ok"):
        say(f"  {name}: refused ({r.get('error')})")
        return r
    res = r["result"]
    bits = [f"arrived={res.get('arrived')}"]
    for k in ("switched_at_mm", "halted", "aborted"):
        if res.get(k) is not None:
            bits.append(f"{k}={res[k]}")
    if res.get("grasp"):
        bits.append(f"holding={res['grasp']['holding']}")
    say(f"  {name}: " + " ".join(bits) + (f"\n        INCIDENT: {r['incident']['summary']}" if r.get("incident") else ""))
    return r


def wait(pred, timeout, what, every=1.0):
    t = time.time()
    while time.time() - t < timeout:
        st = call("status")
        if pred(st):
            say(f"  {what} ({time.time() - t:.0f} s)")
            return st
        time.sleep(every)
    raise SystemExit(f"timed out after {timeout} s waiting for: {what}")


def romulus(url, device_ids):
    try:
        with urllib.request.urlopen(f"{url}/v1/incidents", timeout=5) as resp:
            items = json.load(resp)
        with urllib.request.urlopen(f"{url}/v1/work-orders", timeout=5) as resp:
            orders = json.load(resp)
    except OSError as exc:
        say(f"Romulus not reachable at {url}: {exc}")
        return
    items = items if isinstance(items, list) else items.get("incidents", [])
    orders = orders if isinstance(orders, list) else orders.get("work_orders", [])
    say(f"Romulus ({url}):")
    for i in [i for i in items if i.get("device_id") in device_ids][:4]:
        say(f"  {i['device_id']:11s} {i['kind']:12s} resolved={i.get('resolved')} action={i.get('resolving_action')} "
            f"cause={i.get('suspected_cause')}")
    for w in [w for w in orders if w.get("device_id") in device_ids][:2]:
        say(f"  work order {w['id']} for {w['device_id']}: {w.get('status')}")


def story_a(real):
    say("STORY A: the system collapses (a bad pod firmware), the pod heals itself, the arm resumes")
    skill("go_home")
    if real:
        say("Olir: start the bad-image-02 scenario on the pod now (firmware/demo/scenario start 02)")
    else:
        say("a bad pod image (v1.2, wrong clock) goes out: distances and the pod's clock run at half speed")
        call("inject", fault="pod_image02")
        time.sleep(1.0)
    skill("approach")
    wait(lambda s: any(i["kind"] == "clock_skew" for i in s.get("pod", {}).get("incidents", [])), 60,
         "podd's own triage flags the pod: clock_skew")
    wait(lambda s: not s["incidents"], 120, "the pod healer rolled back, and the arm's check at home passed")
    say("the arm resumes the job")
    for name in ("approach", "grip", "lift", "release", "go_home"):
        skill(name)


def story_b(real, hermes):
    say("STORY B: job -> work -> break -> heal: a stuck distance sensor in the middle of a job")
    skill("go_home")
    if hermes:
        key = f"armsim-job-{int(time.time())}"
        out = subprocess.run(["naive", "board", "add", BOARD, "--key", key, "--title", "ARM JOB: pick up the block",
                              "--body", "Pick up the 3 cm block: approach, grip, lift 30 mm, then release it and go "
                              "home. Heal any fault and retry once. Comment RESULT: and move this card to done.",
                              "--json"], capture_output=True, text=True)
        card = json.loads(out.stdout or "{}").get("data", {})
        say(f"a job card lands on the Naïve board: {card.get('id')} '{card.get('title')}'")
    if real:
        say("plant the fault by hand now (Olir: the stuck-sensor scenario on the pod)")
    else:
        say("the wrist sensor's reading freezes (the pod keeps sending its last value)")
        call("inject", fault="us_frozen")
    if hermes:
        say("Hermes takes the card and runs the job")
        subprocess.run(["hermes", "chat", "--oneshot", "-t", "so101_arm,mcp-naive_board", "-q",
                        COMMANDER.format(board=BOARD)], check=False)
        return
    skill("reach")
    r = skill("approach")
    if r.get("incident") or call("status")["incidents"]:
        say("the healer restarts the sensor through podd ('us off', 'us on'); the watchdog checks it at home")
        fix = call("repair", action="restart_sensor")
        say(f"  verified={fix.get('verified')} still={fix.get('still')}")
    for name in ("approach", "grip", "lift", "release", "go_home"):
        skill(name)


def story_stall(real):
    say("HARDWARE: a joint that won't move is handed to a person; Romulus opens a work order")
    skill("go_home")
    if real:
        say("unplug the elbow servo's cable now (or hold the elbow)")
    else:
        call("inject", fault="joint_stuck", params={"joint": 2})
    skill("reach")
    r = call("repair", action="verify")
    say(f"  checked retry: verified={r.get('verified')}; handed off: "
        f"{[i['summary'] for i in call('status')['handed_off']]}")
    if not real:
        call("clear", fault="joint_stuck")                   # the person replaces the servo
        r = call("repair", action="verify")
        say(f"  after the repair by hand: verified={r.get('verified')}")


def main():
    ap = argparse.ArgumentParser(description="Run a demo story against the arm host.")
    ap.add_argument("story", choices=["a", "b", "stall"])
    ap.add_argument("--hermes", action="store_true", help="story b: the job comes from a Naïve card, Hermes runs it")
    ap.add_argument("--real", action="store_true", help="the real bench: plant faults by hand, don't inject")
    ap.add_argument("--romulus", default=os.environ.get("ARMSIM_ROMULUS", "http://localhost:8000"))
    a = ap.parse_args()
    st = call("status")
    if st.get("incidents"):
        raise SystemExit(f"the arm has open incidents; clear them first: {st['incidents']}")
    {"a": lambda: story_a(a.real), "b": lambda: story_b(a.real, a.hermes), "stall": lambda: story_stall(a.real)}[a.story]()
    time.sleep(12)                                           # the next check-in and the outbox drain
    romulus(a.romulus, {st.get("romulus", {}).get("device", "arm-twin-1"), "pod-1"})


if __name__ == "__main__":
    main()
