"""The soak (armsim/soak.py) against a file board in a temp dir and a fake arm host: no host, no Naïve, no
Hermes. Most orders are worked by the real dispatcher (armsim/dispatch.py, scripted commander) running in a
thread against the same fake host. Run from prototypes/arm-sim:

    PYTHONPATH=. .venv/bin/python tests/test_soak.py      (or: python -m pytest tests/)
"""
import functools
import json
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from armsim import dispatch, events, orders, soak              # noqa: E402
from armsim.board import FileBoard, comment_text               # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="armsim-soak-test-"))
events.PATH = TMP / "default-events.jsonl"                     # orders.place() logs here, not in runs/


def fresh(name):
    d = TMP / f"{name}-{time.time_ns()}"
    d.mkdir(parents=True)
    log = d / "events.jsonl"
    events.PATH = log                                           # orders.place() and the soak share it
    return d, FileBoard(d / "board.json", board_id="brd_test"), log, functools.partial(events.emit, path=log, echo=False)


class FakeHost:
    """A stand-in arm host, safe to call from the soak and a dispatcher thread at once. Skills take skill_s;
    a planted fault shows up at the next skill the way the watchdog reports it:
      us_frozen     the skill halts with a us_frozen incident; restart_sensor fixes it
      pod_image07   podd flags us_mismatch (critical) and the arm parks with pod_us_mismatch; the pod's
      pod_image02   healer rolls back after pod_polls status calls, and the host's own check closes it
      joint_stuck   the skill doesn't arrive (joint_stall); a check fails, handing it to a person, until cleared
    notice=False: planted faults trip nothing (the soak must still clear them). fixable=False: no check ever
    passes. resettable=True: it has the reset_scene op (a newer host); otherwise "unknown op". block_gone=True:
    the block isn't where the pick expects it (the jaws close on nothing)."""

    def __init__(self, *, skill_s=0.04, pod_polls=5, notice=True, fixable=True, resettable=False, block_gone=False):
        self.lock = threading.RLock()
        self.resettable, self.block_gone = resettable, block_gone
        self.skill_s, self.pod_polls, self.notice, self.fixable = skill_s, pod_polls, notice, fixable
        self.faults, self.incidents, self.pod_crit, self.image = set(), [], [], "good"
        self.calls, self.since_flash = [], 0

    def open(self):
        return [i for i in self.incidents if i["state"] == "open"]

    def _open(self, kind, summary, **extra):
        inc = {"id": f"inc-{len(self.incidents) + 1}", "state": "open", "kind": kind, "summary": summary, **extra}
        self.incidents.append(inc)
        return inc

    def _short(self, i):
        return {k: i.get(k) for k in ("id", "kind", "summary")}

    def _pod_tick(self):
        if self.image == "good":
            return
        self.since_flash += 1
        if self.since_flash >= self.pod_polls:            # the pod healer rolled back; the host's check passed
            self.image, self.pod_crit = "good", []
            for i in self.open():
                if i["kind"].startswith("pod_"):
                    i["state"] = "resolved"

    def _verify(self):
        for i in self.open():
            fixed = self.fixable and {"us_frozen": "us_frozen" not in self.faults,
                                      "joint_stall": "joint_stuck" not in self.faults}.get(i["kind"], self.image == "good")
            if fixed:
                i["state"] = "resolved"
            elif i["kind"] == "joint_stall":
                i.update(handed_off=1.0, handoff_reason="still failing after a checked retry: home move arrived=False")
        return {"ok": True, "verified": not self.open(), "still": [i["summary"] for i in self.open()],
                "open": [i["id"] for i in self.open()]}

    def __call__(self, op, **f):
        with self.lock:
            self.calls.append((op, f.get("fault") or f.get("name") or f.get("action")))
            if op == "status":
                self._pod_tick()
                return {"ok": True, "incidents": [self._short(i) for i in self.open()],
                        "handed_off": [{**self._short(i), "handoff_reason": i["handoff_reason"]}
                                       for i in self.open() if i.get("handed_off")],
                        "pod": {"podd_up": True, "image": self.image, "incidents": list(self.pod_crit)}}
            if op == "incidents":
                return {"ok": True, "incidents": [dict(i) for i in self.incidents]}
            if op == "inject":
                fault = f["fault"]
                self.faults.add(fault)
                if fault.startswith("pod_image"):
                    self.image, self.since_flash = fault[-2:], 0
                    if self.notice:
                        self.pod_crit = [{"kind": "us_mismatch", "severity": "critical",
                                          "summary": "distance disagrees with its echo"}]
                if fault == "joint_stuck":
                    assert f.get("params") == {"joint": 2}, f
                return {"ok": True, "fault": fault, "where": "pod" if fault.startswith(("pod_", "us_")) else "arm"}
            if op == "reset_scene" and self.resettable:
                return {"ok": True}
            if op == "clear":
                self.faults.discard(f.get("fault"))
                return {"ok": True}
            if op == "repair":
                if f["action"] == "restart_sensor":
                    self.faults.discard("us_frozen")
                return {"ok": True, "action": f["action"], **self._verify()}
            if op != "skill":
                return {"ok": False, "error": f"unknown op {op!r}"}
            if self.open() and f["name"] != "park":
                return {"ok": False, "error": "incident open; the arm stays parked until a repair is verified",
                        "incidents": [self._short(i) for i in self.open()]}
        time.sleep(self.skill_s)                          # the arm moves (outside the lock)
        with self.lock:
            name, inc = f["name"], None
            if self.notice and "us_frozen" in self.faults:
                inc = self._open("us_frozen", "Distance frozen at 130 mm: no echo jitter at all")
            elif self.notice and self.image != "good" and self.pod_crit:
                inc = self._open("pod_us_mismatch", "Sensor pod: distance disagrees with its echo",
                                 pod_incidents=["us_mismatch-1"])
            elif self.notice and "joint_stuck" in self.faults:
                inc = self._open("joint_stall", "Elbow flex stopped 40 deg short of its target")
            result = {"arrived": not inc}
            if inc and inc["kind"] != "joint_stall":
                result["halted"] = inc["kind"]
            if name in ("grip", "lift") and not inc:
                result["grasp"] = {"holding": not self.block_gone}
            return {"ok": True, "skill": name, "result": result, **({"incident": self._short(inc)} if inc else {})}


