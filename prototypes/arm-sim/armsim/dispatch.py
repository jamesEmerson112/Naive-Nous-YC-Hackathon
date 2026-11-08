"""The autonomous loop's dispatcher: an order becomes an ARM JOB card, a commander picks it up on its
own, the arm works, a fault heals, the card closes. Nobody types.

    python -m armsim.dispatch                         # file board (runs/board.json), scripted commander
    python -m armsim.dispatch --once                  # work the oldest waiting card, if there is one, then exit
    python -m armsim.dispatch --max-jobs 3            # work three cards, then exit
    python -m armsim.dispatch --board naive --exec hermes     # the real Naïve board; Hermes does the jobs
    python -m armsim.orders add pick                  # (another terminal) place an order

Options: --board file|naive (default $ARMSIM_BOARD_KIND, else file), --board-id ID, --exec scripted|hermes
(default scripted), --every 5 (seconds between polls while idle), --once, --max-jobs N, --job-timeout 600
(seconds; Hermes is killed after it, the scripted commander stops between steps).
The worker/manager healer (armsim/worker.py, docs/worker-manager-healer.md), scripted commander only:
--manager off|fake|hermes (default off: today's scripted heal), --manager-url (default $ARMSIM_MANAGER_URL;
the key is $ARMSIM_MANAGER_KEY), --manager-deadline 90, --fake-mode good|bad_json|off_list|unsafe|slow|429|
500|failed|refused (--manager fake), --worker-lm off|ollama, --ollama URL, --worker-model qwen3.5:4b,
--exec-via code|lm (lm: the worker's model issues the checked action, gated to that exact call).
--wrong-action off|redo (default off; redo needs --manager): a missed grasp is a wrong action (category 2,
armsim/redo.py): reverse it, the manager corrects the command, the arm redoes it, at most twice.

How it works, one card at a time (there is one arm):
1. At startup it takes the board's dispatcher lock (one dispatcher per board: a second one exits), then
   moves any ARM JOB card left in doing to blocked: a dispatcher stopped mid-job, and re-running half a
   physical job is unsafe.
2. While idle it lists the todo cards that are ARM JOBs (armsim/orders.py), oldest first. Before claiming
   one it reads the arm host's status: an incident handed off to a person, or a host that doesn't answer,
   means wait (said once in the event log, not once per poll).
3. Claim: the card moves to doing, with a `CLAIMED:` comment.
4. The commander works the card:
   - scripted (no LLM; the deterministic stand-in for Hermes, used in sim, the soak and tests): runs the
     card's steps as arm host skills. When the watchdog reports an incident it heals it the way the
     healer tools do (a stuck sensor: restart it; a sensor-pod fault: wait for the pod's own healer and
     the host's check; a stalled joint or anything else: a checked retry at home), then retries that
     step once; at most 2 heals a job. Then `RESULT:` and done with a note, or `BLOCKED:` and blocked.
   - hermes: `hermes chat --oneshot -t so101_arm,mcp-naive_board -q <prompt naming the board and card>`.
     Hermes reads the card through its naive_board MCP and does the same with the so101_arm tools. Its
     output goes into the event log line by line and into runs/commander/<card>.log.
5. The card is read back: if it isn't done and has no `BLOCKED:` comment from this run, the dispatcher
   comments `BLOCKED:` itself; a card that isn't done always ends in blocked.

Stopping: the first SIGTERM or Ctrl-C lets the current skill finish, blocks the card (half a job is not
re-run on its own) and exits; a second one stops at once. A dispatcher killed outright leaves its card in
doing, and the next one to start moves it to blocked (step 1).

Everything goes into the shared event log (armsim/events.py): src dispatch for the dispatcher (dispatcher,
card_claimed, card_done, card_blocked, job_summary, waiting, error), src commander for the commander
(job_started, skill, incident, heal, job_result, commander_line).
"""
import argparse
import fcntl
import os
import re
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from . import events
from .board import BoardError, comment_text, open_board
from .client import call as host_call
from .client import socket_path
from .orders import JOBS, is_arm_job, parse_card

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
COMMANDER_LOGS = RUNS / "commander"
MAX_HEALS = 2
CLAIM_RETRY_S = 60                  # a card whose claim failed is skipped this long
# "The reading disagrees with the pose": the arm's own check can fire before podd flags the pod's
# firmware (bad image 07 opens us_offset, then podd's us_mismatch), so give podd a moment first.
MAYBE_POD_KINDS = ("us_offset", "us_scale", "us_residual")

