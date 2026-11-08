#!/usr/bin/env python3
"""Naïve Technician demo helper: one fresh joint_stall → its work order → a technician case.

    python3 cloud/scripts/technician_demo.py live           # sim-1, a real Naïve session + email
    python3 cloud/scripts/technician_demo.py replay --no-open   # the bundled recording
    python3 cloud/scripts/technician_demo.py fake --device sim-2 --base http://localhost:8001
    python3 cloud/scripts/technician_demo.py --status tc_...    # where a case stands

It posts `cloud/tests/fixtures/technician/incident_joint_stall.json` with a new incident_id
and now as occurred_at to `POST /v1/devices/<device>/incidents`, waits (<= 15 s) for the
worker's `triage_new` to open its work order (`GET /v1/work-orders`), then opens the case with
`POST /v1/work-orders/<wo>/technician?mode=<mode>` and opens the case page (macOS `open`).
Routes: docs/naive-technician/design.md §9. Stdlib only; runbook: docs/naive-technician/runbook.md.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

FIXTURE = (
    Path(__file__).resolve().parents[1] / "tests/fixtures/technician/incident_joint_stall.json"
)
WAIT_S = 15


class Fail(Exception):
    """A clear one-line error for the operator; main prints it and exits 1."""


def call(base: str, method: str, path: str, body: dict | None = None, timeout: float = 15):
    """(status, parsed JSON or text). Raises Fail when nothing answers at `base`."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise Fail(
            f"Romulus isn't answering at {base} ({reason}). Is the stack up? "
            "export COMPOSE_PROJECT_NAME=tech; make dev && make seed"
        ) from None
    text = raw.decode(errors="replace")
    try:
        return status, json.loads(text) if text else None
    except ValueError:
        return status, text


def detail(payload) -> str:
    if isinstance(payload, dict):
        return f"{payload.get('error', '?')}: {payload.get('detail', payload)}"
    return str(payload)[:300]