class Bench:
    """The real dispatcher (scripted commander) in a thread, on the fake host and a temp file board."""

    def __init__(self, name, host=None, *, dispatcher=True):
        self.dir, self.board, self.log, self.emit = fresh(name)
        self.host = host or FakeHost()
        self.stop, self.thread = threading.Event(), None
        if dispatcher:
            ex = dispatch.ScriptedExecutor(self.board, call=self.host, emit=self.emit, poll_s=0.01, pod_clear_s=0.05,
                                           hold_s=0.05)
            d = dispatch.Dispatcher(self.board, ex, call=self.host, emit=self.emit, every=0.02, stop=self.stop)
            self.thread = threading.Thread(target=d.run, daemon=True)
            self.thread.start()

    def soak(self, **kw):
        kw = {"call": self.host, "emit": self.emit, "out": self.dir / "soak.jsonl", "every": 0, "claim_timeout": 5,
              "claim_poll_s": 0.01, "poll_s": 0.01, "pod_heal_s": 2, "job_timeout": 30, "events_path": self.log,
              "wait": lambda s: time.sleep(min(s, 0.01)), **kw}
        return soak.Soak(self.board, **kw)

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(5)

    def lines(self):
        return [json.loads(x) for x in (self.dir / "soak.jsonl").read_text().splitlines()]


def plan_with(fault, job=None, *, faults=("us_frozen", "pod_image07", "pod_image02", "joint_stuck")):
    """The first order index whose plan (seed 1, rate 0.5) plants this fault (and has this job)."""
    for i in range(500):
        p = soak.plan(i, seed=1, rate=0.5, faults=faults)
        if p["fault"] == fault and (job is None or p["job"] == job):
            return i
    raise AssertionError(f"no plan plants {fault}")