HERMES = ["hermes", "chat", "--oneshot", "-t", "so101_arm,mcp-naive_board", "-q"]   # stories.py COMMANDER's call
PROMPT = (
    "You are the arm commander for our robot-arm company. Your job is card {card} on the Naïve board {board}; "
    "other cards on that board are not yours, so leave them alone. Read card {card} and its comments, then do "
    "the job it describes with the so101_arm tools. Rules: only the arm_* skills move the arm. Use the healer "
    "tools only after a skill reports an incident or a tool call is blocked: call arm_status, fix the cause, and "
    "retry that step once. A stuck or frozen distance: arm_restart_sensor. A sensor-pod fault (arm_status lists "
    "pod incidents): the pod's own healer fixes it within a minute, so call arm_status every few seconds until "
    "the arm's incidents are empty, and use arm_check only once the pod's incidents are gone. Anything else: "
    "arm_check. When the job is done, add a comment on card {card} starting 'RESULT:' that says what you did and "
    "any fault you healed, then move card {card} to done with a one-sentence note. If you can't finish, comment "
    "'BLOCKED:' on card {card} with the reason.")
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def local_time():
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def short(text, n=160):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


def card_status(card):
    return card.get("status") or card.get("column")


def created(item):
    return item.get("created_at") or item.get("createdAt") or ""


def this_run(comments):
    """The comments after the last CLAIMED: one (this run's), as text, oldest first. A card a person
    moved back from blocked to todo still carries the last run's BLOCKED: comment."""
    texts = [comment_text(c).strip() for c in sorted(comments, key=created)]
    last = max((i for i, t in enumerate(texts) if t.startswith("CLAIMED")), default=-1)
    return texts[last + 1:]


class Host:
    """The arm host through an injectable call (armsim.client.call by default). Never raises: a host
    that doesn't answer is {"ok": False, "unreachable": True, "error": ...}."""

    def __init__(self, call=None):
        self._call = call or host_call

    def __call__(self, op, **fields):
        try:
            return self._call(op, **fields)
        except (OSError, ValueError) as exc:
            return {"ok": False, "unreachable": True,
                    "error": f"the arm host isn't answering at {socket_path()} ({type(exc).__name__}: {exc})"}


def skill_text(step, reply):
    """One short line for a skill's reply: 'approach: arrived, switched at 75 mm'."""
    if not reply.get("ok"):
        return f"{step}: refused ({short(reply.get('error'), 100)})"
    res = reply.get("result") or {}
    bits = []
    if res.get("halted"):
        bits.append(f"halted ({res['halted']})")
    elif res.get("arrived") is True:
        bits.append("arrived")
    elif res.get("arrived") is False:
        bits.append("didn't arrive" + (f": {res['aborted']}" if res.get("aborted") else ""))
    if res.get("switched_at_mm") is not None:
        bits.append(f"switched at {res['switched_at_mm']} mm")
    if res.get("grasp"):
        bits.append("holding the block" if res["grasp"].get("holding") else "not holding anything")
    if reply.get("incident"):
        bits.append(f"parked: {reply['incident'].get('summary')}")
    return f"{step}: " + (", ".join(bits) or "done")