def post_incident(base: str, device: str) -> str:
    incident = json.loads(FIXTURE.read_text())
    incident["incident_id"] = str(uuid.uuid4())
    incident["occurred_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if "device_id" in incident:
        incident["device_id"] = device
    status, body = call(base, "POST", f"/v1/devices/{device}/incidents", incident)
    if status == 404 and isinstance(body, dict) and body.get("error") == "unknown_device":
        raise Fail(f"device {device!r} isn't registered on {base}: `make seed` ({detail(body)})")
    if status not in (200, 201):
        raise Fail(f"POST incident answered {status}: {detail(body)}")
    return body["incident_id"]


def find_work_order(base: str, incident_id: str) -> dict:
    deadline = time.monotonic() + WAIT_S
    while True:
        status, body = call(base, "GET", "/v1/work-orders?limit=100")
        if status != 200:
            raise Fail(f"GET /v1/work-orders answered {status}: {detail(body)}")
        for order in body:
            if order.get("incident_id") == incident_id:
                return order
        if time.monotonic() >= deadline:
            raise Fail(
                f"no work order for incident {incident_id} after {WAIT_S} s. Is the worker "
                "running (`docker compose -p tech ps`, `docker compose -p tech logs worker`)?"
            )
        time.sleep(1)


def open_case(base: str, order_id: str, mode: str) -> dict:
    status, body = call(base, "POST", f"/v1/work-orders/{order_id}/technician?mode={mode}")
    if status == 200:
        return body
    if status == 404 and isinstance(body, dict) and body.get("error") == "not_found":
        raise Fail(
            f"{base} has no technician route: this stack isn't running the PR #68 backend "
            "(tech/backend)"
        )
    if status == 409:
        raise Fail(
            f"409 {detail(body)}\n  (TECHNICIAN_MODE and the NAIVE_* ids live in .env; after "
            "changing it, run `make dev` again: a plain `restart` keeps the old env)"
        )
    raise Fail(f"POST technician answered {status}: {detail(body)}")


def run(args: argparse.Namespace) -> None:
    base = args.base.rstrip("/")
    incident_id = post_incident(base, args.device)
    print(f"incident    {incident_id}  ({args.device}, joint_stall)")
    order = find_work_order(base, incident_id)
    print(f"work order  {order['id']}  ({order['status']}, part {order['part']})")
    if order["status"] != "needs_human":
        print(f"  note: the order is {order['status']}, not needs_human; opening anyway")
    case = open_case(base, order["id"], args.mode)
    url = base + case["page_url"]
    print(f"case        {case['id']}  ({case['mode']}, {case['status']})")
    if case["mode"] != args.mode:
        print(
            f"  note: the worker had already opened a {case['mode']} case for this order "
            "(TECHNICIAN_MODE in .env); it came back instead of a new one"
        )
    print(f"page        {url}")
    print(f"status      python3 cloud/scripts/technician_demo.py --status {case['id']}")
    if not args.no_open and sys.platform == "darwin":
        subprocess.run(["open", url], check=False)


DURATION_RE = re.compile(r'title="Naïve session time[^"]*">([^<]+)<')


def status(args: argparse.Namespace) -> None:
    base, case_id = args.base.rstrip("/"), args.status
    code, case = call(base, "GET", f"/v1/technician/cases/{case_id}")
    if code != 200:
        raise Fail(f"GET case answered {code}: {detail(case)}")
    report = case.get("report") or {}
    email = (case.get("actions") or {}).get("email") or {}
    part = (case.get("actions") or {}).get("part") or {}
    staged = case.get("technician_part")
    duration = None  # case JSON has no duration; the page's header does
    pcode, page = call(base, "GET", f"/technician/{case_id}/partial")
    if pcode == 200 and isinstance(page, str):
        found = DURATION_RE.search(page)
        duration = found.group(1).strip() if found else None
    if staged:
        part_line = (
            f"staged: {staged.get('qty')} × {staged.get('title')} at ${staged.get('unit_usd')}"
            f" · {staged.get('cart_url')}"
        )
    elif part:
        part_line = f"not staged: {part.get('blocked_reason') or part.get('shop_error') or '?'}"
    else:
        part_line = "none yet" if not report else "none (no part needed)"
    email_line = email.get("status") or "not yet"
    if email.get("to"):
        email_line += f" → {email['to']}"
    if email.get("error"):
        email_line += f" ({email['error']})"
    verdict = report.get("verdict") or "—"
    if report.get("confidence"):
        verdict += f" ({report['confidence']} confidence)"
    rows = [
        ("case", f"{case['id']}  ({case['mode']}, {case['device_id']})"),
        ("status", case["status"] + (f": {case['why']}" if case.get("why") else "")),
        ("work order", f"{case['work_order_id']}  ({case.get('work_order_status')})"),
        ("verdict", verdict),
        ("email", email_line),
        ("part", part_line),
        ("cost", f"${case['cost_usd']:.2f}" if case.get("cost_usd") is not None else "—"),
        ("duration", duration or "—"),
        ("session", case.get("naive_session_id") or "—"),
        ("page", f"{base}/technician/{case_id}"),
    ]
    for key, value in rows:
        print(f"{key:<11} {value}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Post a fresh joint_stall and open a Naïve Technician case on its work order."
    )
    ap.add_argument("mode", nargs="?", choices=("live", "replay", "fake"), default="live")
    ap.add_argument("--device", default="sim-1", help="device that reports it (default sim-1)")
    ap.add_argument("--base", default="http://localhost:8000", help="Romulus API base URL")
    ap.add_argument("--no-open", action="store_true", help="print the page URL, don't open it")
    ap.add_argument("--status", metavar="CASE_ID", help="print where a case stands, then exit")
    args = ap.parse_args(argv)
    try:
        if args.status:
            status(args)
        else:
            run(args)
    except Fail as exc:
        sys.stdout.flush()
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