# ---- the plan --------------------------------------------------------------------------------------
def test_the_plan_is_deterministic_and_spreads_the_faults_evenly():
    faults = ("us_frozen", "pod_image07", "pod_image02")
    a = [soak.plan(i, seed=7, rate=0.5, faults=faults) for i in range(1000)]
    assert a == [soak.plan(i, seed=7, rate=0.5, faults=faults) for i in range(1000)]          # same seed, same plan
    assert a != [soak.plan(i, seed=8, rate=0.5, faults=faults) for i in range(1000)]
    for n in (2, 4, 10, 48, 1000):                    # any run of orders is within one of the rate
        assert abs(sum(p["fault"] is not None for p in a[:n]) - n * 0.5) <= 1, n
    for lo in range(0, 990, 37):
        assert abs(sum(p["fault"] is not None for p in a[lo:lo + 10]) - 5) <= 1, lo
    planted = [p["fault"] for p in a if p["fault"]]
    for k in range(0, len(planted) - 2, 3):           # the faults take turns: each once per round
        assert sorted(planted[k:k + 3]) == sorted(faults), planted[k:k + 3]
    waves = sum(p["job"] == "wave" for p in a)
    assert 120 < waves < 280, waves                   # mostly picks, sometimes a wave
    assert all(p["delay_s"] == 0.0 for p in a if (p["fault"] or "").startswith("pod_"))   # pod faults: at the claim
    assert all(0.2 <= p["delay_s"] <= 2.5 for p in a if p["fault"] == "us_frozen")
    assert all(p["delay_s"] is None for p in a if not p["fault"])
    four = [soak.plan(i, seed=1, rate=0.5, faults=("us_frozen", "pod_image07"))["fault"] for i in range(4)]
    assert sorted(f for f in four if f) == ["pod_image07", "us_frozen"], four   # the real run below: one of each
    assert not any(soak.plan(i, rate=0.0)["fault"] for i in range(100))
    assert all(soak.plan(i, rate=1.0)["fault"] for i in range(100))
    assert abs(sum(bool(soak.plan(i, seed=3, rate=0.3)["fault"]) for i in range(100)) - 30) <= 1


# ---- one order, through the real dispatcher ----------------------------------------------------------
def test_a_clean_order_is_done_with_its_timings_and_result():
    b = Bench("clean")
    try:
        s = b.soak(rate=0.0)
        s.run_id = "soak-test"
        rec = s.tick(0)
    finally:
        b.close()
    assert rec["outcome"] == "done" and rec["final_status"] == "done" and not rec["fault"], rec
    assert rec["healed"] is False and rec["residue"] is False and rec["incidents"] == [], rec
    assert 0 <= rec["claimed_after_s"] <= rec["finished_after_s"] < 10, rec
    assert rec["comment"].startswith("RESULT:") and "No faults" in rec["comment"], rec
    assert [ln["order"] for ln in b.lines()] == [rec["order"]]
    out = [e for e in events.read(b.log, src="soak") if e["kind"] == "soak_outcome"]
    assert out and out[-1]["outcome"] == "done" and out[-1]["card"] == rec["card"], out


def test_a_frozen_sensor_is_planted_after_the_claim_healed_and_never_written_on_the_card():
    b = Bench("frozen")
    try:
        s = b.soak(faults=["us_frozen"], rate=1.0)
        rec = s.tick(plan_with("us_frozen", "pick", faults=("us_frozen",)))
    finally:
        b.close()
    assert rec["outcome"] == "done" and rec["healed"] and rec["noticed"], rec
    assert rec["planted_at"] and "us_frozen" in rec["incidents"], rec
    assert [h["action"] for h in rec["heals"]] == ["restart_sensor"] and rec["heals"][0]["ok"], rec["heals"]
    card = b.board.card(rec["card"])
    assert "frozen" not in (card["title"] + card["body"]).lower(), card     # the fault isn't on the card
    kinds = [e["kind"] for e in events.read(b.log)]
    assert kinds.index("card_claimed") < kinds.index("fault_planted") < kinds.index("card_done"), kinds
    assert ("clear", "us_frozen") in b.host.calls                           # never outlives its order
    planted = next(e for e in events.read(b.log) if e["kind"] == "fault_planted")   # the measured time, not the plan
    assert planted["after_claim_s"] == rec["planted_after_claim_s"] and rec["planted_ts"], (planted, rec)
    said = f"landed {rec['planted_after_claim_s']:.1f} s after the soak saw the claim (planned: {rec['delay_s']:g} s"
    assert said in planted["text"], planted["text"]


def test_a_bad_pod_image_waits_for_the_pod_healer_and_counts_podd_s_kind():
    b = Bench("image")
    try:
        rec = b.soak(faults=["pod_image07"], rate=1.0).tick(plan_with("pod_image07", faults=("pod_image07",)))
    finally:
        b.close()
    assert rec["outcome"] == "done" and rec["healed"] and rec["delay_s"] == 0.0, rec      # planted at the claim
    assert "pod_us_mismatch" in rec["incidents"] and "podd:us_mismatch" in rec["incidents"], rec
    assert rec["heals"] and rec["heals"][0]["action"].startswith("wait_for_pod"), rec["heals"]
    assert b.host.image == "good" and not rec["residue"], rec


