"""Fleet learning A2: one cluster-review session on the Naïve agent `fleet-doctor`, live.

It builds the case file of design.md §4.2 for the hero cluster (`us_frozen`: the arm-sim watchdog
handles it, the pods' detect.py doesn't), sends it with the §4.4 proposal schema
(`structured_output_required: true`) through the shipping adapter (`HttpNaive.create_session /
get_session / session_events / find_session`), validates the typed proposal with the Technician's
schema checker (`domain/technician.py` `_check`), checks G1 and writes a fixture.

Run from the repo root (settings from the environment, else --env-file):

    uv run python cloud/scripts/review_spike.py \\
        --env-file /Users/kentgang/git2/Naive-Nous-YC-Hackathon/.env \\
        --fixture-out cloud/tests/fixtures/review/us_frozen_live.json

    uv run python cloud/scripts/review_spike.py --dry-run --case-file /tmp/case.json  # no Naïve

Settings: NAIVE_API_KEY, NAIVE_BASE_URL, NAIVE_VERSION, NAIVE_REVIEW_AGENT.

The evidence run is real-shape: the harness transcript records `edge_bridge.fake_healer.AgentRunDir`
writes, turned into an agent run report by the unmodified `edge_bridge.agent_runs.build()`. Its
telemetry and triage results are computed on a committed frozen window (us_frozen_windows.json) by
today's detect.py; the healer's words in it are scripted (it is a sim rehearsal of the hero fault).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from edge_bridge import agent_runs
from edge_bridge.fake_healer import AgentRunDir
from romulus_cloud.adapters.naive import DEFAULT_BASE_URL, DEFAULT_VERSION, HttpNaive, NaiveError
from romulus_cloud.domain.datasets import redact
from romulus_cloud.domain.technician import _check

sys.path.insert(0, str(Path(__file__).resolve().parent))
# lesson_probe puts prototypes/arm-sim on sys.path; workerbench.l1 adds firmware/healer (detect.py)
import lesson_probe  # noqa: E402

REPO = lesson_probe.REPO
FIX = REPO / "cloud" / "tests" / "fixtures" / "review"
KIND = "us_frozen"
DEVICE, DEVICE_CLASS = "sim-pod-1", "sensor_pod"
DEVICE_CLASSES = ["so101", "sensor_pod"]
WINDOW_ID = "frozen-10-0"
# no "sim" segment in the run id: agent_runs.build() would mark the run a rehearsal (excluded)
RUN_ID = "20261108-084500-ollama-watch"
TEXT_MAX = 200
STEPS_MAX = 8
POLL_S = 3.0
BUDGET_USD = 0.50
G1_SECONDS = 60.0
LEVEL1 = REPO / "prototypes" / "arm-sim" / "armsim" / "level1.py"
DETECT = REPO / "firmware" / "healer" / "healer" / "detect.py"
SAFE_REPLAY = REPO / "arm" / "safe_replay.py"


# --- the evidence run -----------------------------------------------------------------------


def _rel(p: Path) -> str:
    return str(p.relative_to(REPO))


def evidence_run(window: dict[str, Any], index: int) -> dict[str, Any]:
    """An agent run report for a pod healer woken on the watchdog's us_frozen finding, whose own
    triage (today's detect.py, run here on the window) sees nothing."""
    snap = lesson_probe.window_snapshot(index, window)
    now = snap["now"]
    wd = lesson_probe.l1.level1.check_ultrasonic_frozen({"now": now, "D": snap["D"]})
    triage = lesson_probe.l1.detect.run(snap)
    recent = [r for r in snap["D"] if r["ts"] >= now - 1.0]
    t = now + 10.0  # the harness wakes about 10 s after the incident (agent_runs.LINK_BEFORE_S)
    with tempfile.TemporaryDirectory() as tmp:
        run = AgentRunDir(Path(tmp), RUN_ID)
        incident = {
            "id": "inc-us-frozen-1",
            "kind": KIND,
            "severity": wd[0]["severity"] if wd else "critical",
            "summary": wd[0]["summary"] if wd else "frozen ultrasonic (arm watchdog)",
        }
        # AgentRunDir.start's record with the arm's kickoff: podd opened nothing (no detect.py rule)
        run.write(
            "run_start",
            t,
            label="watch",
            driver="ollama",
            model="qwen3.5:4b",
            target=None,
            tools=["triage", "telemetry_recent", "pod_cmd", "fw_flash"],
            kickoff=f"The robot arm's watchdog reported {KIND} ({incident['severity']}) on this "
            f"pod's distance stream: {incident['summary']}\npodd has no open incidents.",
            podd_incidents=None,
            arm=None,
            chip_at_start={
                "crc": "A1C69398",
                "build_id": gen_build(snap),
                "running": True,
                "version": "v1.1-sim",
                "status": "known-good",
                "origin": "release",
            },
        )
        run.call(
            t + 4,
            "triage",
            {},
            {"findings": triage},
            thinking="The arm host reports a frozen ultrasonic. "
            "Let me run the pod's own triage first.",
        )
        run.call(
            t + 9,
            "telemetry_recent",
            {"kind": "D", "seconds": 1},
            {"rows": recent},
            thinking="Triage is clean. Let me look at the last second of distance readings.",
        )
        run.end(
            t + 15,
            "Triage finds no fault on the pod and the distance readings are steady and in range. "
            "Nothing to fix on the pod; I changed nothing.",
            {
                "chip": {
                    "crc": "A1C69398",
                    "running": True,
                    "version": "v1.1-sim",
                    "status": "known-good",
                },
                "findings": lesson_probe.l1.detect.run(snap),
                "open_incidents": [],
            },
        )
        records = [json.loads(line) for line in run.path.read_text().splitlines()]
    report = agent_runs.build(RUN_ID, records, incident_ids=[incident["id"]])
    return {"report": report, "incident": incident, "window": window["id"], "watchdog": wd}


def gen_build(snap: dict[str, Any]) -> str:
    boot = next((e for e in snap.get("E") or [] if e.get("ev") == "boot"), {})
    return boot.get("build") or "pod v1.1-sim"


def verdict(report: dict[str, Any]) -> tuple[str, str]:
    """design.md §2's verdict table, first matching row (lane B's domain/runs.py isn't merged)."""
    out = report.get("outcome") or {}
    ver = report.get("verification") or {}
    if report.get("sim"):
        return "excluded", "rehearsal"
    if report.get("state") != "finished":
        return "excluded", "still running"
    if ver.get("passed") is False or out.get("status") == "broken":
        return "fail", f"device check: {out.get('headline') or 'broken'}"
    if ver.get("passed") is True:
        return "pass", "bench check passed"
    return "unverified", f"device says {out.get('status') or 'unknown'}; nothing checked it"


def _cut(text: Any) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= TEXT_MAX else s[: TEXT_MAX - 1] + "…"


def case_run(ev: dict[str, Any]) -> dict[str, Any]:
    rep = ev["report"]
    v, why = verdict(rep)
    steps = [
        {
            "n": s.get("n"),
            "tool": s.get("tool"),
            "action": _cut(s.get("action")),
            "effect": _cut(s.get("effect")),
            "ok": s.get("ok"),
            "thought": _cut(s.get("thought")),
            "excerpt": _cut(s.get("excerpt")),
        }
        for s in (rep.get("steps") or [])[:STEPS_MAX]
    ]
    return redact(
        {
            "id": f"run:{DEVICE}/{rep['run_id']}",
            "device": DEVICE,
            "class": DEVICE_CLASS,
            "device_kind": "sim",
            "kind": KIND,
            "incident": ev["incident"],
            "verdict": v,
            "verifier": why,
            "headline": _cut((rep.get("outcome") or {}).get("headline")),
            "summary": _cut(
                " ".join(
                    f"{p.get('label')}: {p.get('text')}"
                    for p in (rep.get("writeup") or {}).get("summary") or []
                )
            ),
            "steps": steps,
            "source": "edge_bridge.agent_runs.build() over a scripted sim healer transcript; "
            f"telemetry and triage computed on committed window {ev['window']} "
            f"({_rel(lesson_probe.WINDOWS)})",
        }
    )


# --- the case file (design §4.2) ------------------------------------------------------------


def _func(path: Path, name: str) -> tuple[ast.FunctionDef, list[str]]:
    lines = path.read_text().splitlines()
    node = next(
        n
        for n in ast.walk(ast.parse("\n".join(lines)))
        if isinstance(n, ast.FunctionDef) and n.name == name
    )
    return node, lines


def handled_elsewhere() -> dict[str, Any]:
    node, lines = _func(LEVEL1, "check_ultrasonic_frozen")
    module_doc = ast.get_docstring(ast.parse(LEVEL1.read_text())) or ""
    bullet = next((p for p in module_doc.split("\n- ") if "check_ultrasonic_frozen" in p), "")
    return {
        "by": "arm-sim watchdog (level 1, plain code)",
        "device_class": "so101",
        "file": f"{_rel(LEVEL1)}:{node.lineno}-{node.end_lineno}",
        "function": node.name,
        "docstring": re.sub(r"\s+", " ", bullet.lstrip("- ")).strip(),
        "code": "\n".join(lines[node.lineno - 1 : node.end_lineno]),
    }


def pod_rules() -> dict[str, Any]:
    src = DETECT.read_text()
    return {
        "file": _rel(DETECT),
        "rules": [r.__name__ for r in lesson_probe.l1.detect.RULES],
        "finding_kinds": sorted(set(re.findall(r'_f\("([a-z_]+)"', src)) - {"detector_error"}),
        "note": f"no rule here raises {KIND}",
    }


def start_gate() -> dict[str, Any]:
    node, lines = _func(SAFE_REPLAY, "classify")
    return {
        "file": f"{_rel(SAFE_REPLAY)}:{node.lineno}-{node.end_lineno}",
        "excerpt": "\n".join(lines[node.lineno - 1 : node.end_lineno]),
    }


def probe_entry(probe: dict[str, Any]) -> dict[str, Any]:
    keep = ("caught", "of", "false_alarms", "healthy")
    return {
        "id": probe["probe"],
        "kind": probe["kind"],
        "description": probe.get("description"),
        "port": probe["port"],
        "scorecard": {
            "before": {k: probe["before"][k] for k in keep},
            "after": {k: probe["after"][k] for k in keep},
            "limits": probe.get("limits", []),
            "generated_at": probe.get("generated_at"),
            "measured_by": "our probe script (cloud/scripts/lesson_probe.py), not the agent",
        },
    }


def build_case_file(
    probe: dict[str, Any], runs: list[dict[str, Any]], *, case_id: str
) -> dict[str, Any]:
    """The case file for the us_frozen cluster: plain data, sent as untrusted evidence."""
    return {
        "case": case_id,
        "kind": KIND,
        "qualified": {
            "trigger": "transfer",
            "why": f"{len(runs)} {runs[0]['verdict']} run of {KIND} on a {DEVICE_CLASS}, while "
            f"the {DEVICE_CLASSES[0]} arm-sim watchdog already detects {KIND} "
            "(check_ultrasonic_frozen) and the pods' detect.py has no rule for it",
        },
        "runs": runs,
        "handled_elsewhere": [handled_elsewhere()],
        "probes": [probe_entry(probe)],
        "pod_rules": pod_rules(),
        "start_gate": start_gate(),
        "device_classes": DEVICE_CLASSES,
    }


def message(case: dict[str, Any]) -> str:
    return (
        f"Cluster review for fault kind {case['kind']} (case {case['case']}). Read the case "
        "file and return exactly one proposal with submit_output.\n\n"
        '<case_file untrusted="true">\n'
        + json.dumps(case, indent=1, ensure_ascii=False)
        + "\n</case_file>"
    )


# --- the proposal schema (design §4.4) and its checks ---------------------------------------


def proposal_schema(probe_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["gap", "title", "pattern", "evidence", "applies_to", "proposal", "confidence"],
        "properties": {
            "gap": {"enum": ["missed_detection", "failed_heal"]},
            "title": {"type": "string", "maxLength": 80},
            "pattern": {"type": "string", "maxLength": 280},
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 12,
            },
            "applies_to": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "proposal": {
                "oneOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["kind", "probe"],
                        "properties": {"kind": {"const": "rule"}, "probe": {"enum": probe_ids}},
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["kind", "slug", "markdown"],
                        "properties": {
                            "kind": {"const": "skill"},
                            "slug": {"type": "string", "pattern": "^[a-z0-9-]{3,48}$"},
                            "markdown": {"type": "string", "maxLength": 2000},
                        },
                    },
                ]
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }


def _local(schema: Any) -> Any:
    """The schema in the subset `_check` reads: `const` as a one-value `enum`."""
    if isinstance(schema, dict):
        out = {k: _local(v) for k, v in schema.items() if k != "const"}
        if "const" in schema:
            out["enum"] = [schema["const"]]
        return out
    if isinstance(schema, list):
        return [_local(v) for v in schema]
    return schema


def validate(out: Any, schema: dict[str, Any]) -> list[str]:
    """[] when `out` fits the schema: the Technician's `_check`, plus the two things it doesn't
    read here (the proposal's `oneOf`: exactly one branch fits; a float's minimum/maximum)."""
    errors: list[str] = []
    s = _local(schema)
    _check(out, s, "proposal", errors)
    if isinstance(out, dict) and "proposal" in out:
        fits = []
        for branch in s["properties"]["proposal"]["oneOf"]:
            e: list[str] = []
            _check(out["proposal"], branch, "proposal.proposal", e)
            fits.append(e)
        if sum(not e for e in fits) != 1:
            errors.append(
                "proposal.proposal: fits no branch (rule: "
                + "; ".join(fits[0])
                + " | skill: "
                + "; ".join(fits[1])
                + ")"
            )
    c = out.get("confidence") if isinstance(out, dict) else None
    if isinstance(c, float) and not 0 <= c <= 1:
        errors.append("proposal.confidence: out of range")
    return errors