class ScriptedExecutor:
    """The commander without an LLM: the card's steps as host skills, healing what the watchdog reports.

    The healing pattern (the same tools Hermes has):
      a sensor-pod fault (a pod_* kind, or podd has a critical incident tied to it)
          -> wait for the pod's own healer and the host's own check at home; if podd is clear and the
             arm's incident lingers, `repair verify`
      a sensor kind (us_*)   -> `repair restart_sensor` (podd's `us off`, `us on`, then the check at home)
      joint_stall, the rest  -> `repair verify` (a checked retry at home)
      still open             -> a person: BLOCKED.
    """

    name = "scripted"

    def __init__(self, board, *, call=None, emit=None, max_heals=MAX_HEALS, pod_wait_s=90.0, pod_clear_s=10.0,
                 hold_s=6.0, poll_s=1.0, sleep=time.sleep, worker=None, redo=None):
        self.board, self.host = board, Host(call)
        self.worker = worker                  # armsim.worker.Worker: heal through the manager first (None: off)
        self.redo = redo                      # armsim.redo.Redo: redo a missed grasp (None: block, as before)
        self.replies = {}                     # step -> its last skill reply (the redo's evidence)
        self.job_steps = []
        self.emit = emit or events.emit
        self.max_heals, self.pod_wait_s, self.pod_clear_s = max_heals, pod_wait_s, pod_clear_s
        self.hold_s, self.poll_s, self.sleep = hold_s, poll_s, sleep
        self.ctx, self.healed, self.pod_seen = {}, [], {}
        self.stop = threading.Event()         # set: block the card at the next step instead of going on

    def _ev(self, kind, text, **fields):
        return self.emit("commander", kind, text, **self.ctx, **fields)

    # ---- the job -------------------------------------------------------------------------------
    def run(self, card, timeout=None):
        job = parse_card(card)
        self.ctx = {"card": card["id"], "order": job["order"], "job": job["job"], "exec": self.name}
        self.healed, self.replies = [], {}
        t0 = time.time()
        steps = self.job_steps = job["steps"]
        self._ev("job_started", f"the scripted commander starts {JOBS[job['job']]['title']}: {', '.join(steps)}",
                 steps=steps)
        for step in steps:
            if timeout and time.time() - t0 > timeout:
                return self._blocked(f"ran past the job timeout ({timeout:.0f} s) before {step}", t0)
            ok, why = self._step(step)
            if not ok:
                return self._blocked(why, t0)
        return self._finish(job, t0)

    def _step(self, step):
        """Run one step; after an incident, heal it and retry the step once. (ok, why-not)."""
        for attempt in (1, 2):
            if self.stop.is_set():
                return False, f"the dispatcher was stopped in the middle of the job, before {step}; the rest wasn't run"
            ok, why = self._clear_between_steps(step)
            if not ok:
                return False, why
            t = time.time()
            reply = self.replies[step] = self.host("skill", name=step)
            if reply.get("unreachable"):
                return False, f"the arm host stopped answering during {step}: {reply['error']}"
            self._ev("skill", skill_text(step, reply), step=step, ok=bool(reply.get("ok")) and not reply.get("incident"),
                     ms=round((time.time() - t) * 1000), attempt=attempt)
            inc = reply.get("incident") or (None if reply.get("ok") else (reply.get("incidents") or [None])[0])
            if inc:
                self._incident(inc, step)
                if attempt == 2:
                    return False, f"{step} hit a second fault on its retry ({inc.get('kind')}: {inc.get('summary')})"
                ok, why = self._heal(inc, step)
                if not ok:
                    return False, why
                continue                                    # healed: retry this step once
            if not reply.get("ok"):
                return False, f"the arm host refused {step}: {reply.get('error')}"
            res = reply.get("result") or {}
            if res.get("arrived") is False:
                return False, f"{step} didn't arrive ({res.get('aborted') or 'no reason given'})"
            if step in ("grip", "lift") and res.get("grasp") and not res["grasp"].get("holding"):
                if self.redo:                       # a wrong action: reverse, correct, redo (armsim/redo.py)
                    return self.redo.wrong_action(self, step, reply)
                return False, f"{step}: the gripper isn't holding the block"
            return True, None
        return False, f"{step} didn't finish"

    def _clear_between_steps(self, step):
        """A pod incident can open while the arm stands between steps: heal what's open first."""
        for _ in range(self.max_heals + 1):
            st = self.host("status")
            if st.get("unreachable"):
                return False, f"the arm host stopped answering before {step}: {st['error']}"
            open_now = st.get("incidents") or []
            if not open_now:
                return True, None
            self._incident(open_now[0], step, before=True)
            ok, why = self._heal(open_now[0], step)
            if not ok:
                return False, why
        return False, f"incidents keep opening before {step}"

    def _incident(self, inc, step, before=False):
        where = f"before {step}" if before else f"during {step}"
        self._ev("incident", f"incident {where}: {inc.get('kind')}: {inc.get('summary')}", step=step,
                 incident={"id": inc.get("id"), "kind": inc.get("kind"), "summary": inc.get("summary")})

    # ---- healing -------------------------------------------------------------------------------
    def _heal(self, inc, step):
        kind, summary, iid = inc.get("kind", "?"), inc.get("summary", ""), inc.get("id")
        if len(self.healed) >= self.max_heals:
            return False, (f"a fault after {self.max_heals} heals in this job ({kind}: {summary}); "
                           "leaving the arm parked for a person")
        t0 = time.time()
        st = self.host("status")
        handed = self._handed_off(st, iid)
        if handed:
            return False, f"{kind} was handed to a person: {handed.get('handoff_reason') or summary}"
        done = self.worker.heal(self, inc, step) if self.worker else None   # None: the scripted heal below
        if done:
            ok, action, detail = done
        elif self._pod_side(inc, st) or (kind in MAYBE_POD_KINDS and self._hold_for_pod(inc)):
            ok, action, detail = self._wait_for_pod(inc)
        elif kind.startswith("us_"):
            r = self.host("repair", action="restart_sensor")
            if r.get("verified"):
                ok, action, detail = True, "restart_sensor", "restarted the sensor through podd; the check at home passed"
            elif self._pod_side(inc, self.host("status")):
                ok, action, detail = self._wait_for_pod(inc, first="the sensor restart didn't fix it; ")
            else:
                ok, action, detail = False, "restart_sensor", ("restarted the sensor, but the check at home still "
                                                               f"fails: {self._still(r)}")
        else:
            r = self.host("repair", action="verify")
            if r.get("verified"):
                ok, action, detail = True, "verify", "a checked retry at home passed"
            else:
                handed = self._handed_off(self.host("status"), iid)
                ok, action, detail = False, "verify", ((handed or {}).get("handoff_reason")
                                                       or f"still failing after a checked retry at home: {self._still(r)}")
        if self.worker:
            detail = self.worker.closed(self, inc, step, ok, action, detail)
        secs = round(time.time() - t0, 1)
        verdict = "healed" if ok else "couldn't heal"
        self._ev("heal", f"{verdict} {kind}: {detail} ({secs:.0f} s)", step=step, ok=ok, action=action,
                 verified=ok, seconds=secs, incident={"id": iid, "kind": kind, "summary": summary})
        if not ok:
            return False, f"{kind} during {step} ({summary}): {detail}; it needs a person"
        self.healed.append({"kind": kind, "summary": summary, "step": step, "action": action, "detail": detail,
                            "seconds": secs, "pod": list(self.pod_seen) if action.startswith("wait_for_pod") else []})
        return True, None

    @staticmethod
    def _handed_off(st, iid):
        return next((i for i in st.get("handed_off") or [] if i.get("id") == iid), None)

    @staticmethod
    def _still(r):
        return "; ".join(r.get("still") or r.get("pod_critical") or []) or r.get("error") or f"open {r.get('open')}"

    def _pod_side(self, inc, st):
        """Is the sensor pod behind this incident? A pod_* kind, podd incidents tied to it, or a critical
        podd incident open right now."""
        if inc.get("kind", "").startswith("pod_") or inc.get("pod_incidents"):
            return True
        full = next((i for i in self.host("incidents").get("incidents") or [] if i.get("id") == inc.get("id")), {})
        if full.get("pod_incidents"):
            return True
        return any(i.get("severity") == "critical" for i in (st.get("pod") or {}).get("incidents") or [])

    def _hold_for_pod(self, inc):
        """Give podd up to hold_s to flag the pod before restarting the sensor."""
        t0 = time.time()
        while time.time() - t0 < self.hold_s and not self.stop.is_set():
            self.sleep(self.poll_s)
            if self._pod_side(inc, self.host("status")):
                return True
        return False

    def _wait_for_pod(self, inc, first=""):
        """The pod's own healer owns this fix (a rollback; in sim, PodHealSim), and the host checks at home
        on its own once podd is clear. Wait for that; if podd is clear and the arm's incident lingers,
        run the check ourselves. What podd flagged meanwhile goes on the card (self.pod_seen)."""
        iid, t0, clear_since = inc.get("id"), time.time(), None
        self.pod_seen = {}
        while time.time() - t0 < self.pod_wait_s:
            if self.stop.is_set():
                return False, "wait_for_pod", f"{first}the dispatcher was stopped while waiting for the sensor pod"
            st = self.host("status")
            if st.get("unreachable"):
                return False, "wait_for_pod", f"{first}the arm host stopped answering: {st['error']}"
            crit = [i for i in (st.get("pod") or {}).get("incidents") or [] if i.get("severity") == "critical"]
            for i in crit:
                self.pod_seen.setdefault(i.get("kind"), i.get("summary"))
            if not any(i.get("id") == iid for i in st.get("incidents") or []):
                return True, "wait_for_pod", (f"{first}{self._podd_said()}the sensor pod's own healer fixed it and "
                                              "the host's check at home passed")
            if self._handed_off(st, iid):
                return False, "wait_for_pod", f"{first}{self._podd_said()}the host handed it to a person"
            if crit:
                clear_since = None
            else:
                clear_since = clear_since or time.time()
                if time.time() - clear_since >= self.pod_clear_s:
                    break
            self.sleep(self.poll_s)
        r = self.host("repair", action="verify")
        if r.get("verified"):
            return True, "wait_for_pod+verify", (f"{first}{self._podd_said()}the sensor pod's incidents cleared "
                                                 "and a check at home passed")
        return False, "wait_for_pod+verify", (f"{first}{self._podd_said()}the sensor pod's fault didn't clear in "
                                              f"{time.time() - t0:.0f} s ({self._still(r)})")

    def _podd_said(self):
        if not self.pod_seen:
            return ""
        return "podd flagged " + "; ".join(f"{k} ({short(v, 110)})" for k, v in self.pod_seen.items()) + "; "

    # ---- closing the card ----------------------------------------------------------------------
    def _finish(self, job, t0):
        cid, title, secs = self.ctx["card"], JOBS[job["job"]]["title"], time.time() - t0
        if self.healed:
            n = len(self.healed)
            fixed = "; ".join(f"{h['kind']} during {h['step']} (\"{h['summary']}\"): {h['detail']} "
                              f"({h['seconds']:.0f} s), then {h['step']} was retried" for h in self.healed)
            healed = f" Healed {n} fault{'s' if n > 1 else ''}: {fixed}."
            note = f"Done: {title}; healed {', '.join(self._named(h) for h in self.healed)} on the way."
        else:
            healed, note = " No faults.", f"Done: {title}, no faults."
        result = (f"RESULT: {title}, done by the scripted commander in {secs:.0f} s "
                  f"({', '.join(job['steps'])}).{healed}")
        try:
            self.board.comment(cid, result)
        except BoardError as exc:
            self._ev("error", f"couldn't comment RESULT on {cid}: {exc}")
        try:
            self.board.done(cid, note=note)
        except BoardError as exc:
            self._ev("job_result", f"job finished but the card didn't close: {exc}", ok=False, seconds=round(secs, 1))
            return {"ok": False, "closed": False, "reason": f"board.done failed: {exc}", "healed": self.healed}
        self._ev("job_result", f"job done in {secs:.0f} s" + (f", healed {', '.join(h['kind'] for h in self.healed)}"
                                                             if self.healed else ", no faults"),
                 ok=True, seconds=round(secs, 1), healed=[h["kind"] for h in self.healed])
        return {"ok": True, "closed": True, "reason": "done", "healed": self.healed}

    @staticmethod
    def _named(h):
        return h["kind"] + (f" (the sensor pod's {', '.join(h['pod'])})" if h.get("pod") else "")

    def _blocked(self, why, t0):
        cid, secs = self.ctx["card"], time.time() - t0
        try:
            self.board.comment(cid, f"BLOCKED: {why}")
            self.board.move(cid, status="blocked")
        except BoardError as exc:
            self._ev("error", f"couldn't mark {cid} blocked: {exc}")
        self._ev("job_result", f"job blocked after {secs:.0f} s: {short(why, 200)}", ok=False,
                 seconds=round(secs, 1), reason=why, healed=[h["kind"] for h in self.healed])
        return {"ok": False, "closed": False, "reason": why, "healed": self.healed}


