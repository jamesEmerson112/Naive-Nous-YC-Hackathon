"""The overnight soak: an order every N minutes (10 by default), about half of them with a planted fault,
and one outcome line per order, so the morning shows how often the loop healed itself.

    python -m armsim.soak run --with-dispatcher scripted      # sim: an order every 10 min until Ctrl-C
    python -m armsim.soak run --board naive --hours 5         # the real board; a dispatcher must be running
    python -m armsim.soak run --every 25 --orders 4 --faults us_frozen,pod_image07 --with-dispatcher scripted
    python -m armsim.soak report                              # Markdown for MORNING.md (all of runs/soak.jsonl)
    python -m armsim.soak report --since 70m                  # the last 70 minutes (or --since 2026-11-08T02:00)

Run options: --every 600 (seconds between orders), --fault-rate 0.5, --faults us_frozen,pod_image07,pod_image02
(also us_no_echo, and joint_stuck: a dead elbow that needs a person), --orders N or --hours H (default: until
Ctrl-C), --seed 1, --board file|naive (default $ARMSIM_BOARD_KIND, else file), --board-id ID,
--with-dispatcher scripted|hermes (start `python -m armsim.dispatch` as a child, logging to
runs/logs/soak-dispatch.log; without it a dispatcher must already be running on the same board),
--job-timeout 900 (seconds from the claim), --file runs/soak.jsonl ($ARMSIM_SOAK).
It needs a running arm host with a virtual pod (demo/up.sh, or `python -m armsim.host --pod sim`).

Each order, one at a time (there is one arm):
1. The arm host must answer, with no incident open and a good pod image; anything left over is cleaned up
   first (as in 6).
2. orders.place(): an ARM JOB card, mostly "pick", sometimes "wave". The fault is never written on the card.
3. plan(): --seed and the order's index alone decide the job, whether to plant a fault, which one and when.
   Faulted orders are spread evenly (any run of orders is within one of --fault-rate), and the faults take
   turns in an order reshuffled every round, so 48 orders at 0.5 plant each of 3 faults 8 times.
4. Wait for a dispatcher to claim the card (it moves to doing; up to 120 s). Nobody does: not_claimed, a loud
   warning, and the card moves to blocked, so a dispatcher can't run it late, on top of a later order.
5. A planted fault goes in 1-4 s after the claim through the host's `inject`, with a fault_planted event.
   The host does one thing at a time, so it lands at the next skill boundary (after go_home or reach). joint_stuck can't heal by itself: once the card is blocked, the soak plays the
   person (clear the fault, `repair verify`) and the order counts as needs_human.
6. Wait for done or blocked (--job-timeout). Then the planted fault is cleared, and an incident or a bad pod
   image left over is cleaned up (the pod's own healer gets 90 s, then `repair verify`), so nothing piles up
   for the next order: the line says residue: true and what was left. A pick that didn't finish may have
   dropped the block (a park turns the torque off): the soak asks the host to `reset_scene` (sim; a host
   without that op just answers "unknown op"), and two clean orders blocked in a row are said loudly.
7. One line in runs/soak.jsonl and a soak_outcome event (armsim/events.py).

Orders are --every apart from the start, not from the end of the last job; a job that runs past the next
order's time skips that slot, so orders never queue up behind a running job. The first Ctrl-C (or SIGTERM)
stops cleanly: the order in flight is recorded as stopped and its fault cleared, the child dispatcher gets
one SIGTERM (it finishes its step and blocks its card), and the arm is cleaned up. A second one quits now.

The outcome line, one JSON object per order:
  n, slot, at (ISO, when the order was placed), ts, order, card, job, fault, delay_s, planted_at,
  claimed_after_s and finished_after_s (both counted from placing the order; from the dispatcher's events
  when they're in the same event log), final_status (the card's column at the end), outcome (done, blocked,
  needs_human, not_claimed, timeout, stopped, host_down, arm_not_ready, error), healed (a fault was planted
  and the card is done), noticed (an incident was seen), incidents (kinds seen: the arm's, "podd:<kind>" for
  podd's own, the commander's),
  heals (the commander's heal events), comment (the RESULT: or BLOCKED: line, 200 chars), residue,
  residue_detail, person_fix, and the run's settings (every, rate, faults, seed).
"""
import argparse
import fcntl
import json
import math
import os
import random
import re
import signal
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import dispatch, events, orders
from .board import BoardError, open_board

ROOT = Path(__file__).resolve().parents[1]
SOAK_FILE = Path(os.environ.get("ARMSIM_SOAK") or ROOT / "runs" / "soak.jsonl")
FAULTS = {        # what the soak can plant: the host's inject params, and one sentence for the event log
    "us_frozen": ({}, "the wrist sensor's reading freezes"),
    "us_no_echo": ({}, "the wrist sensor stops hearing its echo"),
    "pod_image07": ({}, "bad pod image 07 goes out (its distance math is truncated)"),
    "pod_image02": ({}, "bad pod image 02 goes out (its clock runs at half speed)"),
    "joint_stuck": ({"joint": 2}, "the elbow servo dies"),
}
DEFAULT_FAULTS = ("us_frozen", "pod_image07", "pod_image02")
NEEDS_PERSON = ("joint_stuck",)                  # planted faults no healer can fix: the soak plays the person
NEEDS_VPOD = ("us_no_echo", "pod_image07", "pod_image02")
WAVE_SHARE = 0.2                                 # the rest are picks
DELAY_S = (1.0, 4.0)                             # from the claim to the fault
CLAIM_TIMEOUT_S = 120.0
CLAIM_POLL_S = 1.0
POD_HEAL_S = 90.0                                # the pod's own healer's time to roll a bad image back
LATE_GRACE_S = 5.0                               # a slot missed by less than this is still used
OUTCOMES = ("done", "blocked", "needs_human", "not_claimed", "timeout", "stopped", "host_down", "arm_not_ready",
            "error")


