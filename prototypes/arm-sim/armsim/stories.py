"""The demo stories, one command each, against a running arm host (demo/up.sh starts everything).
Tonight they run in sim; on the bench, `--real` waits for the fault to be planted by hand (Olir's bad
pod image, or a servo unplugged) instead of injecting it.

    python -m armsim.stories a            # the system collapses: bad pod image 02 -> pod heals -> arm resumes
    python -m armsim.stories b            # job -> work -> break -> heal: stuck sensor -> restart -> retry
    python -m armsim.stories b --hermes   # the job comes from a Naïve board card and Hermes does it
    python -m armsim.stories stall        # a dead joint -> handed to a person -> Romulus work order
    python -m armsim.stories redo --manager fake   # a wrong action (sim only): the block was bumped, the grasp
                                          # misses -> reversed -> the manager corrects the approach -> redone
      --shift-mm 30 (how far the block moves away from the base; 60 is past the scan: 2 redos, then a person),
      --fake-mode good|redo_offset, --manager off (today: the card is blocked). Runs the dispatcher once on
      the file board ($ARMSIM_FILE_BOARD), then prints the run's event lines and puts the block back.
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


IMAGES = {   # bad pod images (firmware/demo/bad-images) and the podd incident each one opens
    "07": ("v1.7, distance math truncated to 16 bits: the distance disagrees with its own echo time",
           "us_mismatch"),
    "02": ("v1.2, wrong clock: distances and the pod's clock run at half speed", "clock_skew"),
}


def story_a(real, image="07"):
    what, kind = IMAGES[image]
    say("STORY A: the system collapses (a bad pod firmware), the pod heals itself, the arm resumes")
    skill("go_home")
    if real:
        say(f"Olir: start the bad-image-{image} scenario on the pod now (firmware/demo/scenario start {image})")
    else:
        say(f"a bad pod image ({what}) goes out")
        call("inject", fault=f"pod_image{image}")
        time.sleep(1.0)
    skill("approach")
    wait(lambda s: any(i["kind"] == kind for i in s.get("pod", {}).get("incidents", [])), 60,
         f"podd's own triage flags the pod: {kind}")
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


def story_redo(real, manager, shift_mm, fake_mode):
    """Category 2 (armsim/redo.py), through the dispatcher: an order, a bumped block, a missed grasp.
    --manager hermes checks the manager's tools before anything moves (the dispatcher's own preflight would
    refuse later, after the block moved and the order was placed), and whatever the dispatcher does, the block
    goes back and a card it never ran is blocked, so the next story or dispatcher doesn't inherit either.
    When the redo handed the arm to a person, the story plays the person: block back, then the check at home."""
    from . import events
    from .board import BoardError, open_board
    from .dispatch import main as dispatch_main
    from .orders import is_arm_job, place
    if real:
        raise SystemExit("story redo is sim only tonight: it moves the simulated block, and the relocate scan needs "
                         "the arm's kinematics, which are off on the real arm")
    board = open_board("file")
    waiting = [c["id"] for c in board.show("todo") if is_arm_job(c)]
    if waiting:
        raise SystemExit(f"the file board {getattr(board, 'path', '')} has ARM JOB cards waiting ({', '.join(waiting)}): "
                         "the dispatcher would work those first. Use a fresh ARMSIM_FILE_BOARD.")
    if manager == "hermes":                                   # before anything moves (armsim/worker.py preflight)
        from .manager import ManagerClient
        from .worker import preflight
        preflight(ManagerClient(os.environ.get("ARMSIM_MANAGER_URL", "http://127.0.0.1:8642"),
                                key=os.environ.get("ARMSIM_MANAGER_KEY"), label="hermes"))
    say("STORY REDO: the arm does the wrong thing -> it is told -> it reverses -> the manager corrects the command "
        "-> the arm redoes it")
    skill("go_home")
    say(f"someone bumps the block {shift_mm:g} mm away from the base (sim only)")
    r = call("inject", fault="block_shift", params={"dx_mm": shift_mm, "dy_mm": 0})
    if not r.get("ok"):
        raise SystemExit(f"couldn't move the block: {r.get('error')}")
    card = None
    try:
        order, card = place(board, "pick", source="story")
        say(f"order {order['id']} is card {card['id']}; the dispatcher takes it"
            + (f" (manager {manager}, wrong actions redone)" if manager != "off" else " (manager off: today's behaviour)"))
        since = time.time() - 0.5
        argv = ["--board", "file", "--exec", "scripted", "--once"]
        if manager != "off":
            argv += ["--manager", manager, "--wrong-action", "redo", "--fake-mode", fake_mode]
        dispatch_main(argv)
        say("the run's event lines:")
        for e in events.read(since=since):
            if e.get("card") in (None, card["id"]) and e.get("src") != "order":
                print(f"{time.strftime('%H:%M:%S', time.localtime(e['ts']))} {e['src']:9s} {e['kind']:12s} {e['text']}",
                      flush=True)
        final = board.card(card["id"]) or {}
        say(f"card {card['id']} is {final.get('status')}: {final.get('note') or ''}")
    finally:
        call("clear", fault="block_shift")
        say("a person puts the block back where the poses expect it")
        if card is not None:
            try:                                              # never left for a dispatcher with the block shifted
                if (board.card(card["id"]) or {}).get("status") == "todo":
                    board.comment(card["id"], "BLOCKED: story redo: the dispatcher didn't run this card, so nothing "
                                              "ran on the arm; the block was put back. Move it to todo to run it.")
                    board.move(card["id"], status="blocked", expect="todo")
                    say(f"card {card['id']} was never run: blocked, so no dispatcher takes it later")
            except BoardError as exc:
                say(f"couldn't block card {card['id']} ({exc}): move it out of todo by hand")
        held = call("status").get("handed_off") or []
        if held:                                              # the redo handed the arm to a person: be the person
            r = call("repair", action="verify")
            say(f"the person checks the arm at home (it held {', '.join(i['id'] for i in held)} for them): "
                f"verified={r.get('verified')}")


def main():
    ap = argparse.ArgumentParser(description="Run a demo story against the arm host.")
    ap.add_argument("story", choices=["a", "b", "stall", "redo"])
    ap.add_argument("--hermes", action="store_true", help="story b: the job comes from a Naïve card, Hermes runs it")
    ap.add_argument("--real", action="store_true", help="the real bench: plant faults by hand, don't inject")
    ap.add_argument("--image", choices=sorted(IMAGES), default="07",
                    help="story a: which bad pod image (07 is the bench demo's, #54; 02 the clock bug)")
    ap.add_argument("--romulus", default=os.environ.get("ARMSIM_ROMULUS", "http://localhost:8000"))
    ap.add_argument("--manager", choices=["off", "fake", "hermes"], default="fake",
                    help="story redo: who corrects the command (off: today's behaviour, the card is blocked)")
    ap.add_argument("--fake-mode", choices=["good", "redo_offset"], default="good",
                    help="story redo with --manager fake: relocate (good) or a bounded offset (redo_offset)")
    ap.add_argument("--shift-mm", type=float, default=30.0, help="story redo: how far the block moves (mm, +x)")
    a = ap.parse_args()
    st = call("status")
    if st.get("incidents"):
        raise SystemExit(f"the arm has open incidents; clear them first: {st['incidents']}")
    {"a": lambda: story_a(a.real, a.image), "b": lambda: story_b(a.real, a.hermes),
     "stall": lambda: story_stall(a.real),
     "redo": lambda: story_redo(a.real, a.manager, a.shift_mm, a.fake_mode)}[a.story]()
    if a.story == "redo":                                    # a dispatcher incident: nothing goes to Romulus
        return
    time.sleep(12)                                           # the next check-in and the outbox drain
    romulus(a.romulus, {st.get("romulus", {}).get("device", "arm-twin-1"), "pod-1"})


if __name__ == "__main__":
    main()
