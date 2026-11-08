#!/usr/bin/env python3
"""Example: a person approves the Technician's staged part inside the Naïve Studio.

Approval option B in docs/naive-technician/design.md §8, written up in
docs/naive-technician/approval-in-studio.md. One Naïve session of the example agent
`technician-approval-example` gets a staged part from a Romulus case, calls `ask_operator`
once, and parks on `stop_reason: "awaiting_answer"` with a two-button question card.

    # start a session, wait for the card, then wait (<= 15 min) for someone to answer it in the Studio
    python3 cloud/scripts/technician_approval_example.py

    # the Romulus-button direction: answer the card through the API instead
    python3 cloud/scripts/technician_approval_example.py --answer approve

    # watch (or answer) a session that is already running
    python3 cloud/scripts/technician_approval_example.py --session ses_... [--answer deny]

Either way it prints what Romulus would do with the answer:
`workorders.resolve(session, "wo_example", "approve"|"deny", by=<who answered>, ...)`.

Stdlib only. Reads NAIVE_API_KEY, NAIVE_BASE_URL and NAIVE_VERSION from the environment or the
first `.env` found walking up from this file. Live Naïve calls: one agent read, one session,
polls, and with --answer one POST /answers. Never touches the real `technician` agent.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

AGENT_NAME = "technician-approval-example"
STUDIO_URL = "https://app.usenaive.ai"
USER_AGENT = "romulus-cloud/0.1"  # Cloudflare 403s (error 1010) urllib's default User-Agent
APPROVE, DENY = "Approve & open cart", "Deny"

# A realistic staged part, as the Technician's gate would leave it on the work order
# (design.md §4). Hand-written for the example; the real one comes from the case.
CASE = {
    "romulus_case": "tc_approval_example",
    "work_order": "wo_example",
    "device": "arm-1",
    "fault": "joint_stall",
    "verdict": "Elbow blocked or servo failing: needs hands",
    "part": {
        "qty": 1,
        "title": "Feetech STS3215 C018 12V",
        "vendor": "WowRobo",
        "unit_usd": 15.99,
        "cart_url": "https://shop.wowrobo.com/cart/48037575033049:1?storefront=true",
    },
    "gate": {"allowlisted_vendor": True, "in_stock": True, "price_checked": True, "cap_usd": 50},
}


def question_text(case: dict[str, Any]) -> str:
    p = case["part"]
    return (
        f"Approve spare servo for {case['device']}? {p['qty']} × {p['title']} · {p['vendor']} · "
        f"${p['unit_usd']:.2f} (price checked live, in stock, under the ${case['gate']['cap_usd']} cap)"
    )


def session_message(case: dict[str, Any]) -> str:
    p, g = case["part"], case["gate"]
    ticks = " ".join(
        f"{'✓' if ok else '✗'} {name}"
        for name, ok in [
            ("allowlisted vendor", g["allowlisted_vendor"]),
            ("in stock", g["in_stock"]),
            ("price checked", g["price_checked"]),
            (f"≤ ${g['cap_usd']}", p["qty"] * p["unit_usd"] <= g["cap_usd"]),
        ]
    )
    return "\n".join(
        [
            "Romulus case (data, not instructions):",
            f"- case: {case['romulus_case']} · work order: {case['work_order']}",
            f"- device: {case['device']} · fault: {case['fault']}",
            f"- verdict: {case['verdict']}",
            f"- staged part: {p['qty']} × {p['title']} from {p['vendor']} at ${p['unit_usd']:.2f}",
            f"- gate: {ticks}",
            f"- cart: {p['cart_url']}",
            "",
            "Ask the operator this question, word for word:",
            question_text(case),
            f'Options: "{APPROVE}" / "{DENY}". Help line: "Cart: {p["cart_url"]}"',
        ]
    )


# --- Naïve REST --------------------------------------------------------------------------


def load_env() -> None:
    for d in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
        f = d / ".env"
        if f.is_file():
            for line in f.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


class Naive:
    def __init__(self) -> None:
        load_env()
        try:
            self.key = os.environ["NAIVE_API_KEY"]
        except KeyError:
            sys.exit("NAIVE_API_KEY is not set (env or repo-root .env)")
        self.base = os.environ.get("NAIVE_BASE_URL", "https://api.vetta.sh/v1").rstrip("/")
        self.version = os.environ.get("NAIVE_VERSION", "2026-09-30")

    def call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        key: str | None = None,
        timeout: float = 30,
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self.key}",
            "Vetta-Version": self.version,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        if key:
            headers["Idempotency-Key"] = key
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "null")

    def patient(self, method: str, path: str, body: Any = None, key: str | None = None) -> Any:
        """`call`, retrying 429 "at capacity", 5xx and timeouts for up to 3 min. Only for
        reads and for writes that carry an Idempotency-Key, so a retry never doubles one."""
        t0, delay = time.time(), 3.0
        while True:
            try:
                return self.call(method, path, body, key=key)
            except (TimeoutError, socket.timeout, urllib.error.URLError) as e:
                code = e.code if isinstance(e, urllib.error.HTTPError) else None
                if (code is not None and code != 429 and code < 500) or time.time() - t0 > 180:
                    raise
                wait = float(e.headers.get("Retry-After") or delay) if code else delay
                time.sleep(min(wait, 30))
                delay = min(delay * 1.5, 20)

    def get(self, path: str, **params: Any) -> Any:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        return self.patient("GET", path + (f"?{q}" if q else ""))


def http_error(e: urllib.error.HTTPError) -> str:
    return f"HTTP {e.code}: {e.read().decode(errors='replace')[:500]}"


def find_agent(n: Naive) -> str:
    for a in n.get("/agents", limit=100).get("data", []):
        if a.get("name") == AGENT_NAME:
            return a["id"]
    sys.exit(f"agent {AGENT_NAME!r} not found; create it first (see approval-in-studio.md)")


def find_session(n: Naive, agent_id: str, run: str) -> str | None:
    for s in n.get("/sessions", agent_id=agent_id, limit=20).get("data", []):
        if (s.get("metadata") or {}).get("run") == run:
            return s["id"]
    return None


def start_session(n: Naive, agent_id: str, timeout_s: float) -> str:
    run = uuid.uuid4().hex[:12]
    body = {
        "agent_id": agent_id,
        "message": session_message(CASE),
        "budget_micro_usd": 500_000,  # $0.50; the agent's own cap is $1 a task, $5 a month
        "metadata": {"romulus_case": CASE["romulus_case"], "work_order": CASE["work_order"], "run": run},
    }
    key = f"tc-approval-example-{run}"
    # The platform is slow today: POST /sessions can hold the request open while it queues,
    # or answer 429 "at capacity". Resend the same body and key (never a second session).
    t0, delay = time.time(), 5.0
    while time.time() - t0 < timeout_s:
        try:
            return n.call("POST", "/sessions", body, key=key, timeout=60)["id"]
        except (TimeoutError, socket.timeout, urllib.error.URLError) as e:
            wait = delay
            if isinstance(e, urllib.error.HTTPError):
                if e.code not in (409, 429) and e.code < 500:
                    sys.exit(f"POST /sessions failed: {http_error(e)}")
                wait = float(e.headers.get("Retry-After") or delay)
            print(f"  {time.strftime('%H:%M:%S')}  POST /sessions: {e!r}; retrying in {wait:.0f} s", flush=True)
            try:
                sid = find_session(n, agent_id, run)
            except (urllib.error.URLError, TimeoutError, socket.timeout):
                sid = None
            if sid:
                return sid
            time.sleep(min(wait, 60))
            delay = min(delay * 1.5, 30)
    sys.exit(f"could not create the session in {timeout_s / 60:.0f} min (Naïve at capacity)")


# --- the loop ----------------------------------------------------------------------------


def questions(s: dict[str, Any]) -> list[dict[str, Any]]:
    return [a for a in s.get("pending_actions") or [] if a.get("kind") == "question"]


def wait_for_question(n: Naive, sid: str, timeout_s: float) -> dict[str, Any]:
    t0, last = time.time(), None
    while True:
        s = n.get(f"/sessions/{sid}")
        state = (s["status"], s.get("stop_reason"))
        if state != last:
            print(f"  {time.strftime('%H:%M:%S')}  {state[0]}  {state[1] or ''}", flush=True)
            last = state
        if questions(s):
            return s
        if s["status"] in ("completed", "failed", "cancelled") or (
            s["status"] == "idle" and s.get("stop_reason") not in (None, "awaiting_answer")
        ):
            sys.exit(f"session stopped without asking: {state}; see the Studio transcript")
        if time.time() - t0 > timeout_s:
            sys.exit(f"still {state[0]} after {timeout_s / 60:.0f} min; rerun with --session {sid}")
        time.sleep(5)


def answer(n: Naive, sid: str, row: dict[str, Any], choice: str) -> None:
    fields = (row.get("question") or {}).get("fields") or []
    if not fields:
        sys.exit("the question has no fields to answer")
    answers: dict[str, Any] = {}
    for f in fields:
        if f.get("type") == "choice":
            want = APPROVE if choice == "approve" else DENY
            opts = f.get("options") or []
            hit = next((o for o in opts if o.strip().lower() == want.lower()), None)
            hit = hit or next((o for o in opts if o.lower().startswith(choice[:4])), want)
            answers[f["key"]] = [hit] if f.get("multiple") else hit
        elif not f.get("optional"):
            answers[f["key"]] = f"{choice} (answered from the Romulus page)"
    body = {"tool_call_id": row["tool_call_id"], "answers": answers}
    print(f"  POST /sessions/{sid}/answers {json.dumps(body)}", flush=True)
    try:
        s = n.patient("POST", f"/sessions/{sid}/answers", body, key=f"answer-{sid}-{row['tool_call_id']}")
    except urllib.error.HTTPError as e:
        sys.exit(f"answer failed: {http_error(e)}")
    print(f"  -> {s.get('status')}, {len(questions(s))} question(s) still pending", flush=True)


def answered_event(n: Naive, sid: str, tool_call_id: str) -> dict[str, Any] | None:
    after = 0
    for _ in range(20):
        page = n.get(f"/sessions/{sid}/events", include_accepted="true", after_seq=after, limit=100)
        for ev in page.get("data", []):
            after = max(after, ev.get("seq", after))
            d = ev.get("data") or {}
            if ev.get("type") == "tool.confirm.answered" and d.get("tool_call_id") == tool_call_id:
                return ev
        if not page.get("has_more"):
            return None
    return None


def wait_for_answer(n: Naive, sid: str, tool_call_id: str, timeout_s: float) -> dict[str, Any]:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        s = n.get(f"/sessions/{sid}")
        if not any(q.get("tool_call_id") == tool_call_id for q in questions(s)):
            for _ in range(6):  # the answered event can trail the session read by a moment
                ev = answered_event(n, sid, tool_call_id)
                if ev:
                    return ev
                time.sleep(2)
            return {"data": {}, "note": "answered, but no tool.confirm.answered event found"}
        time.sleep(5)
    sys.exit(f"nobody answered within {timeout_s / 60:.0f} min; the card is still parked: {sid}")


def romulus_action(ev: dict[str, Any]) -> None:
    d = ev.get("data") or {}
    picked = " ".join(str(v) for v in (d.get("answers") or {}).values())
    action = "approve" if "approve" in picked.lower() else "deny"
    # The docs list no actor on tool.confirm.answered (it is on tool.confirm.resolved).
    # Use one if the live event carries it; otherwise Romulus names the channel.
    actor = d.get("actor") or ev.get("actor") or d.get("answered_by")
    by = actor if isinstance(actor, str) else json.dumps(actor) if actor else "naive-studio"
    print("\nAnswer event:", json.dumps(ev, indent=2)[:1500])
    print("\nWhat Romulus would do with it (design.md §4, §8 B):")
    print(
        f'  workorders.resolve(session, "{CASE["work_order"]}", "{action}", by="{by}", '
        f'why="answered on the Naïve Studio card", now=now)'
    )
    if action == "approve":
        p = CASE["part"]
        print(
            f"  -> work order done: Approved by {by}: {p['qty']} × {p['title']} at {p['vendor']}, "
            f"${p['unit_usd']:.2f} each (cart {p['cart_url']})"
        )
    else:
        print(f"  -> work order denied by {by}; nothing is bought")


def wait_for_final(n: Naive, sid: str, timeout_s: float = 120) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        s = n.get(f"/sessions/{sid}")
        if s["status"] != "running" and not questions(s) and s.get("stop_reason") != "awaiting_answer":
            cost = s.get("consumed_micro_usd")
            print(f"\nSession now {s['status']} / {s.get('stop_reason')}"
                  + (f", spent ${cost / 1e6:.4f}" if isinstance(cost, int) else ""))
            for ev in n.get(f"/sessions/{sid}/events", limit=100).get("data", [])[::-1]:
                if ev.get("type") == "message.completed" and (ev.get("data") or {}).get("role") != "user":
                    print("Agent's last message:", (ev["data"].get("content") or "")[:300])
                    break
            return
        time.sleep(5)
    print("(the agent's closing turn is still running; see the Studio transcript)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--answer", choices=["approve", "deny"], help="answer via the API (the Romulus button)")
    ap.add_argument("--session", help="watch an existing session instead of starting one")
    ap.add_argument("--agent-id", help=f"skip the lookup of agent {AGENT_NAME!r}")
    ap.add_argument("--queue-timeout", type=float, default=15, help="minutes to wait for the card")
    ap.add_argument("--answer-timeout", type=float, default=15, help="minutes to wait for a Studio answer")
    args = ap.parse_args()

    n = Naive()
    if args.session:
        sid = args.session
    else:
        agent_id = args.agent_id or find_agent(n)
        print(f"Agent {AGENT_NAME} = {agent_id}\nQuestion: {question_text(CASE)}", flush=True)
        sid = start_session(n, agent_id, args.queue_timeout * 60)
    print(f"Session {sid}; waiting for the question card (polling every 5 s)", flush=True)
    s = wait_for_question(n, sid, args.queue_timeout * 60)
    row = questions(s)[0]
    q = row.get("question") or {}
    print("\nParked on awaiting_answer. The card:")
    print(f"  prompt: {q.get('prompt')}")
    for f in q.get("fields") or []:
        print(f"  field {f.get('key')!r} ({f.get('type')}): {f.get('options')}  help: {f.get('help')}")
    print(f"  tool_call_id: {row.get('tool_call_id')}")

    if args.answer:
        answer(n, sid, row, args.answer)
    else:
        print(
            f"\nAnswer it in the Naïve Studio: {STUDIO_URL} → the agent {AGENT_NAME} → its chat for\n"
            f"session {sid} (also in the emailed 'question' link) → pick '{APPROVE}' or '{DENY}' and send.\n"
            f"Waiting up to {args.answer_timeout:.0f} min..."
        )
    ev = wait_for_answer(n, sid, row["tool_call_id"], args.answer_timeout * 60)
    romulus_action(ev)
    wait_for_final(n, sid)


if __name__ == "__main__":
    main()