def g1(
    out: dict[str, Any] | None,
    errors: list[str],
    case: dict[str, Any],
    duration_s: float,
    cost_usd: float | None,
) -> list[dict[str, Any]]:
    out = out or {}
    run_ids = {r["id"] for r in case["runs"]}
    probe_ids = {p["id"] for p in case["probes"]}
    prop = out.get("proposal") or {}
    checks = [
        ("the proposal validates", not errors),
        ("gap = missed_detection", out.get("gap") == "missed_detection"),
        (
            "it cites a probe from the case file",
            prop.get("kind") == "rule" and prop.get("probe") in probe_ids,
        ),
        (
            "evidence ids are case-file runs",
            bool(out.get("evidence")) and set(out["evidence"]) <= run_ids,
        ),
        (
            "applies_to is within the case file's classes",
            bool(out.get("applies_to")) and set(out["applies_to"]) <= set(case["device_classes"]),
        ),
        (f"<= {G1_SECONDS:.0f} s", duration_s <= G1_SECONDS),
        (f"<= ${BUDGET_USD:.2f}", cost_usd is not None and cost_usd <= BUDGET_USD),
    ]
    return [{"check": c, "ok": bool(ok)} for c, ok in checks]


# --- the session ----------------------------------------------------------------------------


def load_env(path: Path | None) -> dict[str, str]:
    env: dict[str, str] = {}
    if path and path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if v})
    return env


