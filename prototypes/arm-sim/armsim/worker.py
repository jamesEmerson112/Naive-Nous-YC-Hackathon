"""The worker: plain code next to the arm host, plus an optional small model, between an incident and its
fix (docs/worker-manager-healer.md §1). The scripted commander calls it from its heal step
(armsim/dispatch.py, `--manager fake|hermes`); with `--manager off` (the default) nothing here runs.

    python -m armsim.dispatch --board file --exec scripted --manager fake            # the fake manager, in sim
    python -m armsim.dispatch --board file --manager hermes --manager-url http://<mac>:8642/p/manager
                              [--worker-lm ollama --ollama http://192.168.50.1:11434/v1]
    python -m armsim.worker packet          # print the packet for the host's open incident (any open one)

One incident, in order (the arm is already parked: the watchdog parked it when the incident opened):
1. Packet: `build_escalation()` reads the host's status, incidents, read and window ops and renders the
   last 6 s as id'd lines (the "lite" profile: D rows, one joint line, podd's state, INC lines). For the
   us_offset / us_scale / us_residual kinds it first gives podd hold_s to explain them, as the scripted
   heal does, so a bad pod image escalates with the pod's allowlist.
2. Worker model (`--worker-lm ollama`): `worker_answer()` asks the laptop's Ollama for the six-key answer
   (OpenAI-compatible /chat/completions, a JSON-schema response_format, 30 s cap). None -> `worker: null`.
3. Ask: `ManagerClient.ask()` (armsim/manager.py), inside the packet's deadline (90 s).
4. Check: `escalation.check_plan()`. A refused plan falls back to the scripted heal, except a plan that
   touches safety: the arm stays parked and the incident goes to a person.
5. Act: plain code issues the one checked action (`execute()`; `--exec-via lm` lets the model issue
   it, gated to that exact call, step 8). verify() on the host decides whether it worked.
Fallback: no plan, a late plan or a refused plan -> today's scripted heal (dispatch.py ScriptedExecutor).
A checked plan that ran and failed its check is final for this heal (the card is blocked and the arm
stays parked), except a sensor restart that failed because podd now flags the pod: then the scripted
heal waits for the pod's own healer, as it would have.

Events (armsim/events.py, through the executor's emit, with the job's card/order/job fields):
  src worker:  escalation (the packet went out), plan_check (the check's verdict), plan_action (what ran
               and whether verify() passed)
  src manager: fix_plan (the manager's answer, or why there was none)
Board: one `INC <id>: <kind> on <device>` card per escalation with ESCALATION:, PLAN: and RESULT: or
BLOCKED: comments, and a PLAN: line on the job card. Packets are saved under runs/escalations/, so one
can be replayed by hand against the real manager (hermes-manager/README.md).
"""
import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import events
from .board import BoardError
from .escalation import (ESCALATION_SCHEMA, MAYBE_POD_KINDS, SAFETY, allowed_actions, check_plan, parse_plan,
                         validate)

ROOT = Path(__file__).resolve().parents[1]
PACKETS = Path(os.environ.get("ARMSIM_PACKETS") or ROOT / "runs" / "escalations")
WINDOW_S = 6.0
WORKER_CAP_S = 30.0         # the worker model's budget (p90 turn 10.2 s, firmware/harness/RESULTS.md:67-68)
EXEC_VIA_LM_S = 15.0        # step 8: the model gets this long to issue the call, then plain code does

# The worker's answer: exactly six keys (docs/research/worker-benchmark-data.md §1.3), kinds from its list.
KINDS = ("none", "us_frozen", "us_offset", "us_scale", "us_noisy", "us_no_echo", "us_intermittent", "us_out_of_range",
         "us_mismatch", "us_blocked", "joint_stuck", "joint_offset", "joint_reversed", "clock_skew", "link_garbled",
         "link_silent", "link_down", "crash_loop", "watchdog_loop", "acc_frozen", "acc_gravity", "acc_error",
         "tx_overrun", "pod_health", "grasp_missed", "other")
ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["status", "kind", "severity", "evidence", "escalate", "message"],
    "properties": {"status": {"enum": ["healthy", "fault"]}, "kind": {"enum": list(KINDS)},
                   "severity": {"enum": ["info", "warn", "critical"]},
                   "evidence": {"type": "array", "maxItems": 8,
                                "items": {"type": "string", "pattern": "^[A-Z]+[0-9]+(-[A-Z]+[0-9]+)?$"}},
                   "escalate": {"type": "boolean"}, "message": {"type": "string", "maxLength": 300}}}