def test_a_dead_elbow_is_handed_off_and_the_soak_plays_the_person():
    b = Bench("elbow")
    try:
        s = b.soak(faults=["joint_stuck"], rate=1.0)
        rec = s.tick(plan_with("joint_stuck", "pick", faults=("joint_stuck",)))
        time.sleep(0.2)
        ready = dispatch.Host(b.host)("status")
    finally:
        b.close()
    assert rec["outcome"] == "needs_human" and rec["final_status"] == "blocked" and not rec["healed"], rec
    assert rec["person_fix"] == {"handed_off": True, "verified": True, "still": []}, rec["person_fix"]
    assert rec["comment"].startswith("BLOCKED:") and "person" in rec["comment"], rec["comment"]
    assert "joint_stall" in rec["incidents"], rec
    assert not ready["incidents"] and not ready["handed_off"], ready                # the next order can run
    assert not soak.is_failure(rec)
    assert ("reset_scene", None) in b.host.calls and "scene_reset" not in rec, rec   # this host has no such op
    assert s.can_reset is False                                                     # so it isn't asked again


def test_a_failed_pick_puts_the_block_back_when_the_host_can():
    b = Bench("reset", FakeHost(resettable=True))
    try:
        rec = b.soak(faults=["joint_stuck"], rate=1.0).tick(plan_with("joint_stuck", "pick", faults=("joint_stuck",)))
    finally:
        b.close()
    assert rec["outcome"] == "needs_human" and rec["scene_reset"] is True, rec
    assert [c for c in b.host.calls if c[0] == "reset_scene"] == [("reset_scene", None)], b.host.calls
    assert any(e["kind"] == "fault_cleared" and "put the block back" in e["text"] for e in events.read(b.log))


def test_two_clean_orders_in_a_row_that_fail_are_said_loudly():
    b = Bench("streak", FakeHost(block_gone=True))
    try:
        s = b.soak(rate=0.0)
        first = s.tick(0)
        assert not any(e["kind"] == "error" for e in events.read(b.log)), "warned after one"
        s.tick(1)
    finally:
        b.close()
    assert first["outcome"] == "blocked" and "isn't holding the block" in first["comment"], first
    warned = [e["text"] for e in events.read(b.log) if e["kind"] == "error"]
    assert any("two orders in a row with no fault planted" in w for w in warned), warned


def test_an_unnoticed_fault_is_still_cleared_and_a_bad_image_left_over_is_residue():
    b = Bench("quiet", FakeHost(notice=False, pod_polls=30))
    try:
        rec = b.soak(faults=["pod_image02"], rate=1.0).tick(plan_with("pod_image02", faults=("pod_image02",)))
    finally:
        b.close()
    assert rec["outcome"] == "done" and not rec["healed"] and not rec["noticed"], rec  # done, but nothing tripped
    assert rec["plant_note"] == "planted, not noticed during the job", rec
    assert rec["residue"] and rec["residue_cleared"], rec
    assert rec["residue_detail"] == ["the pod runs bad image 02"], rec["residue_detail"]
    assert b.host.image == "good"


def test_an_order_nobody_claims_is_not_claimed_and_moved_out_of_todo():
    b = Bench("unclaimed", dispatcher=False)
    t0 = time.time()
    rec = b.soak(claim_timeout=0.3, rate=1.0, faults=["us_frozen"]).tick(0)
    assert time.time() - t0 < 5
    assert rec["outcome"] == "not_claimed" and rec["planted_at"] is None, rec
    assert not any(op == "inject" for op, _ in b.host.calls), b.host.calls           # nothing planted
    card = b.board.card(rec["card"])
    said = [comment_text(c) for c in card["comments"]]
    assert card["status"] == "blocked" and said[-1].startswith("BLOCKED: soak: no dispatcher claimed"), (card, said)
    assert rec["comment"].startswith("BLOCKED: soak"), rec
    assert any(e["kind"] == "error" and "not claimed" in e["text"] for e in events.read(b.log)), "no loud warning"
    assert soak.is_failure(rec)