class HermesExecutor:
    """Hermes does the job: the exact one-shot call that closed a real card Saturday night (stories.py),
    with a prompt that names this board and this card. Hermes reads and closes the card itself, through
    its naive_board MCP, so it only works with the real board; the dispatcher checks the card after."""

    name = "hermes"

    def __init__(self, board, *, emit=None, log_dir=COMMANDER_LOGS, argv=None):
        self.board, self.emit, self.log_dir = board, emit or events.emit, Path(log_dir)
        self.argv = list(argv or HERMES)
        self.stop = threading.Event()

    def run(self, card, timeout=600):
        job = parse_card(card)
        ctx = {"card": card["id"], "order": job["order"], "job": job["job"], "exec": self.name}
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log = self.log_dir / f"{card['id']}.log"
        argv = [*self.argv, PROMPT.format(board=self.board.id, card=card["id"])]
        self.emit("commander", "job_started", f"Hermes takes card {card['id']} (its output: {log})", **ctx, log=str(log))
        t0 = time.time()
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    text=True, errors="replace", bufsize=1, start_new_session=True)
        except FileNotFoundError:
            reason = f"`{self.argv[0]}` is not installed on this machine"
            self.emit("commander", "job_result", f"Hermes didn't start: {reason}", **ctx, ok=False)
            return {"ok": False, "closed": None, "reason": reason}

        def tee():
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"# {local_time()} {' '.join(argv[:-1])} <prompt for {card['id']}>\n")
                for line in proc.stdout:
                    f.write(line)
                    f.flush()
                    text = ANSI.sub("", line).strip()
                    if text:
                        self.emit("commander", "commander_line", short(text), **ctx)

        reader = threading.Thread(target=tee, name="hermes-tee", daemon=True)
        reader.start()
        rc = reason = None
        try:
            while rc is None:
                try:
                    rc = proc.wait(timeout=0.5)
                    reason = f"Hermes exited with code {rc} after {time.time() - t0:.0f} s"
                except subprocess.TimeoutExpired:
                    if time.time() - t0 > timeout:
                        reason = f"Hermes ran past the job timeout ({timeout:.0f} s) and was stopped"
                    elif self.stop.is_set():
                        reason = "the dispatcher was stopped in the middle of the job, so Hermes was stopped"
                    else:
                        continue
                    self._kill(proc)
                    break
        finally:
            if proc.poll() is None:                 # Ctrl-C or an error while waiting: don't leave it running
                self._kill(proc)
        reader.join(5)
        self.emit("commander", "job_result", reason, **ctx, ok=rc == 0, exit=rc, seconds=round(time.time() - t0, 1))
        return {"ok": rc == 0, "closed": None, "reason": reason, "exit": rc}

    @staticmethod
    def _kill(proc):
        """Hermes starts MCP servers of its own: stop its whole process group."""
        for sig, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                return
            try:
                proc.wait(wait)
                return
            except subprocess.TimeoutExpired:
                continue