# The frozen system prompt (worker-benchmark-data.md §1.4), for the lite render: it carries the healthy
# facts, because a frozen trace is only caught next to a healthy reference (docs/healer-data-levels.md:44-53).
SYSTEM = (
    "You watch the logs of an SO-101 arm and its STM32 sensor pod. You never move the arm or fix anything; you "
    "report to the manager. D = wrist ultrasonic at 10 Hz: mm, echo time us, st ok|out_of_range|no_echo, pred = "
    "the distance the pose predicts (only when kinematics=on); healthy firmware has mm=(us*343+1000)/2000. JS = the "
    "joints now: shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper (deg; gripper 0-100, "
    "0=closed) and servo status bits (32=overload). L = the pod daemon and the pod's image. INC = an alarm a rule "
    "already raised (src watchdog = the arm's level-1 rules, podd = the pod's). +s = seconds since the window "
    "start. Healthy facts: echo time jitters about 0.4 us, so us never stays identical for 3 s, but mm can stay "
    "constant for a long time; at home the jaw is in the beam, so out_of_range is normal there; a held block is in "
    "the beam; one odd reading as the beam crosses an edge is normal. Kinds: " + ", ".join(KINDS) + ". Reply with "
    "one JSON object only, with exactly the keys status, kind, severity, evidence, escalate, message. Healthy -> "
    "kind none, severity info, evidence [], escalate false, message \"\". Fault -> cite at most 8 line ids (D07 "
    "or a range D07-D19); escalate unless severity is info; the message is one sentence of at most 40 words that "
    "names the part, the symptom and one number from the window. Never tell anyone to move a joint to an angle.")

DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # LAN only, never via a proxy (inferred)


def iso(ts):
    """A host time as ISO: wall-clock seconds (pod mode) as they are; sim seconds can't be dated, so now."""
    ts = ts if isinstance(ts, (int, float)) and ts > 1e9 else time.time()
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def short(text, n=160):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


# ---- 1. the packet ----------------------------------------------------------------------------------
def _num(v, fmt="{:g}"):
    return "-" if v is None else fmt.format(v)


def render_lite(window, *, status=None, joints=None, incidents=(), max_chars=11000):
    """The last seconds as id'd lines (the lite profile, worker-benchmark-data.md §1.2). (text, idmap)."""
    status = status or {}
    rows, seconds, until = window.get("rows") or [], float(window.get("seconds") or WINDOW_S), window.get("until")
    start = (until - seconds) if isinstance(until, (int, float)) else (rows[0]["ts"] if rows else 0.0)
    kin = bool(window.get("kinematics", True))
    holding = (status.get("grasp") or {}).get("holding")
    head = (f"WINDOW src=arm-{window.get('arm', 'sim')} span={seconds:.1f}s kinematics={'on' if kin else 'off'} "
            f"holding={'yes' if holding else 'no'}")
    idmap, d_lines = {}, []
    for i, r in enumerate(rows, 1):
        rid = f"D{i:02d}"
        idmap[rid] = r
        line = f"{rid} +{r['ts'] - start:.1f} mm={_num(r.get('mm'))} us={_num(r.get('us'))} st={r.get('st')}"
        if kin and r.get("pred_mm") is not None:
            line += f" pred={r['pred_mm']:.1f}"
        d_lines.append(line)
    tail = []
    if joints:
        pos = ",".join(f"{v:.1f}" for v in joints.values())
        bits = ",".join(str(b) for b in status.get("status") or [])
        tail.append(f"JS pos={pos} st={bits or '-'} gripper_load={_num((status.get('grasp') or {}).get('load_nm'))}")
        idmap["JS"] = joints
    pod = status.get("pod")
    if pod is not None:
        tail.append(f"L podd={'up' if pod.get('podd_up') else 'down'} image={pod.get('image') or '-'}")
    for n, f in enumerate(incidents, 1):
        idmap[f"INC{n}"] = f
        tail.append(f"INC{n} src={f['src']} kind={f['kind']} severity={f['severity']} summary=\"{f['summary']}\"")
    while d_lines and len("\n".join([head, *d_lines, *tail])) > max_chars:
        d_lines.pop(0)                                    # keep the newest rows
    return "\n".join([head, *d_lines, *tail]), idmap