def test_a_host_that_doesn_t_answer_or_won_t_clear_places_no_order():
    def down(op, **f):
        raise FileNotFoundError("no socket")
    b = Bench("down", dispatcher=False)
    rec = b.soak(call=down).tick(0)
    assert rec["outcome"] == "host_down" and rec["card"] is None and "isn't answering" in rec["reason"], rec
    stuck = FakeHost(fixable=False)
    stuck._open("us_noisy", "Too much echo jitter while the arm is still")
    b2 = Bench("stuck", stuck, dispatcher=False)
    rec = b2.soak().tick(0)
    assert rec["outcome"] == "arm_not_ready" and rec["card"] is None and b2.board.show() == [], rec
    assert rec["residue_before"] == ["incident inc-1 us_noisy open"] and rec["residue_before_cleared"] is False, rec
    assert [c for c in stuck.calls if c[0] == "repair"] == [("repair", "verify")] * 2, stuck.calls   # not spammed


def test_an_incident_left_before_an_order_is_cleaned_up_first():
    host = FakeHost()
    host._open("us_frozen", "Distance frozen at 130 mm")
    b = Bench("before", host)
    try:
        rec = b.soak(rate=0.0).tick(0)
    finally:
        b.close()
    assert rec["outcome"] == "done" and rec["residue_before"] == ["incident inc-1 us_frozen open"], rec
    assert rec["residue_before_cleared"] is True and not rec["residue"], rec


def test_orders_go_every_n_seconds_from_the_start_and_a_long_job_skips_slots():
    b = Bench("schedule", dispatcher=False)
    s = b.soak(every=0.3, max_orders=4, late_grace_s=0.05, wait=None)
    got = []

    def tick(n, slot, due, skipped):
        got.append((n, slot, skipped, round(time.time() - due, 2)))
        time.sleep(0.75 if n == 1 else 0.05)          # order 2 runs past two slots
        s.records.append({"outcome": "done"})
    s.tick = tick
    t0 = time.time()
    s.run()
    assert [(n, slot, sk) for n, slot, sk, _ in got] == [(0, 0, 0), (1, 1, 0), (2, 4, 2), (3, 5, 0)], got
    assert all(-0.01 <= late < 0.1 for *_, late in got), got                  # on time, from the start
    assert 1.5 - 0.05 <= time.time() - t0 < 2.5


def test_a_job_past_the_timeout_is_recorded_and_its_bad_image_left_for_the_next_check():
    b = Bench("timeout", FakeHost(skill_s=2.0, pod_polls=1000))
    try:
        rec = b.soak(job_timeout=0.5, rate=1.0, faults=["pod_image07"]).tick(0)
    finally:
        b.close()
    assert rec["outcome"] == "timeout" and rec["final_status"] == "doing" and rec["planted_at"], rec
    assert rec["residue"] and rec["residue_cleared"] is None, rec                 # no check while a job may run
    assert rec["residue_detail"] == ["the pod runs bad image 07", "podd has a critical us_mismatch open"], rec
    assert not [c for c in b.host.calls if c[0] == "repair"], b.host.calls
    assert ("clear", "pod_image07") in b.host.calls
    assert any(e["kind"] == "error" and "0.5" not in e["text"] and "after its claim" in e["text"]
               for e in events.read(b.log)), "no loud warning"
    md = soak.report([rec])
    assert "**1 not cleaned up**" in md and "**timeout**" in md, md


def test_a_stop_ends_the_soak_and_records_the_order_in_flight():
    b = Bench("stop", dispatcher=False)
    s = b.soak(claim_timeout=30, max_orders=5)
    threading.Timer(0.3, s.stop.set).start()
    t0 = time.time()
    recs = s.run()
    assert time.time() - t0 < 3 and [r["outcome"] for r in recs] == ["stopped"], recs
    assert b.lines()[0]["outcome"] == "stopped"


# ---- what counts as healed, and the races around a plant ------------------------------------------------
class LateInjectHost(FakeHost):
    """The inject reaches the host only after the dispatcher logged card_done: the fault lands on an idle arm."""

    def __call__(self, op, **f):
        if op == "inject":                                # outside the lock: the dispatcher thread keeps going
            t = time.time()
            while not any(e["kind"] == "card_done" for e in events.read(self.log)) and time.time() - t < 10:
                time.sleep(0.01)
            time.sleep(0.01)
        return super().__call__(op, **f)