class Dispatcher:
    """Claims ARM JOB cards one at a time and hands each to the executor (scripted or hermes)."""

    def __init__(self, board, executor, *, call=None, emit=None, every=5.0, job_timeout=600.0, sleep=None, stop=None):
        self.board, self.exec, self.host = board, executor, Host(call)
        self.emit = emit or events.emit
        self.stop = stop or threading.Event()        # set by SIGTERM / Ctrl-C: finish the step, block the card, exit
        executor.stop = self.stop
        self.every, self.job_timeout, self.sleep = every, job_timeout, sleep or self.stop.wait
        self.jobs = []                    # one summary per job worked
        self.waiting = {}                 # what we already said we're waiting on: host, person, board
        self.skip = {}                    # card id -> don't try to claim it again before this time

    def _ev(self, kind, text, **fields):
        return self.emit("dispatch", kind, text, **fields)

    def _say_once(self, key, value, kind, text, **fields):
        if self.waiting.get(key) != value:
            self.waiting[key] = value
            self._ev(kind, text, **fields)

    def _cleared(self, key, text):
        if self.waiting.pop(key, None) is not None:
            self._ev("dispatcher", text)

    # ---- startup -------------------------------------------------------------------------------
    def recover(self):
        """An ARM JOB card in doing at startup was being worked by a dispatcher that stopped mid-job
        (only one dispatcher holds the board). Re-running half a physical job is unsafe: block it."""
        for c in self.board.show("doing"):
            if not is_arm_job(c):
                continue
            why = ("dispatcher: this card was in doing when the dispatcher started, so a dispatcher stopped in the "
                   "middle of its job. Re-running half a physical job is unsafe: check the arm, then move the card "
                   "back to todo to run it again.")
            try:
                self.board.comment(c["id"], f"BLOCKED: {why}")
                self.board.move(c["id"], status="blocked")
                self._ev("card_blocked", f"card {c['id']} was left in doing by a stopped dispatcher: moved to blocked",
                         card=c["id"], order=parse_card(c)["order"], reason="stale")
            except BoardError as exc:
                self._ev("error", f"couldn't block the stale card {c['id']}: {exc}", card=c["id"])

    # ---- the loop ------------------------------------------------------------------------------
    def run(self, *, once=False, max_jobs=None):
        self._ev("dispatcher", f"dispatcher up (pid {os.getpid()}) on the {self.board.kind} board {self.board.id} "
                               f"with the {self.exec.name} commander; polling every {self.every:g} s",
                 board=self.board.id, exec=self.exec.name)
        try:
            self.recover()
        except Exception as exc:  # a board error must not stop the dispatcher
            self._ev("error", f"couldn't check for stale cards: {type(exc).__name__}: {exc}")
        worked = 0
        try:
            while not self.stop.is_set():
                try:
                    state = self.poll()
                except Exception as exc:  # never let one bad poll crash the loop
                    self._ev("error", f"dispatcher error: {type(exc).__name__}: {exc}")
                    state = "error"
                worked += state == "job"
                if once or (max_jobs and worked >= max_jobs):
                    break
                if state != "job":
                    self.sleep(self.every)
        except KeyboardInterrupt:
            pass
        finally:
            self._ev("dispatcher", f"dispatcher stopped after {worked} job{'' if worked == 1 else 's'}")
        return self.jobs

    def poll(self):
        """One pass: "job" (worked a card), "idle", "waiting" (host or person) or "error"."""
        try:
            todo = [c for c in self.board.show("todo") if is_arm_job(c) and card_status(c) in (None, "todo")]
        except Exception as exc:
            self._say_once("board", str(exc)[:80], "error", f"can't read the board: {type(exc).__name__}: {exc}")
            return "error"
        self._cleared("board", "the board answers again")
        now = time.time()
        todo = sorted((c for c in todo if self.skip.get(c["id"], 0) <= now), key=created)
        if not todo:
            return "idle"
        if not self.arm_ready():
            return "waiting"
        return "job" if self.work(todo[0]) else "error"

    def arm_ready(self):
        st = self.host("status")
        if not st.get("ok"):
            self._say_once("host", "down", "error", f"can't start a job: {st.get('error')}; "
                                                    f"trying again every {self.every:g} s")
            return False
        self._cleared("host", "the arm host answers again")
        handed = st.get("handed_off") or []
        if handed:
            ids = ",".join(sorted(i.get("id", "?") for i in handed))
            what = "; ".join(f"{i.get('kind')}: {i.get('summary')}" for i in handed)
            self._say_once("person", ids, "waiting", f"waiting for a person: {what} was handed off "
                                                     f"({handed[0].get('handoff_reason')}); no job starts until it's fixed",
                           incidents=handed)
            return False
        self._cleared("person", "the handed-off incident is closed: jobs resume")
        return True

    def work(self, card):
        """Claim, run, check. Returns the outcome ("done" or "blocked"), or None if the claim failed."""
        cid = card["id"]
        try:
            self.board.move(cid, status="doing")
        except BoardError as exc:
            self.skip[cid] = time.time() + CLAIM_RETRY_S
            self._ev("error", f"couldn't claim card {cid}: {exc}; skipping it for {CLAIM_RETRY_S} s", card=cid)
            return None
        t0 = time.time()
        try:
            full = {**card, **(self.board.card(cid) or {})}       # the listing may leave the body out
        except BoardError:
            full = card
        job = parse_card(full)
        ctx = {"card": cid, "order": job["order"], "job": job["job"], "exec": self.exec.name}
        try:
            self.board.comment(cid, f"CLAIMED: the dispatcher started the {self.exec.name} commander on this card "
                                    f"at {local_time()}")
        except BoardError as exc:
            self._ev("error", f"couldn't comment CLAIMED on {cid}: {exc}", **ctx)
        self._ev("card_claimed", f"claimed card {cid} ({full.get('title')}) for the {self.exec.name} commander", **ctx)
        try:
            out = self.exec.run(full, timeout=self.job_timeout)
        except KeyboardInterrupt:
            self._settle(cid, {"reason": "the dispatcher was stopped by hand in the middle of the job"}, ctx, t0)
            raise
        except Exception as exc:  # a commander bug must not leave the card in doing
            out = {"ok": False, "reason": f"the {self.exec.name} commander crashed: {type(exc).__name__}: {exc}"}
            self._ev("error", out["reason"], **ctx)
        outcome = self._settle(cid, out, ctx, t0)
        secs = time.time() - t0
        healed = [h["kind"] for h in out.get("healed") or []]
        summary = {"card": cid, "order": job["order"], "job": job["job"], "outcome": outcome,
                   "seconds": round(secs, 1), "healed": healed, "reason": out.get("reason")}
        self.jobs.append(summary)
        self._ev("job_summary", f"card {cid} ({job['job']}, {job['order'] or 'no order id'}): {outcome} in {secs:.0f} s "
                                f"by the {self.exec.name} commander"
                                + (f", healed {', '.join(healed)}" if healed else "")
                                + ("" if outcome == "done" else f" ({short(out.get('reason'), 120)})"),
                 **ctx, ok=outcome == "done", outcome=outcome, ms=round(secs * 1000), healed=healed)
        return outcome

    def _settle(self, cid, out, ctx, t0):
        """Read the card back. Done: card_done. Anything else ends in blocked, with a BLOCKED: comment
        from this run (the commander's, or the dispatcher's own)."""
        secs = time.time() - t0
        try:
            card = self.board.card(cid)
            mine = this_run(self.board.comments(cid))
        except BoardError as exc:
            self._ev("error", f"couldn't read card {cid} back after the job: {exc}", **ctx)
            return "unknown"
        if card_status(card) == "done":
            self._ev("card_done", f"card {cid} is done ({secs:.0f} s from claim to done): {card.get('note')}",
                     **ctx, ms=round(secs * 1000), seconds=round(secs, 1), note=card.get("note"))
            return "done"
        said = next((t for t in mine if t.startswith("BLOCKED")), None)
        try:
            if not said:
                said = (f"BLOCKED: dispatcher: the {self.exec.name} commander ended without closing the card "
                        f"({out.get('reason') or 'no reason given'})")
                self.board.comment(cid, said)
            if card_status(card) != "blocked":
                self.board.move(cid, status="blocked")
        except BoardError as exc:
            self._ev("error", f"couldn't block card {cid}: {exc}", **ctx)
        self._ev("card_blocked", f"card {cid} is blocked: {short(said, 200)}", **ctx, reason=said,
                 ms=round(secs * 1000))
        return "blocked"