def _sev(s):
    return s if s in ("info", "warn", "critical") else "warn"


def findings_for(full, status):
    """The level-1 finding behind the incident, then podd's open incidents (at most 8)."""
    kind = full.get("kind") or "other"
    out = [{"src": full.get("src") or ("podd" if kind.startswith("pod_") else "watchdog"), "kind": kind[:40],
            "severity": _sev(full.get("severity") or "critical"), "summary": short(full.get("summary") or kind, 300)}]
    for p in ((status or {}).get("pod") or {}).get("incidents") or []:
        out.append({"src": "podd", "kind": str(p.get("kind") or "pod")[:40], "severity": _sev(p.get("severity")),
                    "summary": short(p.get("summary") or p.get("kind"), 300)})
    return out[:8]


def build_escalation(inc, host, *, esc_id, job=None, pod_side=False, tried=(), device_id=None,
                     deadline_s=90.0, category="fault", status=None, seconds=WINDOW_S):
    """The arm-escalation/v1 packet for one host incident, with worker: null (the model's answer goes in
    after, see Worker.heal). (packet, meta) where meta = {text, idmap, full incident, status}."""
    st = status or host("status")
    full = next((i for i in host("incidents").get("incidents") or [] if i.get("id") == inc.get("id")), None) or dict(inc)
    opened = full.get("opened_at")
    win = host("window", seconds=seconds, until=(opened + 0.5) if isinstance(opened, (int, float)) else None)
    if not win.get("ok"):                                 # an older host: one row from the distance op
        row = (host("distance").get("row") or {})
        win = {"rows": [{"ts": 0.0, **row}] if row else [], "seconds": seconds, "until": 0.0, "arm": "sim",
               "kinematics": row.get("pred_mm") is not None}
    joints = host("read").get("pos")
    found = findings_for(full, st)
    text, idmap = render_lite(win, status=st, joints=joints, incidents=found)
    device = device_id or (st.get("romulus") or {}).get("device") or (
        "arm-1" if win.get("arm") == "real" else "arm-twin-1")
    esc = {"schema": "arm-escalation/v1", "incident_id": esc_id, "device_id": device, "category": category,
           "opened_at": iso(opened), "findings": found, "worker": None, "window": text,
           "state": {"mode": "real" if win.get("arm") == "real" else "sim", "kinematics": bool(win.get("kinematics")),
                     "parked": True, "pod_image": (st.get("pod") or {}).get("image"), "job": job,
                     "tried": list(tried)},
           "allowed_actions": allowed_actions(full, {"category": category, "pod_side": pod_side}),
           "deadline_s": float(deadline_s)}
    return esc, {"text": text, "idmap": idmap, "incident": full, "status": st}


# ---- 2. the worker's model --------------------------------------------------------------------------
def _expand_ok(ref, idmap):
    return all(part in idmap for part in ref.split("-"))