def iso(ts):
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def when(ts, *, date=True):
    """'Sun Nov 8, 2:31 AM PST' (or '2:31 AM PST'), the way MORNING.md writes times."""
    dt = datetime.fromtimestamp(ts).astimezone()
    clock = f"{dt.hour % 12 or 12}:{dt:%M} {'AM' if dt.hour < 12 else 'PM'} {dt:%Z}".strip()
    return f"{dt:%a %b} {dt.day}, {clock}" if date else clock


def secs(s):
    if s is None:
        return "?"
    s = float(s)
    if s < 120:
        return f"{s:.0f} s"
    return f"{s / 60:.0f} min" if s % 60 == 0 else f"{s / 60:.1f} min"


def plan(index, *, seed=1, rate=0.5, faults=DEFAULT_FAULTS):
    """What order `index` (0-based) is: {index, job, fault (None: a clean order), delay_s}. Pure: the same
    seed and index always give the same plan (string seeds are hashed the same way in every process).

    A fault goes on order i when floor((i + 1) * rate + u) passes an integer, u being a phase taken from the
    seed: faulted orders are spread evenly. They take the faults in turns, reshuffled every round."""
    rng = random.Random(f"soak:{seed}:{index}")
    job = "wave" if rng.random() < WAVE_SHARE else "pick"
    delay = round(rng.uniform(*DELAY_S), 1)
    u = random.Random(f"soak:{seed}:phase").random()
    before = math.floor(index * rate + u)          # faulted orders before this one
    fault = None
    if faults and math.floor((index + 1) * rate + u) > before:
        rnd, k = divmod(before, len(faults))
        turn = list(faults)
        random.Random(f"soak:{seed}:round:{rnd}").shuffle(turn)
        fault = turn[k]
    return {"index": index, "job": job, "fault": fault, "delay_s": delay if fault else None}


def leftovers(st):
    """What the arm host still has that would trip the next order, as sentences (empty: clean)."""
    out = []
    for i in st.get("incidents") or []:
        handed = any(h.get("id") == i.get("id") for h in st.get("handed_off") or [])
        out.append(f"incident {i.get('id')} {i.get('kind')} open" + (" (handed to a person)" if handed else ""))
    pod = st.get("pod") or {}
    if pod.get("image") not in (None, "good"):
        out.append(f"the pod runs bad image {pod['image']}")
    out += [f"podd has a critical {i.get('kind')} open" for i in pod.get("incidents") or []
            if i.get("severity") == "critical"]
    return out


def pod_bad(st):
    pod = st.get("pod") or {}
    return pod.get("image") not in (None, "good") or any(i.get("severity") == "critical"
                                                          for i in pod.get("incidents") or [])