def lock_path(board):
    """Next to the file board (not its own per-call `board.lock`), or under runs/ for the Naïve board."""
    if getattr(board, "kind", None) == "file":
        return board.path.with_suffix(".dispatch.lock")
    return RUNS / f"dispatch-{board.id}.lock"


def take_lock(path):
    """flock the dispatcher's lock file and write our pid in it. Keep the returned file open for as long
    as the dispatcher runs; a second dispatcher on the same board gets SystemExit."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.seek(0)
        holder = f.read().strip() or "pid unknown"
        f.close()
        raise SystemExit(f"another dispatcher already works this board ({holder}; lock {path}). "
                         "There is one arm: stop that one first.")
    f.seek(0)
    f.truncate()
    f.write(f"pid {os.getpid()} since {local_time()}\n")
    f.flush()
    return f


def main(argv=None):
    ap = argparse.ArgumentParser(description="Claim ARM JOB cards and have a commander do them, one at a time.")
    ap.add_argument("--board", choices=["file", "naive"], default=None,
                    help="file (runs/board.json, or $ARMSIM_FILE_BOARD) or naive; default $ARMSIM_BOARD_KIND, else file")
    ap.add_argument("--board-id", default=None, help="the board's id (naive default: $ARMSIM_BOARD or the team board)")
    ap.add_argument("--exec", choices=["scripted", "hermes"], default="scripted",
                    help="scripted: no LLM, the card's steps with the healing pattern; hermes: Hermes does the job")
    ap.add_argument("--every", type=float, default=5.0, help="seconds between polls while idle (default 5)")
    ap.add_argument("--once", action="store_true", help="one pass: work the oldest waiting card if there is one, then exit")
    ap.add_argument("--max-jobs", type=int, default=None, help="exit after this many jobs")
    ap.add_argument("--job-timeout", type=float, default=600.0, help="seconds a job may take (default 600)")
    ap.add_argument("--manager", choices=["off", "fake", "hermes"], default="off",
                    help="heal through the worker/manager loop first (armsim/worker.py); off = today's scripted heal")
    ap.add_argument("--manager-url", default=os.environ.get("ARMSIM_MANAGER_URL", "http://127.0.0.1:8642"),
                    help="the manager's Hermes API server, with /p/manager when the gateway multiplexes profiles")
    ap.add_argument("--manager-deadline", type=float, default=90.0, help="seconds the manager gets (3-120)")
    ap.add_argument("--fake-mode", default="good", choices=["good", "bad_json", "off_list", "unsafe", "slow", "429",
                                                           "500", "failed", "refused", "redo_offset"],
                    help="--manager fake only")
    ap.add_argument("--worker-lm", choices=["off", "ollama"], default="off", help="the worker's model writes its answer")
    ap.add_argument("--ollama", default=os.environ.get("ARMSIM_OLLAMA", "http://127.0.0.1:11434/v1"),
                    help="the worker model's OpenAI-compatible endpoint")
    ap.add_argument("--worker-model", default="qwen3.5:4b")
    ap.add_argument("--exec-via", choices=["code", "lm"], default="code",
                    help="lm: the worker's model issues the checked action, gated to that exact call (needs --worker-lm)")
    ap.add_argument("--wrong-action", choices=["off", "redo"], default="off",
                    help="redo: a missed grasp is reversed and redone with the manager's bounded correction "
                         "(armsim/redo.py; needs --manager); off = the card is blocked, as before")
    a = ap.parse_args(argv)
    if a.wrong_action == "redo" and a.manager == "off":
        raise SystemExit("--wrong-action redo needs --manager fake|hermes: the manager corrects the command")
    if a.exec_via == "lm" and a.worker_lm == "off":
        raise SystemExit("--exec-via lm needs --worker-lm ollama: the worker's model issues the call")
    if a.manager != "off" and a.exec != "scripted":
        raise SystemExit("--manager works with --exec scripted only: the Hermes commander heals with its own rules")
    kind = a.board or os.environ.get("ARMSIM_BOARD_KIND", "file")
    if a.exec == "hermes" and kind == "file":
        raise SystemExit("--exec hermes needs --board naive: Hermes reads and closes cards only through its "
                         "naive_board MCP, so it can't see the file board. Use --exec scripted with the file board.")
    board = open_board(kind, a.board_id)
    lock = take_lock(lock_path(board))

    # The first SIGTERM or Ctrl-C stops gently: the current skill finishes, the card is blocked, the
    # dispatcher exits. Hanging up on the arm host in the middle of a skill can stop the host itself
    # (its reply write raises BrokenPipeError). A second signal stops at once.
    stop = threading.Event()

    def on_signal(signum, frame):
        if stop.is_set():
            raise KeyboardInterrupt
        stop.set()
        print("[dispatch] stopping after the current step (signal again to stop now)", flush=True)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    if a.exec == "hermes":
        executor = HermesExecutor(board)
    else:
        from .redo import make as make_redo
        from .worker import make as make_worker
        worker = make_worker(a)
        executor = ScriptedExecutor(board, worker=worker, redo=make_redo(a, worker))
    try:
        Dispatcher(board, executor, every=a.every, job_timeout=a.job_timeout, stop=stop).run(
            once=a.once, max_jobs=a.max_jobs)
    finally:
        lock.close()


if __name__ == "__main__":
    main()