def run_session(naive: HttpNaive, body: dict[str, Any], case_id: str, timeout_s: float):
    state = None
    for attempt in range(2):  # one retry (429, a held-open POST), then report
        try:
            state = naive.create_session(body, idempotency_key=case_id)
            break
        except NaiveError as exc:
            print(
                f"create_session attempt {attempt + 1}: {exc} (status={exc.status} code={exc.code})"
            )
            if exc.pending:
                state = naive.find_session(body["agent_id"], case_id)
                if state:
                    break
            if attempt == 0 and exc.retryable:
                time.sleep(max(5.0, float(exc.retry_after or 0)))
                continue
            raise
    assert state is not None
    print(f"session {state.id} ({state.raw.get('status')}) studio: {state.studio_url}")
    t0, events, last = time.monotonic(), [], 0
    while True:
        try:
            new = naive.session_events(state.id, after_seq=last)
            state = naive.get_session(state.id)
        except NaiveError as exc:
            print(f"  poll error (retrying): {exc}")
            new = []
        for ev in new:
            events.append(ev)
            last = max(last, ev.seq)
            if (
                ev.kind in ("tool_call", "output", "message")
                or ev.raw.get("type") == "session.idle"
            ):
                print(f"  {ev.raw.get('type')} {ev.tool or ''} {(ev.text or '')[:100]}")
        if state.state != "running":
            for ev in naive.session_events(state.id, after_seq=last):
                events.append(ev)
            break
        if time.monotonic() - t0 > timeout_s:
            print(f"timeout after {timeout_s:.0f} s: cancelling")
            naive.cancel_session(state.id)
            state = naive.get_session(state.id)
            break
        time.sleep(POLL_S)
    return state, events, time.monotonic() - t0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--env-file", type=Path, default=REPO / ".env")
    ap.add_argument(
        "--probe",
        type=Path,
        default=None,
        help="probe JSON (default: data/learning/probes, else the fixture)",
    )
    ap.add_argument("--fixture-out", type=Path, default=None, help="write the session fixture here")
    ap.add_argument(
        "--case-file", type=Path, default=None, help="also write the case file sent here"
    )
    ap.add_argument("--case-id", default=None)
    ap.add_argument(
        "--dry-run", action="store_true", help="build and print the case file; no Naïve call"
    )
    ap.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args(argv)

    probe_path = args.probe or next(
        p for p in (lesson_probe.OUT, FIX / "probe_us_frozen.json") if p.exists()
    )
    probe = json.loads(probe_path.read_text())
    wins = json.loads(lesson_probe.WINDOWS.read_text())["windows"]
    index = next(i for i, w in enumerate(wins) if w["id"] == WINDOW_ID)
    case_id = args.case_id or f"review:{KIND}:{datetime.now(UTC):%Y%m%dT%H%M%S}"
    case = build_case_file(probe, [case_run(evidence_run(wins[index], index))], case_id=case_id)
    schema = proposal_schema([p["id"] for p in case["probes"]])
    msg = message(case)
    print(f"probe: {_rel(probe_path) if probe_path.is_relative_to(REPO) else probe_path}")
    print(f"case {case_id}: {len(msg)} chars; keys {list(case)}")
    if args.case_file:
        args.case_file.parent.mkdir(parents=True, exist_ok=True)
        args.case_file.write_text(json.dumps(case, indent=2, ensure_ascii=False) + "\n")
        print(f"case file written: {args.case_file}")
    if args.dry_run:
        print(msg)
        return 0

    env = load_env(args.env_file)
    missing = [k for k in ("NAIVE_API_KEY", "NAIVE_REVIEW_AGENT") if not env.get(k)]
    if missing:
        print(f"missing settings: {', '.join(missing)} (env or {args.env_file})")
        return 2
    base = env.get("NAIVE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    base = base if base.endswith("/v1") else base + "/v1"
    naive = HttpNaive(
        env["NAIVE_API_KEY"],
        base_url=base,
        version=env.get("NAIVE_VERSION", DEFAULT_VERSION),
        retry_budget_s=10.0,
    )
    agent_id = env["NAIVE_REVIEW_AGENT"]
    body = {
        "agent_id": agent_id,
        "message": msg,
        "output_schema": schema,
        "structured_output_required": True,
        "budget_micro_usd": int(BUDGET_USD * 1_000_000),
        "metadata": {"romulus_case": case_id},
    }
    print(f"LIVE {base}  agent {agent_id}")
    try:
        state, events, wall_s = run_session(naive, body, case_id, args.timeout)
    except NaiveError as exc:
        print(f"session failed: {exc} (status={exc.status} code={exc.code})")
        return 1

    idle_at = next(
        (e.at for e in reversed(events) if e.raw.get("type") == "session.idle" and e.at), None
    )
    started = state.created_at
    finished = state.ended_at or idle_at
    duration_s = (finished - started).total_seconds() if started and finished else wall_s
    cost_reported = state.raw.get("consumed_micro_usd") is not None
    cost_usd = state.cost_usd if cost_reported else None
    out = state.structured_output
    errors = validate(out, schema) if out is not None else ["no structured_output"]
    checks = g1(out, errors, case, duration_s, cost_usd)
    print(f"\nsession {state.id}: state={state.state} stop_reason={state.stop_reason}")
    print(
        f"duration {duration_s:.1f} s (wall {wall_s:.1f} s)  cost "
        + (f"${cost_usd:.4f}" if cost_usd is not None else "not reported by Naïve")
    )
    print("PROPOSAL " + ("VALID" if not errors else "INVALID: " + "; ".join(errors)))
    print(json.dumps(out, indent=2, ensure_ascii=False))
    for c in checks:
        print(f"  [{'x' if c['ok'] else ' '}] {c['check']}")
    passed = all(c["ok"] for c in checks)
    print("G1 " + ("PASS" if passed else "FAIL"))

    if args.fixture_out:
        fixture = {
            "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "session_id": state.id,
            "agent_id": agent_id,
            "studio_url": state.studio_url,
            "started_at": started.isoformat() if started else None,
            "finished_at": finished.isoformat() if finished else None,
            "duration_s": round(float(duration_s), 1),
            "cost_usd": cost_usd,
            "cost_source": "Naïve session consumed_micro_usd"
            if cost_reported
            else "not reported by Naïve (no consumed_micro_usd on the session)",
            "case_file": case,
            "schema": schema,
            "structured_output": out,
            "validation_errors": errors,
            "g1": {"passed": passed, "checks": checks},
            # FixtureNaive replays these three (adapters/naive.py)
            "request": body,
            "session": state.raw,
            "events": [e.raw for e in events],
        }
        args.fixture_out.parent.mkdir(parents=True, exist_ok=True)
        args.fixture_out.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n")
        print(f"fixture written: {args.fixture_out}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