def worker_answer(text, *, endpoint, model, timeout=WORKER_CAP_S, idmap=None, reasoning_effort=None):
    """(six-key answer, why) from an OpenAI-compatible endpoint (Ollama: http://host:11434/v1), or
    (None, why) on a timeout, an HTTP error or a malformed answer. The cap is wall-clock: the call runs
    in a thread, so a server that trickles bytes can't hold the arm's escalation past it."""
    body = {"model": model, "temperature": 0, "max_tokens": 512, "stream": False,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "worker_answer", "strict": True, "schema": ANSWER_SCHEMA}}}
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort      # Ollama accepts it (worker-benchmark-data.md §4)
    req = urllib.request.Request(endpoint.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer ollama"})
    got = {}

    def call():
        try:
            with DIRECT.open(req, timeout=timeout + 2) as resp:     # the join below is the cap
                got["data"] = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            got["error"] = f"HTTP {exc.code} from {endpoint}"
        except (OSError, ValueError) as exc:
            got["error"] = f"{type(exc).__name__}: {exc}"

    t0 = time.time()
    th = threading.Thread(target=call, name="worker-lm", daemon=True)
    th.start()
    th.join(timeout)
    secs = time.time() - t0
    if th.is_alive():
        return None, f"the worker model ran past its {timeout:.0f} s cap"
    if "error" in got:
        return None, f"the worker model didn't answer ({got['error']})"
    try:
        content = got["data"]["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return None, "the worker model's reply has no message content"
    obj, why = parse_plan(content)                        # one JSON object, one fence tolerated
    if obj is None:
        return None, f"malformed answer: {why}"
    errs = validate(obj, ANSWER_SCHEMA)
    if errs:
        return None, "malformed answer: " + "; ".join(errs[:3])
    bad = [e for e in obj["evidence"] if idmap is not None and not _expand_ok(e, idmap)]
    if bad:
        return None, f"malformed answer: evidence ids not in the window: {', '.join(bad)}"
    return obj, f"answered in {secs:.1f} s"


# ---- 5, step 8: the worker's model issues the checked call (--exec-via lm) ----------------------------
EXEC_SYSTEM = ("You carry out an arm fix plan that plain code has already checked. Call exactly one tool: the "
               "plan's action, with the plan's args and nothing else. Do not explain.")


def lm_issue(plan, allowed, lm, *, timeout=EXEC_VIA_LM_S):
    """Ask the worker's model to issue the plan as a tool call; the allowed actions are its only tools.
    (call, why): call = {"action", "args"} as the model issued it, or None (no call, an HTTP error, or no
    call within the cap). The gate (gate_call) decides whether it may run."""
    tools = [{"type": "function", "function": {
        "name": a, "description": f"arm healer action {a}",
        "parameters": {"type": "object", "additionalProperties": False,
                       "properties": {"from_step": {"enum": ["reach", "approach", "grip", "lift", "release"]}}
                       if a == "redo_step" else {}}}} for a in allowed]
    body = {"model": lm["model"], "temperature": 0, "max_tokens": 256, "stream": False, "tools": tools,
            "messages": [{"role": "system", "content": EXEC_SYSTEM},
                         {"role": "user", "content": json.dumps({k: plan[k] for k in ("action", "args", "why")})}]}
    req = urllib.request.Request(lm["endpoint"].rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer ollama"})
    got = {}

    def call():
        try:
            with DIRECT.open(req, timeout=timeout + 2) as resp:     # the join below is the cap
                got["data"] = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            got["error"] = f"HTTP {exc.code}"
        except (OSError, ValueError) as exc:
            got["error"] = f"{type(exc).__name__}: {exc}"

    th = threading.Thread(target=call, name="worker-lm-exec", daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        return None, f"no call from the worker's model within {timeout:.0f} s"
    if "error" in got:
        return None, f"the worker's model didn't answer ({got['error']})"
    try:
        calls = got["data"]["choices"][0]["message"].get("tool_calls") or []
    except (KeyError, IndexError, TypeError, AttributeError):
        calls = []
    if not calls:
        return None, "the worker's model made no tool call"
    fn = calls[0].get("function") or {}
    try:
        args = fn.get("arguments") or {}
        args = json.loads(args) if isinstance(args, str) else args
    except ValueError:
        return {"action": fn.get("name"), "args": None}, "the call's arguments are not JSON"
    return {"action": fn.get("name"), "args": args or {}}, f"{len(calls)} tool call(s)"


def gate_call(call, plan):
    """Only the exact checked call may run: the same action and the same args (the pod gate's rule,
    firmware/harness/agent.py:389-418). (allowed, reason)."""
    if not call:
        return False, "no call"
    want = (plan["action"], plan.get("args") or {})
    if (call.get("action"), call.get("args") or {}) == want:
        return True, "the call equals the checked plan"
    return False, f"the model called {call.get('action')}({json.dumps(call.get('args'))}), not the plan's {want[0]}({json.dumps(want[1])})"


# ---- 3-5. the loop ----------------------------------------------------------------------------------
class Worker:
    """The scripted commander's heal step, through the manager. heal() returns (ok, action, detail) when
    a plan was carried out (or the arm was handed to a person), or None: run the scripted heal."""

    def __init__(self, client, *, emit=None, lm=None, device_id=None, deadline_s=90.0, exec_via="code",
                 incident_cards=True, packet_dir=None):
        self.client, self.emit = client, emit or events.emit
        self.lm = lm                      # None, or {"endpoint", "model", "reasoning_effort"?}
        self.device_id, self.deadline_s, self.exec_via = device_id, deadline_s, exec_via
        self.exec_cap_s = EXEC_VIA_LM_S   # --exec-via lm: the model's time to issue the call
        self.incident_cards = incident_cards
        self.packet_dir = Path(packet_dir or PACKETS)
        self.token = uuid.uuid4().hex[:8]  # host incident ids restart at inc-1: make ours unique
        self.n = 0
        self.tried = {}                    # host incident id -> [{action, verified}] run by plans
        self.redos = {}                    # card -> redo_step plans run
        self.cur = None                    # the heal in progress: what closed() reports

    @property
    def label(self):
        return getattr(self.client, "label", "manager")

    def _ev(self, ex, src, kind, text, **fields):
        return self.emit(src, kind, text, **ex.ctx, **fields)

    def _board(self, ex, what, fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except BoardError as exc:
            self._ev(ex, "worker", "error", f"couldn't {what}: {exc}")
            return None

    def _tried(self, iid, full):
        """What already ran for this incident: the host's own repairs (an open incident means each failed
        its check), plus the plans this worker ran that the host doesn't record."""
        out = [{"action": a.get("tool", "").removeprefix("arm_"), "verified": False}
               for a in full.get("actions") or [] if a.get("tool")]
        out += [t for t in self.tried.get(iid, []) if t["action"] not in ("restart_sensor", "verify", "no_action")]
        return out

    # ---- the heal --------------------------------------------------------------------------------
    def heal(self, ex, inc, step):
        self.cur = {"iid": inc.get("id"), "source": "fallback", "note": None, "plan": None, "inc_card": None}
        try:
            return self._heal(ex, inc, step)
        except Exception as exc:  # a worker bug must not cost the heal: the arm is parked, the scripted heal runs
            self._ev(ex, "worker", "error", f"the worker failed ({type(exc).__name__}: {exc}); the scripted heal runs")
            self.cur.update(source="fallback", note=f"the worker failed ({type(exc).__name__})")
            return None

    def _heal(self, ex, inc, step):
        iid, kind = inc.get("id"), inc.get("kind") or "?"
        st = ex.host("status")
        pod_side = ex._pod_side(inc, st)
        if not pod_side and kind in MAYBE_POD_KINDS:
            pod_side = ex._hold_for_pod(inc)          # podd may be about to explain it (bad image 07)
            st = ex.host("status")
        if not any(i.get("id") == iid for i in st.get("incidents") or []):
            self.cur["note"] = "the incident closed before the worker asked the manager"
            return None
        self.n += 1
        esc_id = f"inc-{self.token}-{str(iid).removeprefix('inc-')}-{self.n}"
        job = {"card_id": ex.ctx.get("card") or "", "steps": list(getattr(ex, "job_steps", None) or []),
               "failed_step": step}
        full = next((i for i in ex.host("incidents").get("incidents") or [] if i.get("id") == iid), dict(inc))
        esc, meta = build_escalation(inc, ex.host, esc_id=esc_id, job=job, pod_side=pod_side,
                                     tried=self._tried(iid, full), device_id=self.device_id,
                                     deadline_s=self.deadline_s, status=st)
        lm_why = "off"
        if self.lm:
            esc["worker"], lm_why = worker_answer(meta["text"], endpoint=self.lm["endpoint"], model=self.lm["model"],
                                                  idmap=meta["idmap"], reasoning_effort=self.lm.get("reasoning_effort"))
        errs = validate(esc, ESCALATION_SCHEMA)
        if errs:
            self._ev(ex, "worker", "error", f"the escalation packet for {iid} is invalid: {'; '.join(errs[:3])}")
            self.cur["note"] = "the worker couldn't build a valid packet"
            return None
        path = self._save(esc)
        said = (f"; the worker's model says: {short(esc['worker']['message'], 120)}" if esc["worker"]
                else f"; worker model: {lm_why}")
        self._ev(ex, "worker", "escalation",
                 f"escalated {kind} during {step} to the manager ({self.label}) as {esc_id}: allowed "
                 f"{', '.join(esc['allowed_actions'])}{said}",
                 step=step, escalation=esc_id, incident={"id": iid, "kind": kind, "summary": inc.get("summary")},
                 allowed=esc["allowed_actions"], worker=esc["worker"], worker_lm=lm_why, packet=str(path),
                 window_lines=esc["window"].count("\n") + 1, packet_chars=len(json.dumps(esc)))
        self._incident_card(ex, esc, inc, step)
        t_ask = time.time()
        text, why = self.client.ask(esc)
        if text is None:
            self._ev(ex, "manager", "fix_plan", f"no plan from the manager ({self.label}): {why}; the scripted heal "
                                                "takes over", ok=False, escalation=esc_id, seconds=round(time.time() - t_ask, 1))
            return self._fallback(ex, f"no plan from the manager ({why})")
        obj, _ = parse_plan(text)
        if isinstance(obj, dict) and obj.get("action"):
            said = (f"{obj.get('action')}{' from ' + str(obj['args']['from_step']) if (obj.get('args') or {}).get('from_step') else ''}"
                    f" (confidence {obj.get('confidence')}): {short(obj.get('why'), 160)}")
        else:
            said = f"an answer that isn't a plan: {short(text, 160)}"
        self._ev(ex, "manager", "fix_plan", f"the manager ({self.label}) answers {said}", ok=True, escalation=esc_id,
                 plan=obj if isinstance(obj, dict) else None, raw=None if isinstance(obj, dict) else short(text, 400),
                 seconds=round(time.time() - t_ask, 1), run=getattr(self.client, "last", {}).get("run_id"))
        st = ex.host("status")
        state = {"incident_open": any(i.get("id") == iid for i in st.get("incidents") or []),
                 "halted": bool(st.get("incidents")), "now": time.time(), "asked_at": t_ask,
                 "heals_this_job": len(ex.healed), "redos_this_job": self.redos.get(ex.ctx.get("card"), 0),
                 "tried": esc["state"]["tried"] + [t for t in self.tried.get(iid, []) if t not in esc["state"]["tried"]]}
        ok, reason = check_plan(text, esc, state)
        self._ev(ex, "worker", "plan_check", (f"plain code checked the plan: ok, {obj['action']} is allowed and "
                                              "within budget") if ok else f"plain code refused the plan: {reason}",
                 ok=ok, reason=reason, escalation=esc_id)
        if not ok:
            if reason.startswith(SAFETY):
                return self._hand_off(ex, inc, esc_id, reason)
            return self._fallback(ex, f"the manager's plan was refused ({reason})")
        self.cur.update(source="plan", plan=obj, allowed=esc["allowed_actions"])
        self._comment_job(ex, f"PLAN: {kind} during {step} went to the manager ({self.label}) as {esc_id}: it chose "
                              f"{said}; plain code checked it and runs it now.")
        self._comment_inc(ex, f"PLAN: {said}. Plain code's check: ok.")
        ok, action, detail = self.execute(obj, ex, inc, step)
        self.tried.setdefault(iid, []).append({"action": obj["action"], "verified": bool(ok)})
        self._ev(ex, "worker", "plan_action", f"{obj['action']}: {detail}" + (" (verified)" if ok else ""),
                 ok=ok, action=obj["action"], verified=ok, escalation=esc_id, via=self.exec_via)
        if not ok and obj["action"] == "restart_sensor" and ex._pod_side(inc, ex.host("status")):
            return self._fallback(ex, f"the plan's sensor restart didn't fix it and podd now flags the pod ({detail})")
        return ok, action, detail

    def _fallback(self, ex, note):
        self.cur.update(source="fallback", note=note)
        self._comment_job(ex, f"PLAN: {note}; the scripted heal runs instead.")
        self._comment_inc(ex, f"PLAN: {note}; the scripted heal runs instead.")
        return None

    def _hand_off(self, ex, inc, esc_id, reason):
        r = ex.host("handoff", id=inc.get("id"), reason=f"the healer refused a plan: {reason}")
        detail = f"the manager's plan was refused ({reason}); the arm stays parked and a person takes it"
        self.cur.update(source="plan", note=detail)
        self._ev(ex, "worker", "plan_action", f"handoff_human: {detail}", ok=False, action="handoff_human",
                 verified=False, escalation=esc_id, host=r)
        self._comment_job(ex, f"PLAN: {detail}.")
        self._comment_inc(ex, f"PLAN: refused, {reason}.")
        return False, "handoff_human", detail

    # ---- 5. one checked action -------------------------------------------------------------------
    def execute(self, plan, ex, inc, step):
        """Issue the checked action; (ok, action, detail). verify() on the host decides `ok`."""
        if self.exec_via == "lm" and self.lm:
            call, why_call = lm_issue(plan, (self.cur or {}).get("allowed") or [plan["action"]], self.lm,
                                      timeout=self.exec_cap_s)
            ok, why_gate = gate_call(call, plan)
            self._ev(ex, "worker", "plan_gate", (f"the worker's model issued {plan['action']}: the gate allows it"
                                                 if ok else f"the gate refused the model's call ({why_gate if call else why_call}); "
                                                 f"plain code issues {plan['action']} itself"),
                     ok=ok, call=call, reason=why_gate if call else why_call)
        a, why = plan["action"], short(plan.get("why"), 160)
        if a == "restart_sensor":
            r = ex.host("repair", action="restart_sensor")
            if r.get("verified"):
                return True, "restart_sensor", "restarted the sensor through podd; the check at home passed"
            return False, "restart_sensor", f"restarted the sensor, but the check at home still fails: {ex._still(r)}"
        if a in ("verify", "no_action"):
            r = ex.host("repair", action="verify")
            if r.get("verified"):
                return True, "verify", "a checked retry at home passed"
            handed = ex._handed_off(ex.host("status"), inc.get("id"))
            return False, "verify", ((handed or {}).get("handoff_reason")
                                     or f"still failing after a checked retry at home: {ex._still(r)}")
        if a == "pod_rollback":
            # The pod's own healer runs the rollback (on the bench `heal fw_rollback`, firmware/healer/tools.json;
            # in sim armsim/podheal_sim.py), and the host checks at home once podd is clear: wait for both.
            return ex._wait_for_pod(inc, first="the pod healer's rollback: ")
        if a == "park":
            ex.host("skill", name="park")
            return False, "park", f"the manager chose to keep the arm parked ({why})"
        if a == "handoff_human":
            ex.host("handoff", id=inc.get("id"), reason=f"the manager handed it to a person: {why}")
            return False, "handoff_human", f"the manager handed it to a person ({why})"
        if a == "redo_step":
            # Never reached: redo_step is allowed for wrong_action incidents only, and those run in
            # armsim/redo.py (--wrong-action redo), with the reversal, the correction and the check.
            for name in ("release", "go_home"):
                ex.host("skill", name=name)
            card = ex.ctx.get("card")
            self.redos[card] = self.redos.get(card, 0) + 1
            return True, "redo_step", f"reversed {step} (release, go home) to redo from {plan['args']['from_step']}"
        return False, a, f"no code runs {a!r}"

    # ---- the record ------------------------------------------------------------------------------
    def closed(self, ex, inc, step, ok, action, detail):
        """Called by the scripted heal once it knows the outcome; returns the detail for the job card."""
        cur, self.cur = self.cur or {}, None
        if cur.get("iid") != inc.get("id"):
            return detail
        if cur.get("source") == "plan" and cur.get("plan"):
            detail = f"the manager ({self.label}) planned {cur['plan']['action']}, plain code checked and ran it: {detail}"
        elif cur.get("note"):
            detail = f"{cur['note']}; the scripted heal: {detail}"
        if cur.get("inc_card"):
            card = cur["inc_card"]
            if ok:
                self._board(ex, "comment RESULT on the incident card", ex.board.comment, card, f"RESULT: healed. {detail}")
                self._board(ex, "close the incident card", ex.board.done, card,
                            note=f"Healed {inc.get('kind')} during {step} ({action}).")
            else:
                self._board(ex, "comment BLOCKED on the incident card", ex.board.comment, card,
                            f"BLOCKED: {detail}; the arm stays parked")
                self._board(ex, "block the incident card", ex.board.move, card, status="blocked")
        return detail

    def _incident_card(self, ex, esc, inc, step):
        if not self.incident_cards:
            return
        f = esc["findings"][0]
        body = (f"{f['summary']}\n\nincident: {inc.get('id')} on the arm host, during {step}\njob card: "
                f"{ex.ctx.get('card')}\nallowed: {', '.join(esc['allowed_actions'])}\nmanager: {self.label}\n"
                "Written by the worker (armsim/worker.py): PLAN:, then RESULT: or BLOCKED:.")
        card = self._board(ex, "add the incident card", ex.board.add,
                           f"INC {esc['incident_id']}: {f['kind']} on {esc['device_id']}", body=body, key=esc["incident_id"])
        if card and card.get("id"):
            self.cur["inc_card"] = card["id"]
            self._board(ex, "move the incident card", ex.board.move, card["id"], status="doing")
            self._comment_inc(ex, f"ESCALATION: sent to the manager ({self.label}) with deadline {esc['deadline_s']:.0f} s; "
                              f"{len(esc['findings'])} finding(s), worker model: "
                              f"{'answered' if esc['worker'] else 'none'}.")

    def _comment_inc(self, ex, text):
        card = (self.cur or {}).get("inc_card")
        if card:
            self._board(ex, "comment on the incident card", ex.board.comment, card, text)

    def _comment_job(self, ex, text):
        if ex.ctx.get("card"):
            self._board(ex, "comment PLAN on the job card", ex.board.comment, ex.ctx["card"], text)

    def _save(self, esc):
        try:
            self.packet_dir.mkdir(parents=True, exist_ok=True)
            path = self.packet_dir / f"{esc['incident_id']}.json"
            path.write_text(json.dumps(esc, indent=1))
            return path
        except OSError:
            return None


def make(args, emit=None):
    """The Worker for dispatch.py's flags, or None for --manager off."""
    if getattr(args, "manager", "off") in (None, "off"):
        return None
    from .manager import ManagerClient, fake_client
    if args.manager == "fake":
        client, _srv = fake_client(args.fake_mode)
    else:
        client = ManagerClient(args.manager_url, key=os.environ.get("ARMSIM_MANAGER_KEY"), label="hermes")
    lm = None
    if getattr(args, "worker_lm", "off") == "ollama":
        lm = {"endpoint": args.ollama, "model": args.worker_model}
    return Worker(client, emit=emit, lm=lm, device_id=getattr(args, "device_id", None),
                  deadline_s=args.manager_deadline, exec_via=getattr(args, "exec_via", "code"))


def main():
    """`python -m armsim.worker packet`: build and validate the packet for the host's first open incident."""
    from .client import call
    ap = argparse.ArgumentParser(description="Print the escalation packet for the arm host's open incident.")
    ap.add_argument("cmd", choices=["packet"])
    ap.add_argument("--deadline", type=float, default=90.0)
    a = ap.parse_args()
    st = call("status")
    if not st.get("incidents"):
        raise SystemExit("no open incident on the arm host (plant one: client.call('inject', fault='us_frozen'))")
    inc = st["incidents"][0]
    esc, meta = build_escalation(inc, call, esc_id=f"inc-manual-{uuid.uuid4().hex[:8]}", status=st,
                                 deadline_s=a.deadline, pod_side=bool((st.get("pod") or {}).get("incidents")))
    print(json.dumps(esc, indent=1))
    errs = validate(esc, ESCALATION_SCHEMA)
    print("valid arm-escalation/v1" if not errs else "INVALID: " + "; ".join(errs), flush=True)


if __name__ == "__main__":
    main()