def test_a_fault_that_lands_after_the_card_is_done_is_not_healed():
    host = LateInjectHost()
    b = Bench("late-plant", host)
    host.log = b.log
    try:
        rec = b.soak(faults=["us_frozen"], rate=1.0).tick(plan_with("us_frozen", "pick", faults=("us_frozen",)))
    finally:
        b.close()
    assert rec["outcome"] == "done" and rec["planted_at"] and rec["planted_after_job"], rec
    assert not rec["healed"] and not rec["noticed"] and "after the card was done" in rec["plant_note"], rec
    assert ("clear", "us_frozen") in host.calls, host.calls
    md = soak.report([rec])
    assert "| `us_frozen` | 0 | 0 | – | – | 1 landed after the job ended |" in md, md


def test_a_fault_due_on_a_card_that_is_no_longer_in_doing_is_not_planted():
    b = Bench("recheck", dispatcher=False)
    _, card = orders.place(b.board, "wave", source="test")
    b.board.done(card["id"], note="waved")                 # the job finished during the delay
    s = b.soak()
    rec = {"n": 1, "order": "ord-x", "card": card["id"], "fault": "us_frozen", "delay_s": 1.5, "planted_ts": None}
    s._plant(rec, time.time(), time.time())
    s._clear_fault(rec)
    assert not [c for c in b.host.calls if c[0] in ("inject", "clear")], b.host.calls
    assert rec["plant_note"] == "not planted: the card was done by the time the fault was due", rec


class LostReplyHost(FakeHost):
    """The host plants the fault, but the reply never reaches the soak (the client's 60 s timeout)."""

    def __call__(self, op, **f):
        out = super().__call__(op, **f)
        if op == "inject":
            raise TimeoutError("timed out")
        return out


def test_an_inject_whose_reply_was_lost_is_still_cleared():
    host = LostReplyHost(notice=False)
    b = Bench("lost-reply", host)
    try:
        rec = b.soak(faults=["us_frozen"], rate=1.0).tick(plan_with("us_frozen", "pick", faults=("us_frozen",)))
    finally:
        b.close()
    assert rec["outcome"] == "done" and rec["planted_at"] is None and "inject failed" in rec["plant_note"], rec
    assert ("clear", "us_frozen") in host.calls and "us_frozen" not in host.faults, host.calls   # not carried over


class LateClaimBoard(FileBoard):
    """A dispatcher's claim (no compare-and-set, as on the Naïve board) lands right after the soak blocked the
    unclaimed card; the job then finishes a moment later."""

    claimed = False

    def move(self, card_id, **kw):
        out = super().move(card_id, **kw)
        if kw.get("status") == "blocked" and not self.claimed:
            self.claimed = True
            super().move(card_id, status="doing")
            self.comment(card_id, "CLAIMED: a late dispatcher")

            def finish():
                self.comment(card_id, "RESULT: waved, no faults")
                self.done(card_id, note="Done: wave hello, no faults.")
            threading.Timer(0.3, finish).start()
        return out


def test_a_claim_that_lands_as_the_soak_gives_up_is_waited_for():
    b = Bench("late-claim", dispatcher=False)
    b.board = LateClaimBoard(b.dir / "board.json", board_id="brd_test")
    rec = b.soak(claim_timeout=0.2, rate=0.0).tick(0)
    assert rec["outcome"] == "done" and rec["late_claim"] and rec["final_status"] == "done", rec
    assert rec["comment"] == "RESULT: waved, no faults", rec
    assert not any(e["kind"] == "error" and "not claimed" in e["text"] for e in events.read(b.log)), "warned anyway"
    # Claimed between the soak's last look and its block: the compare-and-set refuses, and no BLOCKED is posted.
    b2 = Bench("claim-race", dispatcher=False)
    _, card = orders.place(b2.board, "pick", source="test")
    b2.board.move(card["id"], status="doing")
    s = b2.soak()
    r = {"n": 1, "order": "ord-x", "outcome": None, "comment": None}
    assert s._not_claimed(r, card["id"]) == "doing" and r["late_claim"] and r["outcome"] is None, r
    assert b2.board.card(card["id"])["status"] == "doing" and not b2.board.comments(card["id"])