class Soak:
    """Places the orders, plants the faults, watches the cards and writes the outcome lines. Everything it
    touches is injectable (board, host call, event emitter, wait), so the tests run it without a host."""

    def __init__(self, board, *, call=None, emit=None, out=None, every=600.0, rate=0.5, faults=DEFAULT_FAULTS,
                 seed=1, max_orders=None, hours=None, job_timeout=900.0, claim_timeout=CLAIM_TIMEOUT_S,
                 claim_poll_s=CLAIM_POLL_S, poll_s=None, pod_heal_s=POD_HEAL_S, stop=None, wait=None, child=None,
                 events_path=None, late_grace_s=LATE_GRACE_S):
        self.board, self.host = board, dispatch.Host(call)
        self.emit = emit or events.emit
        self.out = Path(out or SOAK_FILE)
        self.every, self.rate, self.faults, self.seed = float(every), float(rate), list(faults), seed
        self.max_orders, self.hours = max_orders, hours
        self.job_timeout, self.claim_timeout, self.claim_poll_s = job_timeout, claim_timeout, claim_poll_s
        self.poll_s = poll_s or (1.0 if getattr(board, "kind", "") == "file" else 3.0)
        self.pod_heal_s, self.late_grace_s = pod_heal_s, late_grace_s
        self.stop = stop or threading.Event()
        self.wait = wait or self.stop.wait           # every pause returns at once on a stop
        self.child = child                           # ChildDispatcher, when the soak started one
        self.events_path = events_path               # None: events.PATH, the shared log the dispatcher writes
        self.settings = {"every": self.every, "rate": self.rate, "faults": self.faults, "seed": seed}
        self.run_id = None
        self.can_reset = True                        # until the host answers that it has no reset_scene op
        self.records = []

    def _ev(self, kind, text, **fields):
        return self.emit("soak", kind, text, run=self.run_id, **fields)

    def warn(self, text, **fields):
        bar = "!" * 100
        print(f"\n{bar}\n[soak] WARNING: {text}\n{bar}\n", file=sys.stderr, flush=True)
        self._ev("error", text, **fields)

    # ---- the schedule --------------------------------------------------------------------------
    def run(self):
        t0 = time.time()
        self.run_id = "soak-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(t0))
        limit = (f"{self.max_orders} orders" if self.max_orders else f"for {self.hours:g} h" if self.hours
                 else "until stopped")
        self._ev("soak_tick", f"soak up: an order every {secs(self.every)} ({limit}), about {self.rate:.0%} with a "
                              f"planted fault ({', '.join(self.faults) or 'none'}; seed {self.seed}), on the "
                              f"{self.board.kind} board {self.board.id}; outcomes in {self.out}", **self.settings)
        n = slot = skipped = 0
        try:
            while not self.stop.is_set():
                if self.max_orders is not None and n >= self.max_orders:
                    break
                due = t0 + slot * self.every
                if self.hours is not None and due - t0 >= self.hours * 3600:
                    break
                while time.time() < due and not self.stop.is_set():
                    self.wait(min(due - time.time(), 60.0))
                if self.stop.is_set():
                    break
                if self.child:
                    self.child.ensure(self)
                self.tick(n, slot, due, skipped)
                n += 1
                nxt, now = slot + 1, time.time()
                while t0 + nxt * self.every < now - self.late_grace_s:   # the job ran past these slots: skip them
                    nxt += 1
                skipped, slot = nxt - slot - 1, nxt
        finally:
            c = Counter(r["outcome"] for r in self.records)
            self._ev("soak_tick", f"soak stopped after {n} order{'' if n == 1 else 's'}: "
                                  + (", ".join(f"{v} {k}" for k, v in c.items()) or "none placed"))
        return self.records

    # ---- one order -----------------------------------------------------------------------------
    def tick(self, n, slot=0, due=None, skipped=0):
        """Place order n (0-based), see it through, write its line. Returns the line."""
        p = plan(n, seed=self.seed, rate=self.rate, faults=self.faults)
        now = time.time()
        rec = {"v": 1, "run": self.run_id, "n": n + 1, "slot": slot, "skipped_slots": skipped, "at": iso(now),
               "ts": round(now, 3), "late_s": None, "order": None, "card": None, "job": p["job"], "fault": p["fault"],
               "delay_s": p["delay_s"], "planted_at": None, "claimed_after_s": None, "finished_after_s": None,
               "final_status": None, "outcome": None, "healed": False, "noticed": False, "incidents": [], "heals": [], "comment": None,
               "residue": False, "residue_detail": [], "person_fix": None, **self.settings}
        try:
            self._order(rec, due if due is not None else now)
        except Exception as exc:  # one bad order must not end the night
            rec["outcome"] = "error"
            rec["error"] = f"{type(exc).__name__}: {exc}"
            self.warn(f"order {rec['n']} ({rec['order'] or 'not placed'}) hit a soak error: {rec['error']}",
                      order=rec["order"], card=rec["card"])
            try:
                self._clear_fault(rec)
                self._cleanup(rec)
            except Exception as exc2:  # the line still gets written
                rec["cleanup_error"] = f"{type(exc2).__name__}: {exc2}"
        self._record(rec)
        return rec

    def _order(self, rec, due):
        st = self.host("status")
        if st.get("unreachable"):
            rec.update(outcome="host_down", reason=st.get("error"))
            self.warn(f"order {rec['n']} not placed: {st.get('error')}")
            return
        if leftovers(st):
            if not self._cleanup(rec, st, what="before"):
                rec.update(outcome="arm_not_ready", reason="left from before and not cleared: "
                                                           + "; ".join(leftovers(self.host("status"))))
                self.warn(f"order {rec['n']} not placed: the arm isn't ready ({rec['reason']})")
                return
        before = {i.get("id") for i in self.host("incidents").get("incidents") or []}
        seen = {}                                   # incident kinds, in the order first seen
        order, card = orders.place(self.board, rec["job"], source="soak")
        t0 = time.time()
        cid = card.get("id")
        rec.update(order=order["id"], card=cid, at=iso(t0), ts=round(t0, 3), late_s=round(t0 - due, 1))
        self._ev("soak_tick", f"order {rec['n']}: {order['id']} ({orders.JOBS[rec['job']]['title']}) is card {cid}",
                 order=order["id"], card=cid, job=rec["job"], n=rec["n"])

        status = self._wait_claim(cid)
        if status is None:
            if self.stop.is_set():
                rec["outcome"] = "stopped"
            else:
                self._not_claimed(rec, cid)
            self._finish(rec, t0, before, seen)
            return
        rec["claimed_after_s"] = round(time.time() - t0, 1)
        if rec["fault"]:
            if status == "doing":
                self._plant(rec, t0)
            else:
                rec["plant_note"] = f"not planted: the card was already {status} when the soak saw it claimed"
        claimed_at = t0 + rec["claimed_after_s"]
        final = self._wait_final(cid, claimed_at, seen)
        rec["finished_after_s"] = round(time.time() - t0, 1) if final in ("done", "blocked") else None
        if final in ("stopped", "timeout"):
            rec["outcome"] = final
            if final == "timeout":
                self.warn(f"order {rec['n']} ({rec['order']}, card {cid}) is still {self._card_status(cid)} "
                          f"{self.job_timeout:.0f} s after its claim", order=rec["order"], card=cid)
            self._clear_fault(rec)                   # but no check at home: a dispatcher may still hold the card
            left = leftovers(self.host("status"))
            if final == "timeout" and left:          # the next order's check cleans it up (residue_before)
                rec.update(residue=True, residue_detail=left, residue_cleared=None)
            elif left:                               # a stop by hand: final_cleanup() runs after the dispatcher stops
                rec["left_at_stop"] = left
            self._finish(rec, t0, before, seen)
            return
        if final == "blocked" and rec["fault"] in NEEDS_PERSON and rec["planted_at"]:
            self._play_the_person(rec)
        self._clear_fault(rec)
        self._cleanup(rec)
        if final != "done" and rec["job"] == "pick":
            self._reset_scene(rec)
        self._finish(rec, t0, before, seen)

    def _card(self, cid):
        try:
            return self.board.card(cid) or {}
        except BoardError:
            return {}

    def _card_status(self, cid):
        return dispatch.card_status(self._card(cid))

    def _wait_claim(self, cid):
        """The card's status once it leaves todo; None if nobody claims it in time (or the soak stops)."""
        t0 = time.time()
        while not self.stop.is_set():
            st = self._card_status(cid)
            if st and st != "todo":
                return st
            if time.time() - t0 >= self.claim_timeout:
                return None
            self.wait(self.claim_poll_s)
        return None

    def _not_claimed(self, rec, cid):
        rec["outcome"] = "not_claimed"
        why = (f"BLOCKED: soak: no dispatcher claimed this card within {self.claim_timeout:.0f} s, so the soak "
               "moved it out of todo (a late run would land on top of a later order). Is a dispatcher running "
               "on this board?")
        card = self._card(cid)
        if dispatch.card_status(card) in (None, "todo"):
            try:
                self.board.comment(cid, why)
                self.board.move(cid, status="blocked")
            except BoardError as exc:
                why += f" (couldn't block the card: {exc})"
        rec["comment"] = why[:200]
        alive = ("the soak's dispatcher is running" if self.child and self.child.alive()
                 else "the soak's dispatcher is NOT running" if self.child
                 else "no dispatcher holds this board's lock" if not dispatcher_running(self.board)
                 else "a dispatcher holds this board's lock")
        self.warn(f"order {rec['n']} ({rec['order']}, card {cid}) was not claimed within "
                  f"{self.claim_timeout:g} s ({alive}; see runs/logs/ and the event log)",
                  order=rec["order"], card=cid)

    def _plant(self, rec, t0):
        fault = rec["fault"]
        params, what = FAULTS[fault]
        self.wait(rec["delay_s"])
        if self.stop.is_set():
            return
        r = self.host("inject", fault=fault, **({"params": params} if params else {}))
        if not r.get("ok"):
            rec["plant_note"] = f"inject failed: {r.get('error')}"
            self.warn(f"couldn't plant {fault} on order {rec['n']}: {r.get('error')}", order=rec["order"],
                      card=rec["card"])
            return
        now = time.time()
        rec["planted_at"], rec["planted_after_s"] = iso(now), round(now - t0, 1)
        self._ev("fault_planted", f"planted {fault} on order {rec['order']}: {what}, {rec['delay_s']:g} s after the "
                                  f"claim (the card says nothing about it)", order=rec["order"], card=rec["card"],
                 fault=fault, where=r.get("where"), delay_s=rec["delay_s"])

    def _wait_final(self, cid, claimed_at, seen):
        """done or blocked; timeout (--job-timeout from the claim) or stopped. Notes incident kinds meanwhile."""
        last_look = 0.0
        while True:
            st = self._card_status(cid)
            if st in ("done", "blocked"):
                return st
            if self.stop.is_set():
                return "stopped"
            if time.time() - claimed_at > self.job_timeout:
                return "timeout"
            if time.time() - last_look >= 2.0:
                self._look(seen)
                last_look = time.time()
            self.wait(self.poll_s)

    def _look(self, seen, st=None):
        st = st or self.host("status")
        for i in st.get("incidents") or []:
            seen.setdefault(i.get("kind"), True)
        for i in (st.get("pod") or {}).get("incidents") or []:
            seen.setdefault(f"podd:{i.get('kind')}", True)
        return st

    def _play_the_person(self, rec):
        """A dead elbow was handed to a person and the card blocked: replace the servo (clear the fault), then
        the check at home a person would run."""
        st = self.host("status")
        handed = bool(st.get("handed_off"))
        self.host("clear", fault=rec["fault"])
        rec["cleared"] = True
        r = self.host("repair", action="verify")
        rec["person_fix"] = {"handed_off": handed, "verified": bool(r.get("verified")), "still": r.get("still")}
        self._ev("fault_cleared", f"the soak played the person on order {rec['order']}: replaced the elbow servo "
                                  f"(cleared {rec['fault']}); the check at home "
                                  + ("passed" if r.get("verified") else f"still fails ({r.get('still')})"),
                 order=rec["order"], card=rec["card"], fault=rec["fault"], verified=bool(r.get("verified")))

    def _clear_fault(self, rec):
        """A planted fault never outlives its order, healed or not (an unnoticed frozen sensor would break the
        next one). `clear` doesn't flash the pod back: a bad image is the pod healer's (see _cleanup)."""
        if not rec.get("planted_at") or rec.get("cleared"):
            return
        r = self.host("clear", fault=rec["fault"])
        rec["cleared"] = bool(r.get("ok"))
        self._ev("fault_cleared", f"cleared {rec['fault']} after order {rec['order']}", order=rec["order"],
                 card=rec["card"], fault=rec["fault"], ok=bool(r.get("ok")))

    def _cleanup(self, rec, st=None, *, what="residue"):
        """Leave nothing for the next order: give a bad pod image to the pod's own healer (pod_heal_s), then
        check open incidents at home (`repair verify`, at most twice: two failed checks hand a sensor incident
        to a person). True when the arm is clean. what: residue (after this order: residue, residue_detail,
        residue_cleared), before (left before it: residue_before, residue_before_cleared) or stop."""
        st = st or self.host("status")
        if st.get("unreachable"):
            return False
        left = leftovers(st)
        if not left:
            return True
        if what == "residue":
            rec.update(residue=True, residue_detail=left)
        elif what == "before":
            rec["residue_before"] = left
        where = {"residue": f"after order {rec['order']}", "before": f"before order {rec['n']}",
                 "stop": "after the stop"}[what]
        self._ev("soak_tick", f"cleaning up {where}: " + "; ".join(left), order=rec.get("order"),
                 card=rec.get("card"), left=left)
        t = time.time()
        while pod_bad(st) and time.time() - t < self.pod_heal_s and not self.stop.is_set():
            self.wait(2.0)
            st = self._look({}, self.host("status"))
        for _ in range(2):
            if not st.get("incidents") or st.get("unreachable"):
                break
            self.host("repair", action="verify")
            st = self.host("status")
        clean = not st.get("unreachable") and not leftovers(st)
        if what in ("residue", "before"):
            rec[f"{'residue' if what == 'residue' else 'residue_before'}_cleared"] = clean
        if not clean:
            self.warn(f"{where} the arm is still not clean: " + "; ".join(leftovers(st) or [st.get("error") or "?"]),
                      order=rec.get("order"), card=rec.get("card"))
        return clean

    def _finish(self, rec, t0, before, seen):
        """Timings from the dispatcher's events, incident kinds from every source, the card's last word."""
        cid = rec["card"]
        evs = [e for e in events.read(self.events_path, since=t0 - 1) if e.get("card") == cid]
        claimed = next((e["ts"] for e in evs if e.get("kind") == "card_claimed"), None)
        ended = next((e["ts"] for e in reversed(evs) if e.get("kind") in ("card_done", "card_blocked")), None)
        if claimed:
            rec["claimed_after_s"] = round(claimed - t0, 1)
        if ended and rec["finished_after_s"] is not None:
            rec["finished_after_s"] = round(ended - t0, 1)
        for e in evs:
            if e.get("kind") in ("incident", "heal") and isinstance(e.get("incident"), dict):
                seen.setdefault(e["incident"].get("kind"), True)
        rec["heals"] = [{"kind": (e.get("incident") or {}).get("kind"), "action": e.get("action"), "ok": e.get("ok"),
                         "seconds": e.get("seconds"), "step": e.get("step")} for e in evs if e.get("kind") == "heal"]
        for i in self.host("incidents").get("incidents") or []:
            if i.get("id") not in before:
                seen.setdefault(i.get("kind"), True)
        rec["incidents"] = [k for k in seen if k]
        rec["noticed"] = bool(rec["incidents"])
        card = self._card(cid)
        rec["final_status"] = dispatch.card_status(card)
        try:
            mine = dispatch.this_run(self.board.comments(cid))
        except BoardError:
            mine = []
        last = next((t for t in reversed(mine) if t.startswith(("RESULT", "BLOCKED"))), None)
        rec["comment"] = (last or rec["comment"] or "")[:200] or None
        if rec["outcome"] is None:
            if rec["final_status"] == "done":
                rec["outcome"] = "done"
            elif rec["fault"] in NEEDS_PERSON and rec["planted_at"]:
                rec["outcome"] = "needs_human"
            else:
                rec["outcome"] = "blocked"
        rec["healed"] = bool(rec["planted_at"]) and rec["outcome"] == "done"

    def _record(self, rec):
        self.out.parent.mkdir(parents=True, exist_ok=True)
        with open(self.out, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        self.records.append(rec)
        clean = [r for r in self.records if not r.get("fault") and r.get("card")]
        if len(clean) >= 2 and all(r["outcome"] == "blocked" for r in clean[-2:]) and clean[-1] is rec:
            self.warn("two orders in a row with no fault planted didn't end done: the loop itself is broken. In sim "
                      "that is often the block knocked out of place by an earlier failed pick; restart the arm host "
                      f"between orders (demo/down.sh && demo/up.sh). Last: {rec.get('comment') or rec['outcome']}",
                      order=rec.get("order"), card=rec.get("card"))
        what = rec["fault"] if rec.get("planted_at") else "no fault"
        tail = {"done": f"done in {secs(rec['finished_after_s'])}" + (f", healed {rec['fault']}" if rec["healed"] else ""),
                "needs_human": "handed to a person, as a dead elbow should be"
                               + ("; fixed by hand" if (rec.get("person_fix") or {}).get("verified") else
                                  "; the check after the fix by hand FAILED"),
                "not_claimed": f"NOT CLAIMED in {self.claim_timeout:.0f} s",
                "stopped": f"stopped by hand while the card was {rec['final_status'] or 'unknown'}"}.get(
            rec["outcome"], f"{rec['outcome'].upper()}: " + re.sub(r"^BLOCKED:\s*", "", str(
                rec.get("comment") or rec.get("reason") or rec.get("error"))))
        self._ev("soak_outcome", f"order {rec['n']} ({rec['order'] or 'not placed'}, {rec['job']}, {what}): {tail}"
                                 + (" (cleaned up after it)" if rec["residue"] else ""),
                 **{k: rec.get(k) for k in ("order", "card", "job", "fault", "outcome", "healed", "final_status",
                                           "claimed_after_s", "finished_after_s", "incidents", "residue")})

    def _reset_scene(self, rec):
        """A pick that didn't finish can leave the block out of place: a park while holding it opens the jaws
        (torque off) and drops it, and a job stopped after `lift` leaves it in the gripper. Every later pick then
        fails ("the gripper isn't holding the block"). In sim the person puts the block back: the host's
        `reset_scene` op, when it has one (a newer host); otherwise rec["scene_reset"] stays None."""
        if not self.can_reset:
            return
        r = self.host("reset_scene")
        if r.get("ok"):
            rec["scene_reset"] = True
            self._ev("fault_cleared", f"put the block back after order {rec.get('order')} (reset_scene)",
                     order=rec.get("order"), card=rec.get("card"))
        elif "unknown op" in str(r.get("error")):
            self.can_reset = False
            self._ev("soak_tick", "this arm host has no reset_scene op: a pick that fails can leave the block out of "
                                  "place for the next ones (restart the host to put it back)")
        else:
            rec["scene_reset"] = False

    def final_cleanup(self):
        """After a stop, once no dispatcher of ours holds the arm: nothing planted or open is left behind (and in
        sim the block goes back, in case the stop came between grip and release)."""
        clean = self._cleanup({}, what="stop")
        last = self.records[-1] if self.records else {}
        if last.get("outcome") == "stopped" and last.get("job") == "pick":
            self._reset_scene({"order": last.get("order")})
        return clean


# ---- the dispatcher, as a child -------------------------------------------------------------------
def dispatcher_running(board):
    """Does a dispatcher hold this board's lock (armsim.dispatch.take_lock) on this machine?"""
    path = dispatch.lock_path(board)
    if not path.exists():
        return False
    with open(path, "a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
    return False


def log_tail(path, n=8):
    try:
        return "\n".join(Path(path).read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return ""


class ChildDispatcher:
    """`python -m armsim.dispatch` as the soak's child, in its own session: a Ctrl-C in the soak's terminal
    must not reach it, because the soak sends it exactly one SIGTERM (finish the step, block the card, exit)
    and a second signal would stop it in the middle of a skill."""

    def __init__(self, exec_kind, board_kind, board_id, job_timeout, log):
        self.argv = [sys.executable, "-u", "-m", "armsim.dispatch", "--board", board_kind, "--exec", exec_kind,
                     "--job-timeout", f"{job_timeout:g}"] + (["--board-id", board_id] if board_id else [])
        self.log, self.proc = Path(log), None

    def start(self):
        """(ok, log tail): not ok when it exits within 2 s (another dispatcher holds the board, say)."""
        self.log.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in (str(ROOT), os.environ.get("PYTHONPATH")) if p))
        with open(self.log, "a", encoding="utf-8") as out:
            out.write(f"# {dispatch.local_time()} the soak starts: {' '.join(self.argv[2:])}\n")
            out.flush()
            self.proc = subprocess.Popen(self.argv, cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, start_new_session=True)
        time.sleep(2.0)
        if self.proc.poll() is not None:
            return False, log_tail(self.log)
        return True, None

    def alive(self):
        return bool(self.proc) and self.proc.poll() is None

    def ensure(self, soak):
        """Before each order: a dispatcher that died overnight is said loudly and started again."""
        if self.alive():
            return
        code = self.proc.returncode if self.proc else None
        soak.warn(f"the soak's dispatcher is gone (exit {code}); starting it again. Its log ({self.log}) ends:\n"
                  + log_tail(self.log))
        ok, tail = self.start()
        if not ok:
            soak.warn(f"the dispatcher exited again at once:\n{tail}")

    def stop(self, wait=120.0):
        if not self.alive():
            return
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(wait)
        except subprocess.TimeoutExpired:
            print(f"[soak] the dispatcher (pid {self.proc.pid}) didn't stop in {wait:.0f} s: killing it",
                  file=sys.stderr, flush=True)
            self.proc.kill()
            self.proc.wait(5)


# ---- the report -----------------------------------------------------------------------------------
def parse_since(text):
    """'70m', '2h', '1d', '90s' (ago), or an ISO time (no zone: this machine's local time)."""
    if not text:
        return None
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd])\s*", text)
    if m:
        return time.time() - float(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
    try:
        return datetime.fromisoformat(text.strip()).timestamp()
    except ValueError as exc:
        raise SystemExit(f"--since {text!r}: use an ISO time (2026-11-08T02:00) or 70m / 2h / 1d") from exc


def load(path=None, since=None):
    p = Path(path or SOAK_FILE)
    if not p.exists():
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since is None or (rec.get("ts") or 0) >= since:
                out.append(rec)
    return out


def is_failure(rec):
    """Anything but done, except a dead elbow handed to a person and fixed by hand, and a stop by hand."""
    if rec.get("outcome") in ("done", "stopped"):
        return False
    if rec.get("outcome") == "needs_human":
        return not (rec.get("person_fix") or {}).get("verified")
    return True


def report(recs):
    """A Markdown summary, ready to paste into MORNING.md."""
    if not recs:
        return "### Soak report\n- **Orders:** none in this window.\n"
    recs = sorted(recs, key=lambda r: r.get("ts") or 0)
    t0 = recs[0].get("ts") or 0
    t1 = max((r.get("ts") or 0) + (r.get("finished_after_s") or 0) for r in recs)
    same_day = datetime.fromtimestamp(t0).date() == datetime.fromtimestamp(t1).date()
    by = Counter(r.get("outcome") for r in recs)
    lines = [f"### Soak report: {when(t0)} to {when(t1, date=not same_day)}"]
    extra = [f"{by[k]} {k.replace('_', ' ')}" for k in OUTCOMES[2:] if by.get(k) and k != "not_claimed"]
    lines.append(f"- **Orders:** {len(recs)}: **{by.get('done', 0)} done**, {by.get('blocked', 0)} blocked, "
                 f"{by.get('not_claimed', 0)} not claimed" + "".join(f", {x}" for x in extra) + ".")
    runs = {}
    for r in recs:
        runs.setdefault(r.get("run"), r)
    for r in runs.values():
        lines.append(f"- **Settings** (`{r.get('run')}`): an order every {secs(r.get('every'))}, about "
                     f"{float(r.get('rate') or 0):.0%} with a planted fault from {', '.join(r.get('faults') or []) or 'none'}, "
                     f"seed {r.get('seed')}.")
    done = [r for r in recs if r.get("outcome") == "done" and r.get("finished_after_s") is not None]
    if done:
        worst = max(done, key=lambda r: r["finished_after_s"])
        lines.append(f"- **Time to done** (order placed to card done): median "
                     f"{secs(statistics.median(r['finished_after_s'] for r in done))}, worst "
                     f"{secs(worst['finished_after_s'])} (order `{worst['order']}`, card `{worst['card']}`).")
    clean = [r for r in recs if not r.get("fault")]
    if clean:
        tripped = sum(1 for r in clean if r.get("incidents"))
        lines.append(f"- **No fault planted:** {len(clean)} order{'' if len(clean) == 1 else 's'}, "
                     f"{sum(r.get('outcome') == 'done' for r in clean)} done"
                     + (f"; **{tripped} tripped an incident anyway** (a false alarm, or the sim drifting)" if tripped
                        else "") + ".")
    residue = [r for r in recs if r.get("residue")]
    if residue:
        stuck = [r for r in residue if r.get("residue_cleared") is not True]
        lines.append(f"- **Residue:** {len(residue)} order{'' if len(residue) == 1 else 's'} left something for the "
                     f"next one ({'; '.join(sorted({d for r in residue for d in r.get('residue_detail') or []}))}); "
                     + (f"**{len(stuck)} not cleaned up**." if stuck else "all cleaned up."))
    else:
        lines.append("- **Residue:** none; every order left the arm clean.")

    faulted = [r for r in recs if r.get("fault")]
    if faulted:
        names = [f for f in FAULTS if any(r["fault"] == f for r in faulted)]
        names += sorted({r["fault"] for r in faulted} - set(names))
        lines += ["", "| Fault | Planted | Healed | Healed % | Median to done | Other outcomes |",
                  "|---|---|---|---|---|---|"]
        for name in names:
            rs = [r for r in faulted if r["fault"] == name]
            planted = [r for r in rs if r.get("planted_at")]
            healed = [r for r in planted if r.get("healed")]
            pct = f"{len(healed) / len(planted):.0%}" if planted else "–"
            if name in NEEDS_PERSON:
                pct = "– (needs a person)"
            to_done = [r["finished_after_s"] for r in healed if r.get("finished_after_s") is not None]
            other = Counter(r.get("outcome") for r in planted if not r.get("healed"))
            notes = [f"{v} {k.replace('_', ' ')}" for k, v in other.items()]
            fixed = sum(1 for r in planted if (r.get("person_fix") or {}).get("verified"))
            quiet = sum(1 for r in healed if not r.get("noticed"))
            if quiet:
                notes.append(f"{quiet} of the healed tripped no incident")
            if name in NEEDS_PERSON and planted:
                notes.append(f"{fixed} fixed by hand")
            if len(rs) > len(planted):
                notes.append(f"{len(rs) - len(planted)} planned but not planted")
            lines.append(f"| `{name}` | {len(planted)} | {len(healed)} | {pct} | "
                         f"{secs(statistics.median(to_done)) if to_done else '–'} | {', '.join(notes) or '–'} |")
        lines.append("")
    fails = [r for r in recs if is_failure(r)]
    if not fails:
        lines.append("- **Failures:** none.")
    else:
        lines.append(f"- **Failures ({len(fails)}):**")
        for r in fails:
            why = r.get("comment") or r.get("reason") or r.get("error") or "no reason recorded"
            seen = f" Saw: {', '.join(r['incidents'])}." if r.get("incidents") else ""
            lines.append(f"  - {when(r.get('ts') or 0, date=False)}, order {r.get('n')} `{r.get('order') or '-'}`, card "
                         f"`{r.get('card') or '-'}` ({r.get('job')}, {r.get('fault') or 'no fault'}): "
                         f"**{(r.get('outcome') or '?').replace('_', ' ')}**. {why.rstrip('. ')}.{seen}")
    return "\n".join(lines) + "\n"


# ---- the command line -----------------------------------------------------------------------------
def cmd_run(a):
    faults = [f.strip() for f in (a.faults or "").split(",") if f.strip()]
    unknown = [f for f in faults if f not in FAULTS]
    if unknown:
        raise SystemExit(f"unknown fault(s) {unknown}; the soak can plant {sorted(FAULTS)}")
    if not 0.0 <= a.fault_rate <= 1.0:
        raise SystemExit("--fault-rate is a share of orders, from 0 to 1")
    kind = a.board or os.environ.get("ARMSIM_BOARD_KIND", "file")
    if a.with_dispatcher == "hermes" and kind == "file":
        raise SystemExit("--with-dispatcher hermes needs --board naive: Hermes reads and closes cards only through "
                         "its naive_board MCP. Use --with-dispatcher scripted with the file board.")
    st = dispatch.Host()("status")
    if st.get("unreachable"):
        raise SystemExit(f"{st['error']}. Start the arm host first (demo/up.sh, or python -m armsim.host --pod sim).")
    if not (st.get("pod") or {}).get("image") and set(faults) & set(NEEDS_VPOD):
        raise SystemExit(f"{sorted(set(faults) & set(NEEDS_VPOD))} need the arm host's virtual pod (--pod sim, as "
                         "demo/up.sh starts it); this host has none. Use --faults us_frozen,joint_stuck here.")
    board = open_board(kind, a.board_id)
    try:
        board.show("todo")
    except BoardError as exc:
        raise SystemExit(f"can't read the {kind} board: {exc}") from exc
    out = Path(a.file)
    stop = threading.Event()

    def on_signal(signum, frame):
        if stop.is_set():
            raise KeyboardInterrupt
        stop.set()
        print("[soak] stopping: recording the order in flight, then stopping the dispatcher and cleaning up "
              "(signal again to quit now)", flush=True)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    child = None
    if a.with_dispatcher:
        child = ChildDispatcher(a.with_dispatcher, kind, a.board_id, max(a.job_timeout / 2, a.job_timeout - 300),
                                out.parent / "logs" / "soak-dispatch.log")
        ok, tail = child.start()
        if not ok:
            raise SystemExit(f"the dispatcher exited at once (another one already working this board?). "
                             f"Its log, {child.log}:\n{tail}")
        print(f"[soak] dispatcher started (pid {child.proc.pid}, {a.with_dispatcher}); its log: {child.log}", flush=True)
    soak = Soak(board, out=out, every=a.every, rate=a.fault_rate, faults=faults, seed=a.seed, max_orders=a.orders,
                hours=a.hours, job_timeout=a.job_timeout, stop=stop, child=child)
    if not child and not dispatcher_running(board):
        soak.warn(f"no dispatcher holds the {kind} board's lock ({dispatch.lock_path(board)}): start one "
                  f"(python -m armsim.dispatch --board {kind} ...), or pass --with-dispatcher. Going on anyway.")
    hard = False
    try:
        soak.run()
    except KeyboardInterrupt:
        hard = True
        print("[soak] quitting now: the order in flight has no line and the arm wasn't cleaned up", file=sys.stderr)
    finally:
        if child:
            child.stop()
        last = soak.records[-1] if soak.records else {}
        if not child and last.get("outcome") == "stopped" and last.get("final_status") == "doing":
            print(f"[soak] an outside dispatcher still holds card {last.get('card')}: not touching the arm",
                  flush=True)
        elif not hard:
            soak.final_cleanup()
        if soak.records:
            print("\n" + report(soak.records), flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="The overnight soak: an order every N minutes, about half with a "
                                             "planted fault; and its Markdown report.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="place orders and plant faults until stopped (or --orders / --hours)")
    r.add_argument("--every", type=float, default=600.0, help="seconds between orders, from the start (default 600)")
    r.add_argument("--fault-rate", type=float, default=0.5, help="share of orders with a planted fault (default 0.5)")
    r.add_argument("--faults", default=",".join(DEFAULT_FAULTS),
                   help=f"comma-separated, from {', '.join(FAULTS)} (default {','.join(DEFAULT_FAULTS)})")
    lim = r.add_mutually_exclusive_group()
    lim.add_argument("--orders", type=int, default=None, help="stop after this many orders")
    lim.add_argument("--hours", type=float, default=None, help="place no new order after this many hours")
    r.add_argument("--seed", type=int, default=1, help="the plan's seed: the same seed plants the same faults (default 1)")
    r.add_argument("--board", choices=["file", "naive"], default=None,
                   help="file (runs/board.json, or $ARMSIM_FILE_BOARD) or naive; default $ARMSIM_BOARD_KIND, else file")
    r.add_argument("--board-id", default=None, help="the board's id (naive default: $ARMSIM_BOARD or the team board)")
    r.add_argument("--with-dispatcher", choices=["scripted", "hermes"], default=None,
                   help="start python -m armsim.dispatch with this commander as a child (log: runs/logs/soak-dispatch.log)")
    r.add_argument("--job-timeout", type=float, default=900.0,
                   help="seconds from the claim to done or blocked (default 900; a child dispatcher gets 300 less)")
    r.add_argument("--file", default=str(SOAK_FILE), help="the outcome lines (default runs/soak.jsonl, or $ARMSIM_SOAK)")
    p = sub.add_parser("report", help="a Markdown summary of the outcome lines, for MORNING.md")
    p.add_argument("--since", default=None, help="only orders placed since: an ISO time, or 70m / 2h / 1d ago")
    p.add_argument("--file", default=str(SOAK_FILE), help="the outcome lines (default runs/soak.jsonl, or $ARMSIM_SOAK)")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        cmd_run(a)
    else:
        print(report(load(a.file, parse_since(a.since))), end="")


if __name__ == "__main__":
    main()