def test_another_order_on_the_board_skips_the_slot_as_busy():
    b = Bench("busy", dispatcher=False)
    b.host._open("us_frozen", "Distance frozen at 130 mm")             # left over, but not the soak's to touch now
    _, theirs = orders.place(b.board, "wave", source="mission")
    s = b.soak(rate=0.0)
    first = s.tick(0)
    b.board.move(theirs["id"], status="doing")
    second = s.tick(1)
    assert [r["outcome"] for r in (first, second)] == ["busy", "busy"], (first, second)
    assert first["busy_cards"] == [theirs["id"]] and first["card"] is None and "in todo" in first["reason"], first
    assert "in doing" in second["reason"] and len(b.board.show()) == 1, second             # nothing placed
    assert not [c for c in b.host.calls if c[0] == "repair"], b.host.calls                  # no cleanup mid-job
    b.board.add("ARM JOB: pick up the block", body="a story's card, no order id")           # not a dispatcher's
    b.board.done(theirs["id"], note="waved")
    assert s._busy() == [], s._busy()
    md = soak.report([first, second])
    assert "2 busy" in md and f"**Busy:** 2 slots skipped" in md and f"`{theirs['id']}`" in md, md
    assert "**Failures:** none" in md and not soak.is_failure(first), md


class PresenterHost(FakeHost):
    """Mission Control's presenter plants a stuck sensor during the job (a presenter event, then the fault)."""

    def __call__(self, op, **f):
        if op == "skill" and not getattr(self, "pressed", False):
            self.pressed = True
            events.emit("mission", "presenter", "Presenter: plant a stuck distance sensor (us_frozen)", path=self.log,
                        echo=False, action="fault", fault="us_frozen")
            with self.lock:
                self.faults.add("us_frozen")
        return super().__call__(op, **f)


def test_a_presenter_fault_during_a_clean_order_is_outside_not_a_false_alarm():
    host = PresenterHost()
    b = Bench("presenter", host)
    host.log = b.log
    try:
        rec = b.soak(rate=0.0).tick(0)
    finally:
        b.close()
    assert rec["outcome"] == "done" and not rec["fault"] and "us_frozen" in rec["incidents"], rec
    assert [f["fault"] for f in rec["outside_faults"]] == ["us_frozen"], rec["outside_faults"]
    md = soak.report([rec])
    assert "1 had a fault planted from Mission Control during it (not a false alarm)" in md, md
    assert "tripped an incident anyway" not in md, md


def test_the_report_keeps_unnoticed_late_and_outside_faults_apart():
    recs = [rec(1, "done", fault="pod_image02", after=20, noticed=False, healed=False,
                plant_note="planted, not noticed during the job"),
            rec(2, "done", fault="pod_image02", after=30, incidents=["pod_clock_skew"]),
            rec(3, "done", fault="pod_image02", after=15, healed=False, noticed=False, planted_after_job=True),
            rec(4, "done", after=25, incidents=["us_stuck"], outside_faults=[{"fault": "us_frozen", "after_s": 9}]),
            rec(5, "done", after=25, incidents=["us_noisy"])]
    md = soak.report(recs)
    assert ("| `pod_image02` | 2 | 1 | 50% | 30 s | 1 planted, not noticed during the job, "
            "1 landed after the job ended |") in md, md
    assert ("- **No fault planted:** 2 orders, 2 done; 1 had a fault planted from Mission Control during it "
            "(not a false alarm); **1 tripped an incident anyway**") in md, md


# ---- the child dispatcher ----------------------------------------------------------------------------
def test_the_child_dispatcher_starts_holds_the_board_and_stops_on_one_sigterm():
    d, board, log, _ = fresh("child")
    keep = dict(os.environ)
    os.environ.update(ARMSIM_FILE_BOARD=str(d / "board.json"), ARMSIM_EVENTS=str(log), ARMSIM_SOCK=str(d / "no.sock"))
    try:
        board = FileBoard(d / "board.json")                    # what `--board file` opens in the child
        one = soak.ChildDispatcher("scripted", "file", None, 600, d / "logs" / "soak-dispatch.log")
        ok, tail = one.start()
        assert ok and one.alive(), tail
        assert soak.dispatcher_running(board)
        two = soak.ChildDispatcher("scripted", "file", None, 600, d / "logs" / "second.log")
        ok, tail = two.start()
        assert not ok and "another dispatcher already works this board" in tail, tail
        t0 = time.time()
        one.stop()
        assert not one.alive() and time.time() - t0 < 15, time.time() - t0
        assert not soak.dispatcher_running(board)
        assert "dispatcher stopped" in (d / "logs" / "soak-dispatch.log").read_text()
    finally:
        os.environ.clear()
        os.environ.update(keep)


def test_the_cli_refuses_hermes_on_the_file_board_and_unknown_faults():
    for argv, said in ((["run", "--with-dispatcher", "hermes", "--board", "file"], "needs --board naive"),
                       (["run", "--faults", "us_frozen,gremlins"], "unknown fault"),
                       (["run", "--fault-rate", "2"], "from 0 to 1"),
                       (["run", "--every", "0"], "more than 0")):
        try:
            soak.main(argv)
            raise AssertionError(f"{argv} was accepted")
        except SystemExit as exc:
            assert said in str(exc), (argv, exc)


# ---- the report --------------------------------------------------------------------------------------
def rec(n, outcome, *, fault=None, planted=True, after=None, ago=600, **kw):
    ts = time.time() - ago
    base = {"run": "soak-test", "n": n, "ts": ts, "at": soak.iso(ts), "order": f"ord-20261108-0{n}0000-aaaa",
            "card": f"crd_{n:012d}", "job": "pick", "fault": fault, "planted_at": soak.iso(ts + 5) if fault and planted
            else None, "finished_after_s": after, "outcome": outcome, "healed": bool(fault and planted) and outcome == "done",
            "noticed": bool(fault), "incidents": [], "comment": None, "residue": False, "every": 600, "rate": 0.5,
            "faults": ["us_frozen", "pod_image02", "joint_stuck"], "seed": 1}
    return {**base, **kw}


def test_the_report_counts_outcomes_heals_and_lists_every_failure():
    recs = [rec(1, "done", after=40), rec(2, "done", fault="us_frozen", after=70, incidents=["us_frozen"]),
            rec(3, "blocked", fault="pod_image02", after=200, incidents=["pod_clock_skew", "podd:clock_skew"],
                comment="BLOCKED: pod_clock_skew during approach: the sensor pod's fault didn't clear in 90 s"),
            rec(4, "not_claimed", fault="us_frozen", planted=False, comment="BLOCKED: soak: no dispatcher claimed"),
            rec(5, "needs_human", fault="joint_stuck", person_fix={"handed_off": True, "verified": True}),
            rec(6, "done", fault="pod_image02", after=55, residue=True, residue_detail=["the pod runs bad image 02"],
                residue_cleared=True),
            rec(7, "done", after=30, ago=90000),                       # yesterday: outside --since
            rec(8, "stopped", fault="us_frozen", final_status="doing")]
    path = TMP / f"report-{time.time_ns()}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))
    md = soak.report(soak.load(path, soak.parse_since("2h")))
    print(md)
    assert "- **Orders:** 7: **3 done**, 1 blocked, 1 not claimed, 1 needs human, 1 stopped." in md, md
    assert "median 55 s, worst 70 s (order `ord-20261108-020000-aaaa`" in md, md
    assert "| `us_frozen` | 2 | 1 | 50% | 70 s | 1 stopped, 1 planned but not planted |" in md, md
    assert "| `pod_image02` | 2 | 1 | 50% | 55 s | 1 blocked |" in md, md
    assert "| `joint_stuck` | 1 | 0 | – (needs a person) | – | 1 needs human, 1 fixed by hand |" in md, md
    assert "- **No fault planted:** 1 order, 1 done." in md, md
    assert "all cleaned up" in md and "the pod runs bad image 02" in md, md
    fails = md.split("**Failures (2):**")[1]
    assert "`ord-20261108-030000-aaaa`, card `crd_000000000003`" in fails and "didn't clear in 90 s" in fails, fails
    assert "Saw: pod_clock_skew, podd:clock_skew." in fails and "ord-20261108-040000-aaaa" in fails, fails
    assert "ord-20261108-050000-aaaa" not in fails, fails              # a dead elbow fixed by hand is no failure
    everything = soak.report(soak.load(path))
    assert "- **Orders:** 8:" in everything
    assert soak.report([]).startswith("### Soak report\n- **Orders:** none")
    t = soak.parse_since("2026-11-08T02:00")
    assert abs(t - soak.datetime(2026, 11, 8, 2, 0).timestamp()) < 1


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            t0 = time.time()
            fn()
            print(f"ok  {name}  ({time.time() - t0:.1f} s)")
