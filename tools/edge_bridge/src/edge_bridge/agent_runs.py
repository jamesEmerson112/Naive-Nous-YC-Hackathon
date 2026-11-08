"""Turn one heal-agent run (`firmware/harness/runs/<run>/`) into the cloud's agent run report.

Pure: no I/O, no clock. The harness writes `transcript.jsonl` as it goes (`run_start`, a
`model_turn` per model reply, a `tool_call` per call, `harness_note`s, then `device_check`
and `run_end`), and `arm/heal_test.py` adds `score.json` once it has checked the bench
itself. `build()` reads those records as they are, so a report can be built at any point,
mid-run included. What it makes (APIs §3.11, `romulus_contracts.AgentRunReport`):

- **Write-up**: labelled paragraphs (the problem, what the agent looked at, what it noticed,
  what it changed, the result), the agent's own final report, and every step: the model's
  thinking before it, the action in words, and what came back (`effect`).
- **Firmware**: every image the run touched, in order: the one running when it started,
  each flash and rollback (verified, failed its checks, refused, not flashed), and each
  build. An image is flagged `bad` when the fault appeared on it, it failed its flash
  checks, triage still found a fault on it, the arm's job failed on it, or the agent marked
  it bad. `error` flags a call that failed before anything was written.
- **Help requests**: `notify_human` (a person) and `ask_expert` (a stronger model) as the
  agent asked, plus one from the harness when a finished run left the pod unfixed.
- **Outcome**: `working` while the run goes on; then `fixed`, `broken` or `unknown`, from
  the best evidence there is: the bench's own checks (`score.json`), else the device check
  the harness made at the end, else the agent's last triage after its last flash.

Times in the report are UTC ISO strings; the harness writes epoch seconds (`t`).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

THOUGHT_MAX = 2400
EXCERPT_MAX = 900
TEXT_MAX = 12_000
ARG_MAX = 600
LINK_BEFORE_S = 120.0
"""Without podd's incident ids, an incident that occurred this long before the run started
(or during it) is taken as the one it worked on. The harness wakes about 10 s after podd
opens an incident (watch mode), and a heal test starts its agent within a minute of it;
a longer look-back catches the previous run's incident too."""
SCORE_WAIT_S = 600.0
"""How long after a heal test's agent run its `score.json` may still come."""

STOP_TEXT = {
    "end_turn": "the agent finished and wrote its report",
    "context_full": "the model's context window filled up",
    "max_turns": "the run used all its turns",
    "time_limit": "the run hit its time limit",
    "model_error": "the model stopped answering (an API or network error)",
    "max_tokens": "the model's reply hit its length limit",
    "refusal": "the model refused to continue",
}
UNFINISHED = {"context_full", "max_turns", "time_limit", "model_error", "max_tokens", "refusal"}
FLASH_TOOLS = ("fw_flash", "fw_rollback")


def iso(t: float | None) -> str | None:
    return None if t is None else datetime.fromtimestamp(float(t), UTC).isoformat()


def expects_score(run_id: str, records: list[dict[str, Any]]) -> bool:
    """Heal-test runs (`arm/heal_test.py`, run ids `…-arm08-…`) get a `score.json` after the
    agent stops; watch and evaluation runs don't."""
    label = str((records[0] if records else {}).get("label") or "")
    return label.startswith("arm") or "-arm" in run_id


def podd_incidents(records: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """podd's open incidents when the run started (`run_start.podd_incidents`), or None for
    a transcript from before the harness recorded them."""
    start = records[0] if records and records[0].get("type") == "run_start" else {}
    found = start.get("podd_incidents")
    return found if isinstance(found, list) else None


def span(records: list[dict[str, Any]]) -> tuple[float, float | None]:
    """When the run started, and when it ended (None while it runs)."""
    start = float(records[0]["t"])
    end = next((float(r["t"]) for r in reversed(records) if r.get("type") == "run_end"), None)
    return start, end


# -- the report ----------------------------------------------------------------------------


def build(
    run_id: str,
    records: list[dict[str, Any]],
    *,
    score: dict[str, Any] | None = None,
    score_at: float | None = None,
    incident_ids: list[str] | None = None,
) -> dict[str, Any]:
    """The report for one run, as JSON (an `AgentRunReport` body). `records` is the whole
    transcript so far, oldest first, starting with `run_start`."""
    start = records[0]
    started, ended = span(records)
    finished = ended is not None
    end = next((r for r in records if r.get("type") == "run_end"), None) or {}
    check = next((r for r in reversed(records) if r.get("type") == "device_check"), None)
    sim = "sim" in run_id.split("-") or bool((score or {}).get("sim"))

    steps = _steps(records)
    fw = _firmware(start, records, steps)
    problem = _problem(start)
    outcome = _outcome(finished, steps, fw, check, score, start)
    stop = end.get("stop") if finished else None
    report_text = _final_text(records)
    help_requests = _help(steps, finished, stop, outcome, start)
    updated = max(float(records[-1].get("t", started)), float(score_at or 0))

    body: dict[str, Any] = {
        "run_id": run_id,
        "state": "finished" if finished else "running",
        "started_at": iso(started),
        "ended_at": iso(ended),
        "updated_at": iso(updated),
        "agent": {
            "model": start.get("model"),
            "driver": start.get("driver"),
            "label": start.get("label"),
            "arm": start.get("arm"),
            "tools": len(start.get("tools") or []),
        },
        "sim": sim,
        "incident_ids": list(incident_ids or []),
        "problem": problem,
        "outcome": outcome,
        "writeup": {
            "summary": _summary(problem, steps, fw, outcome, stop, report_text),
            "report": _clip(report_text, TEXT_MAX),
            "stop": stop,
            "stop_text": _stop_text(stop, records),
        },
        "steps": steps,
        "firmware": fw,
        "help_requests": help_requests,
        "stats": _stats(records, end, steps),
        "verification": _verification(score) if score else None,
        "source": "firmware/harness transcript.jsonl" + (" + score.json" if score else ""),
    }
    return body


# -- steps ---------------------------------------------------------------------------------


def _steps(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    turns = {r.get("turn"): r for r in records if r.get("type") == "model_turn"}
    out: list[dict[str, Any]] = []
    thought_given: set[Any] = set()
    flashed = False
    for r in records:
        if r.get("type") != "tool_call":
            continue
        turn = r.get("turn")
        mt = turns.get(turn) or {}
        thought, said = "", ""
        if turn not in thought_given:
            thought_given.add(turn)
            thought = _clip_middle(str(mt.get("thinking") or "").strip(), THOUGHT_MAX)
            said = _clip(str(mt.get("text") or "").strip(), 1500)
        name = str(r.get("name"))
        args = r.get("input") if isinstance(r.get("input"), dict) else {}
        obj = result_obj(r.get("result"))
        allowed = r.get("gate") == "allow"
        if not allowed:
            effect, ok = f"Refused by the harness: {r.get('reason') or 'no reason given'}", False
        elif obj is None:
            effect, ok = _excerpt(r.get("result"), 300) or "No result.", not r.get("is_error")
        else:
            effect, ok = _effect(name, args, obj)
            if r.get("is_error") and ok:
                ok = False
        phase = _phase(name, r.get("risk"), args, flashed)
        if allowed and name in FLASH_TOOLS and isinstance(obj, dict) and obj.get("result"):
            flashed = True
        out.append(
            {
                "n": len(out) + 1,
                "turn": turn,
                "at": iso(r.get("t")),
                "phase": phase,
                "tool": name,
                "risk": r.get("risk"),
                "gate": r.get("gate"),
                "action": _action(name, args),
                "effect": effect,
                "ok": bool(ok),
                "thought": thought,
                "said": said,
                "args": _short_args(args),
                "excerpt": _excerpt(r.get("result"), EXCERPT_MAX),
                "seconds": r.get("seconds"),
                "_obj": obj,
            }
        )
    return out


def _phase(name: str, risk: Any, args: dict[str, Any], flashed: bool) -> str:
    if name in ("notify_human", "ask_expert"):
        return "escalate"
    if name == "note":
        return "note"
    if name in FLASH_TOOLS:
        return "stabilize" if _is_known_good_ref(name, args) else "repair"
    if risk == "code" or name in ("fw_build", "src_edit", "src_write", "src_commit"):
        return "repair"
    if name == "arm_run" or (flashed and risk == "read"):
        return "verify"
    return "investigate"


def _is_known_good_ref(name: str, args: dict[str, Any]) -> bool:
    ref = str(args.get("image") if name == "fw_flash" else args.get("to") or "known-good")
    return ref.lower().replace("_", "-") in ("known-good", "v1.0")


def _action(name: str, a: dict[str, Any]) -> str:
    g = a.get
    labels = {
        "triage": "Ran triage on the live telemetry",
        "incidents": "Checked podd's open incidents",
        "status": "Checked the pod's status",
        "telemetry_recent": f"Read recent telemetry ({g('kind', '?')}"
        + (f", {g('seconds')} s" if g("seconds") else "")
        + ")",
        "telemetry_stats": "Read telemetry statistics"
        + (f" ({g('seconds')} s)" if g("seconds") else ""),
        "events": "Read the pod's event log",
        "log_search": f"Searched the {g('log', '')} log".replace("  ", " "),
        "chip_status": "Read which image is on the chip (SWD)",
        "chip_clock": "Read the chip's clock tree (SWD)",
        "chip_read": f"Read chip memory ({g('symbol') or g('address') or '?'})",
        "chip_regs": f"Read {g('peripheral', 'peripheral')} registers",
        "chip_halt_sample": "Sampled where the CPU spends its time",
        "chip_reset": "Reset the MCU",
        "fw_images": "Listed the firmware images",
        "fw_symbolize": "Symbolized a code address",
        "fw_mark": f"Marked image {g('image') or g('crc') or '?'} as {g('status', '?')}",
        "src_status": "Checked the source workspace",
        "src_list": "Listed the source files",
        "src_read": f"Read {g('path', 'a source file')}"
        + (f" lines {g('start')}–{g('end')}" if g("start") or g("end") else ""),
        "src_search": f"Searched the source for `{_clip(str(g('pattern', '')), 80)}`",
        "src_log": "Read the source history",
        "src_show": f"Read commit {g('rev', '')}".strip(),
        "src_diff": "Diffed the source"
        + (f" {g('rev_a')}..{g('rev_b') or 'working tree'}" if g("rev_a") else "")
        + (f" ({g('path')})" if g("path") else ""),
        "src_edit": f"Edited {g('path', 'a source file')}",
        "src_write": f"Wrote {g('path', 'a source file')}",
        "src_revert": "Reverted uncommitted source changes",
        "src_restore": f"Restored the source to {g('rev', '?')}",
        "src_commit": "Committed the source change",
        "fw_build": "Built the firmware from the workspace",
        "fw_flash": f"Flashed {g('image', '?')}",
        "fw_rollback": f"Rolled back to {g('to') or 'known-good'}",
        "pod_cmd": f"Sent `{g('command', '?')}` to the pod",
        "link_set_baud": f"Set podd's baud to {g('baud', '?')}",
        "link_baud_scan": "Scanned baud rates on the serial link",
        "link_raw_capture": "Captured raw serial bytes",
        "arm_run": "Ran the robot arm's job",
        "arm_state": "Read the arm's joints",
        "arm_report": "Read the arm's motion report",
        "notify_human": "Asked a person for help",
        "ask_expert": "Asked a stronger model for help",
        "note": "Wrote down a conclusion",
        "system_info": "Read the system briefing",
    }
    return labels.get(name, name.replace("_", " ").capitalize())


# -- what each tool call showed ------------------------------------------------------------


def _effect(name: str, a: dict[str, Any], o: Any) -> tuple[str, bool]:
    """(what came back, in a sentence; whether the call did what it was asked)."""
    if not isinstance(o, dict):
        return _excerpt(json.dumps(o), 300), True
    if o.get("ok") is False and name not in FLASH_TOOLS and name != "fw_build":
        return f"Error: {_clip(str(o.get('error') or 'failed'), 400)}", False
    fn = _EFFECTS.get(name)
    if fn is None:
        return _excerpt(json.dumps(o, ensure_ascii=False), 300), True
    try:
        return fn(a, o)
    except Exception:  # an unexpected result shape: show it raw rather than fail the report
        return _excerpt(json.dumps(o, ensure_ascii=False), 300), True


def _findings_text(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "No findings: triage sees nothing wrong."
    parts = [f"{f.get('kind')} ({f.get('severity')}): {f.get('summary')}" for f in findings[:4]]
    more = f" (+{len(findings) - 4} more)" if len(findings) > 4 else ""
    return f"{len(findings)} finding{'s' if len(findings) != 1 else ''}: " + "; ".join(parts) + more


def _triage(a, o):
    return _findings_text(o.get("findings") or []), True


def _incidents(a, o):
    opened = o.get("open") or []
    if not opened:
        return "No open incidents.", True
    return "Open: " + "; ".join(f"{i.get('kind')} ({i.get('severity')})" for i in opened), True


def _status(a, o):
    link = (o.get("link") or {}).get("state")
    h = o.get("last_health") or {}
    opened = o.get("open_incidents") or []
    bits = [f"link {link}"]
    if h:
        bits.append(f"mode {h.get('mode')}, ultrasonic {h.get('us')}, accel {h.get('acc')}")
    bits.append(
        f"{len(opened)} open incident{'s' if len(opened) != 1 else ''}"
        + (f" ({', '.join(str(i.get('kind')) for i in opened)})" if opened else "")
    )
    return "; ".join(bits).capitalize() + ".", True


def _range(values: list[float], unit: str) -> str:
    if not values:
        return "none"
    lo, hi = min(values), max(values)
    return f"{_num(lo)} {unit}" if lo == hi else f"{_num(lo)}–{_num(hi)} {unit}"


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}"


def _counts(values: list[Any]) -> str:
    c: dict[str, int] = {}
    for v in values:
        c[str(v)] = c.get(str(v), 0) + 1
    return ", ".join(f"{k} ×{n}" for k, n in sorted(c.items(), key=lambda kv: -kv[1]))


def _telemetry_recent(a, o):
    rows = o.get("rows") or []
    kind = str(a.get("kind") or (rows[0].get("type") if rows else "?"))
    if not rows:
        return f"No {kind} records in that window.", True
    if kind == "D":
        mm = [float(r["mm"]) for r in rows if _isnum(r.get("mm"))]
        us = [float(r["us"]) for r in rows if _isnum(r.get("us"))]
        return (
            f"{len(rows)} distance readings: {_range(mm, 'mm')}, echo {_range(us, 'µs')}; "
            f"status {_counts([r.get('st') for r in rows])}."
        ), True
    if kind == "A":
        return f"{len(rows)} accelerometer readings.", True
    if kind == "H":
        h = rows[-1]
        return (
            f"{len(rows)} health records; latest: mode {h.get('mode')}, ultrasonic {h.get('us')}, "
            f"accel {h.get('acc')}, boots {h.get('boots')}, crashes {h.get('crashes')}."
        ), True
    if kind == "E":
        evs = [r.get("ev") for r in rows if r.get("ev")]
        return f"{len(rows)} events: {_counts(evs) or 'none named'}.", True
    return f"{len(rows)} {kind} records.", True


def _telemetry_stats(a, o):
    d = o.get("distance") or {}
    mm = d.get("mm_ok") or {}
    parts = []
    if d:
        parts.append(
            f"distance: {d.get('n')} readings at {d.get('rate_hz')} Hz"
            + (f", mean {mm.get('mean')} mm ({mm.get('min')}–{mm.get('max')})" if mm else "")
            + (
                f", status {', '.join(f'{k} ×{v}' for k, v in (d.get('status') or {}).items())}"
                if d.get("status")
                else ""
            )
        )
    acc = (o.get("accel") or {}).get("magnitude_mg") or {}
    if acc.get("mean") is not None:
        parts.append(f"|a| mean {acc.get('mean')} mg")
    return ("; ".join(parts) or "No statistics.").capitalize() + ".", True


def _chip_status(a, o):
    img = o.get("image") or {}
    state = "running" if o.get("running") else "NOT running"
    reg = "" if o.get("in_registry", True) else ", not in the image registry"
    return (
        f"The chip has {img.get('version') or o.get('build_id')} ({o.get('crc')}, "
        f"{img.get('status') or 'unknown status'}{reg}); CPU {state}."
    ), True


def _fw_images(a, o):
    imgs = o.get("images") or []
    good = [f"{m.get('version')} ({m.get('crc')})" for m in imgs if m.get("status") == "known-good"]
    bad = [f"{m.get('version')} ({m.get('crc')})" for m in imgs if m.get("status") == "bad"]
    text = f"{len(imgs)} images; known-good: {', '.join(good) or 'none'}"
    if bad:
        text += f"; marked bad: {', '.join(bad)}"
    return text + ".", True


def _src_read(a, o):
    return (
        f"Read {o.get('path')} lines {o.get('start')}–{o.get('end')} of {o.get('total_lines')}.",
        True,
    )


def _src_search(a, o):
    hits = o.get("hits") or []
    first = f"; first: {hits[0].get('path')}:{hits[0].get('line')}" if hits else ""
    return f"{len(hits)} hit{'s' if len(hits) != 1 else ''}{first}.", True


def _diffstat(diff: str) -> str:
    files = re.findall(r"^\+\+\+ b/(.+)$", diff, re.M)
    plus = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    minus = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    return f"{', '.join(dict.fromkeys(files)) or 'no files'} (+{plus} −{minus})"


def _src_diff(a, o):
    diff = str(o.get("diff") or "")
    return ("No differences." if not diff.strip() else f"The diff touches {_diffstat(diff)}."), True


def _src_edit(a, o):
    diff = str(o.get("diff") or "")
    return (f"Changed {_diffstat(diff)}." if diff else f"Edited {o.get('path')}."), True


def _src_commit(a, o):
    return f"Committed {o.get('commit')} ({o.get('describe')}).", True


def _fw_build(a, o):
    img = o.get("image") or {}
    if o.get("ok"):
        warn = len(o.get("warnings") or [])
        return (
            f"Built {img.get('crc')} ({img.get('version')}), a candidate image"
            + (f"; {warn} warning{'s' if warn != 1 else ''}" if warn else "")
            + "."
        ), True
    errs = o.get("errors") or [o.get("error") or "unknown error"]
    return f"The build failed: {_clip(str(errs[0]), 300)}", False


def _flash(a, o):
    img = o.get("image") or {}
    checks = o.get("checks") or []
    passed = sum(1 for c in checks if c.get("ok"))
    if o.get("result") is None:
        return f"Nothing was flashed: {_clip(str(o.get('error') or 'failed'), 300)}", False
    if o.get("ok"):
        after = o.get("triage_after")
        tri = ""
        if isinstance(after, list):
            tri = "; triage after: " + ("clear" if not after else ", ".join(_kinds(after)))
        return (
            f"Flashed {img.get('version')} ({img.get('crc')}, {img.get('status')}) and verified it "
            f"({passed}/{len(checks)} checks){tri}."
        ), True
    failed = [str(c.get("check")) for c in checks if not c.get("ok")]
    rb = o.get("rollback") or {}
    rb_img = rb.get("image") or {}
    back = (
        f" It rolled back automatically to {rb_img.get('version')} ({rb_img.get('crc')})."
        if rb.get("ok")
        else ""
    )
    return (
        f"Flashing {img.get('version') or a.get('image')} ({img.get('crc') or '?'}) failed"
        + (
            f" its checks: {', '.join(failed)}"
            if failed
            else f": {o.get('error') or o.get('result')}"
        )
        + "."
        + back
    ), False


def _arm_run(a, o):
    if o.get("passed"):
        return f"The arm's job passed: {o.get('verdict')}.", True
    return f"The arm's job failed (exit {o.get('exit')}): {o.get('verdict')}.", True


def _arm_report(a, o):
    return f"Job {o.get('run_id')}: exit {o.get('exit')}, {o.get('verdict')}.", True


def _arm_state(a, o):
    joints = o.get("joints") or {}
    torque = any((j or {}).get("torque_on") for j in joints.values())
    return f"Read {len(joints)} joints; torque {'on' if torque else 'off'}.", True


def _pod_cmd(a, o):
    resp = o.get("responses") or []
    first = json.dumps({k: v for k, v in resp[0].items() if k != "ts"}) if resp else "no response"
    state = "ok" if o.get("ok") else f"failed ({o.get('error')})"
    return f"`{o.get('command') or a.get('command')}` → {state}: {_clip(first, 240)}", bool(
        o.get("ok")
    )


def _notify(a, o):
    return (
        f"Asked a person ({a.get('severity', 'info')}): {_clip(str(a.get('summary') or ''), 300)}",
        True,
    )


def _ask_expert(a, o):
    return f"Asked for a stronger model: {_clip(str(a.get('question') or ''), 300)}", True


def _note(a, o):
    return _clip(str(a.get("text") or ""), 600), True


def _fw_mark(a, o):
    return f"Marked {a.get('image') or a.get('crc')} as {a.get('status')}.", True


_EFFECTS = {
    "triage": _triage,
    "incidents": _incidents,
    "status": _status,
    "telemetry_recent": _telemetry_recent,
    "telemetry_stats": _telemetry_stats,
    "chip_status": _chip_status,
    "fw_images": _fw_images,
    "src_read": _src_read,
    "src_search": _src_search,
    "src_diff": _src_diff,
    "src_edit": _src_edit,
    "src_commit": _src_commit,
    "fw_build": _fw_build,
    "fw_flash": _flash,
    "fw_rollback": _flash,
    "arm_run": _arm_run,
    "arm_report": _arm_report,
    "arm_state": _arm_state,
    "pod_cmd": _pod_cmd,
    "notify_human": _notify,
    "ask_expert": _ask_expert,
    "note": _note,
    "fw_mark": _fw_mark,
}


# -- firmware --------------------------------------------------------------------------------


def _image(img: dict[str, Any] | None, ref: Any = None) -> dict[str, Any]:
    img = img or {}
    out = {k: img.get(k) for k in ("crc", "version", "status", "origin", "build_id") if img.get(k)}
    parts = str(out.get("build_id") or "").split()
    if "version" not in out and len(parts) > 1 and parts[1].startswith("v"):
        out["version"] = parts[1]  # `pod v1.8 2026-…`
    if not out and ref:
        out["ref"] = str(ref)
    return out


def _label(img: dict[str, Any]) -> str:
    if img.get("version") or img.get("crc"):
        return f"{img.get('version') or '?'} ({img.get('crc') or '?'})"
    return str(img.get("ref") or "an image")


def _firmware(
    start: dict[str, Any], records: list[dict[str, Any]], steps: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    had_problem = bool(start.get("podd_incidents")) or "incident" in str(start.get("kickoff") or "")

    chip = start.get("chip_at_start")
    if not chip:  # an older transcript: the first chip_status read before any flash
        for s in steps:
            if s["tool"] in FLASH_TOOLS:
                break
            if s["tool"] == "chip_status" and isinstance(s.get("_obj"), dict):
                o = s["_obj"]
                chip = {
                    "crc": o.get("crc"),
                    "build_id": o.get("build_id"),
                    **(o.get("image") or {}),
                }
                break
    if chip:
        img = _image(chip)
        bad = had_problem and img.get("status") != "known-good"
        current = {
            "at": iso(start.get("t")),
            "step": None,
            "event": "running_at_start",
            "image": img,
            "bad": bad,
            "error": False,
            "flashed": False,
            "verdict": "Running when the fault appeared" if bad else "Running when the run started",
            "evidence": [],
        }
        out.append(current)

    for s in steps:
        o = s.get("_obj")
        name = s["tool"]
        if name in FLASH_TOOLS:
            ref = (
                s["args"].get("image")
                if name == "fw_flash"
                else (s["args"].get("to") or "known-good")
            )
            base = {
                "at": s["at"],
                "step": s["n"],
                "tool": name,
                "evidence": [],
                "reason": s["args"].get("reason"),
            }
            if s["gate"] != "allow":
                out.append(
                    base
                    | {
                        "event": "refused",
                        "image": _image(None, ref),
                        "bad": False,
                        "error": False,
                        "flashed": False,
                        "verdict": s["effect"],
                    }
                )
                continue
            if not isinstance(o, dict) or o.get("result") is None:
                err = (o or {}).get("error") if isinstance(o, dict) else s.get("excerpt")
                out.append(
                    base
                    | {
                        "event": "not_flashed",
                        "image": _image(None, ref),
                        "bad": False,
                        "error": True,
                        "flashed": False,
                        "verdict": f"Not flashed: {_clip(str(err or 'the tool failed'), 300)}",
                    }
                )
                continue
            img = _image(o.get("image"), ref)
            checks = [
                {
                    "check": c.get("check"),
                    "ok": bool(c.get("ok")),
                    "detail": _short(c.get("detail")),
                }
                for c in o.get("checks") or []
            ]
            if o.get("ok"):
                after = o.get("triage_after")
                still = _kinds(after) if isinstance(after, list) else []
                entry = base | {
                    "event": "rolled_back" if name == "fw_rollback" else "flashed",
                    "image": img,
                    "checks": checks,
                    "flashed": True,
                    "error": False,
                    "bad": bool(still) or img.get("status") == "bad",
                    "verdict": (
                        f"Flashed and verified, but triage still finds {', '.join(still)}"
                        if still
                        else f"Flashed and verified ({_passed(checks)} checks)"
                        + ("; triage clear" if isinstance(after, list) else "")
                    ),
                }
                out.append(entry)
                current = entry
            else:
                failed = [c["check"] for c in checks if not c["ok"]]
                entry = base | {
                    "event": "flash_failed",
                    "image": img,
                    "checks": checks,
                    "flashed": True,
                    "error": True,
                    "bad": True,
                    "verdict": "Failed its flash checks"
                    + (
                        f": {', '.join(map(str, failed))}"
                        if failed
                        else f": {o.get('error') or o.get('result')}"
                    ),
                }
                out.append(entry)
                current = entry
                rb = o.get("rollback") or {}
                if rb.get("ok") and rb.get("image"):
                    current = {
                        "at": s["at"],
                        "step": s["n"],
                        "tool": "auto-rollback",
                        "event": "auto_rolled_back",
                        "image": _image(rb.get("image")),
                        "flashed": True,
                        "error": False,
                        "bad": False,
                        "verdict": "Put back automatically after the failed flash",
                        "evidence": [],
                    }
                    out.append(current)
        elif name == "fw_build" and s["gate"] == "allow" and isinstance(o, dict):
            if o.get("ok"):
                out.append(
                    {
                        "at": s["at"],
                        "step": s["n"],
                        "tool": name,
                        "event": "built",
                        "image": _image(o.get("image")),
                        "flashed": False,
                        "error": False,
                        "bad": False,
                        "verdict": "Built from the agent's source changes (candidate, unflashed)",
                        "evidence": [],
                    }
                )
            else:
                out.append(
                    {
                        "at": s["at"],
                        "step": s["n"],
                        "tool": name,
                        "event": "build_failed",
                        "image": {},
                        "flashed": False,
                        "error": True,
                        "bad": False,
                        "verdict": s["effect"],
                        "evidence": [],
                    }
                )
        elif name == "fw_mark" and s["gate"] == "allow" and str(s["args"].get("status")) == "bad":
            ref = str(s["args"].get("image") or s["args"].get("crc") or "").upper()
            for e in out:
                if ref and ref in str(e["image"].get("crc") or "").upper():
                    e["bad"] = True
                    e["evidence"].append(f"Step {s['n']}: the agent marked it bad")
        elif current is not None and s["gate"] == "allow" and isinstance(o, dict):
            # Later checks say whether the image on the chip works.
            if name == "triage":
                kinds = _kinds(o.get("findings") or [])
                current["evidence"].append(
                    f"Step {s['n']}: triage " + (f"found {', '.join(kinds)}" if kinds else "clear")
                )
                if kinds and current["event"] != "running_at_start":
                    current["bad"] = True
            elif name == "arm_run":
                passed = bool(o.get("passed"))
                current["evidence"].append(
                    f"Step {s['n']}: the arm's job "
                    + ("passed" if passed else f"failed (exit {o.get('exit')}: {o.get('verdict')})")
                )
                if not passed:
                    current["bad"] = True

    for e in out:
        e["label"] = _label(e["image"])
    if out:
        running = [
            e
            for e in out
            if e["event"]
            in ("running_at_start", "flashed", "rolled_back", "flash_failed", "auto_rolled_back")
        ]
        if running:
            running[-1]["running_at_end"] = True
    for s in steps:
        s.pop("_obj", None)
    return out


# -- outcome, help, write-up -----------------------------------------------------------------


def _outcome(finished, steps, fw, check, score, start) -> dict[str, Any]:
    on_chip = next((e for e in reversed(fw) if e.get("running_at_end")), None)
    running = (on_chip or {}).get("image") or {}
    if not finished:
        last = steps[-1] if steps else None
        return {
            "status": "working",
            "headline": f"The agent is working on it: step {last['n']}, {last['action'].lower()}."
            if last
            else "The agent has started.",
            "reasons": [],
            "caveats": [],
            "verified_by": None,
            "running": running,
        }
    reasons: list[str] = []
    bad: list[str] = []  # the reasons that make it broken, first one in the headline
    caveats: list[str] = []
    status = "unknown"
    verified_by = None
    first = fw[0] if fw and fw[0]["event"] == "running_at_start" else None
    faulty_crc = first["image"].get("crc") if first and first.get("bad") else None

    if score and isinstance(score.get("checks"), dict):
        c = score["checks"]
        verified_by = "bench"
        good_fw = bool(
            c.get("good_firmware_running", c.get("known_good_running"))
        )  # older heal tests
        clean = bool(c.get("triage_clean"))
        loops = c.get("final_loops_ok")
        arm_ok = bool(loops) if loops is not None else bool(c.get("gate_passes_now"))
        what = score.get("running") or (
            "known-good" if c.get("known_good_running") else "an unrecognised image"
        )
        line = f"The chip runs {what} ({score.get('running_build') or '?'})"
        reasons.append(line)
        if not good_fw:
            bad.append(line + ": neither known-good nor a verified build of the agent's")
        triage = score.get("final_triage") or []
        if clean:
            reasons.append("Triage is clean on the bench")
        else:
            reasons.append(f"Triage still finds {', '.join(map(str, triage))}")
            bad.append(reasons[-1])
        fl = score.get("final_loops") or {}
        if loops is not None:
            reasons.append(
                "The arm's final loops: "
                + str(fl.get("check") or fl.get("verdict") or ("passed" if loops else "failed"))
            )
        else:
            reasons.append("The arm's start gate " + ("passes" if arm_ok else "refuses") + " now")
        if not arm_ok:
            bad.append(reasons[-1])
        if score.get("error"):
            caveats.append(f"The bench test stopped early: {score['error']}")
        status = "broken" if bad else "fixed"
    elif check and (check.get("chip") or check.get("findings") is not None):
        verified_by = "device_check"
        findings = check.get("findings")
        chip = check.get("chip") or {}
        if chip:
            running = _image(chip) or running
            reasons.append(
                f"The chip runs {_label(running)}, {chip.get('status') or 'unknown status'}"
            )
            if chip.get("running") is False:
                bad.append("The chip's CPU is not running")
            if chip.get("status") == "bad":
                bad.append(f"{_label(running)} is marked bad")
            if faulty_crc and chip.get("crc") == faulty_crc:
                bad.append(f"It still runs {_label(running)}, the image the fault appeared on")
        if findings is None:
            caveats.append("Triage couldn't be run at the end")
        elif findings:
            reasons.append(
                "Triage at the end finds "
                + "; ".join(f"{f.get('kind')} ({f.get('severity')})" for f in findings)
            )
            bad.append(reasons[-1])
        else:
            reasons.append("Triage at the end finds nothing wrong")
        arm = _last_arm_after_flash(steps)
        if arm is False:
            reasons.append("The arm's job failed after the last flash")
            bad.append(reasons[-1])
        elif arm is True:
            reasons.append("The arm's job passed after the last flash")
        elif start.get("arm"):
            caveats.append("The agent never re-ran the arm's job on the firmware it left")
        status = "broken" if bad else "fixed" if findings == [] else "unknown"
    else:
        verified_by = "agent"
        tri = _last_triage_after_flash(steps)
        if tri is not None:
            reasons.append(
                "The agent's last triage "
                + ("found nothing wrong" if not tri else f"found {', '.join(tri)}")
            )
            if tri:
                bad.append(reasons[-1])
        if running:
            reasons.append(f"Last known on the chip: {_label(running)}")
            if faulty_crc and running.get("crc") == faulty_crc:
                bad.append(f"It still runs {_label(running)}, the image the fault appeared on")
        arm = _last_arm_after_flash(steps)
        if arm is not None:
            reasons.append(
                "The arm's job " + ("passed" if arm else "failed") + " after the last flash"
            )
            if arm is False:
                bad.append(reasons[-1])
        caveats.append(
            "No check of the pod after the run: this is from the agent's own last checks"
        )
        status = "broken" if bad else "fixed" if tri == [] else "unknown"

    if status == "fixed":
        headline = "Fixed" + (
            f": the pod runs {_label(running)}"
            + (f", {running.get('status')}" if running.get("status") else "")
            if running
            else ""
        )
    elif status == "broken":
        headline = f"Still broken: {bad[0][0].lower()}{bad[0][1:]}"
    else:
        headline = "Unknown: the run left no check that says whether the pod works"
    return {
        "status": status,
        "headline": headline.rstrip(".") + ".",
        "reasons": reasons,
        "problems": bad,
        "caveats": caveats,
        "verified_by": verified_by,
        "running": running,
    }


def _last_flash_index(steps) -> int:
    idx = -1
    for i, s in enumerate(steps):
        if s["tool"] in FLASH_TOOLS and s["gate"] == "allow" and s["ok"]:
            idx = i
    return idx


def _last_arm_after_flash(steps) -> bool | None:
    after = steps[_last_flash_index(steps) + 1 :]
    runs = [s for s in after if s["tool"] == "arm_run" and s["gate"] == "allow"]
    return None if not runs else runs[-1]["effect"].startswith("The arm's job passed")


def _last_triage_after_flash(steps) -> list[str] | None:
    i = _last_flash_index(steps)
    after = steps[i + 1 :] if i >= 0 else steps
    tri = [s for s in after if s["tool"] == "triage" and s["gate"] == "allow"]
    if tri:
        eff = tri[-1]["effect"]
        return (
            []
            if eff.startswith("No findings")
            else re.findall(r"(\w+) \((?:critical|warn|info)\)", eff)
        )
    if i >= 0 and "triage after: clear" in steps[i]["effect"]:
        return []
    return None


def _help(steps, finished, stop, outcome, start) -> list[dict[str, Any]]:
    out = []
    for s in steps:
        if s["gate"] != "allow" or not s["ok"]:
            continue
        a = s["args"]
        if s["tool"] == "notify_human":
            out.append(
                {
                    "at": s["at"],
                    "step": s["n"],
                    "to": "person",
                    "source": "agent",
                    "severity": a.get("severity"),
                    "summary": a.get("summary") or "",
                    "detail": a.get("actions") or "",
                }
            )
        elif s["tool"] == "ask_expert":
            out.append(
                {
                    "at": s["at"],
                    "step": s["n"],
                    "to": "expert_model",
                    "source": "agent",
                    "summary": a.get("question") or "",
                    "detail": a.get("tried") or "",
                }
            )
    if finished and outcome["status"] != "fixed":
        model = start.get("model") or "the local model"
        if stop in UNFINISHED:
            out.append(
                {
                    "at": None,
                    "step": None,
                    "to": "expert_model",
                    "source": "harness",
                    "summary": f"{model} stopped before the pod was fixed: "
                    f"{STOP_TEXT.get(stop, stop)}.",
                    "detail": "A stronger model or a person should pick up from this run's steps.",
                }
            )
        else:
            out.append(
                {
                    "at": None,
                    "step": None,
                    "to": "person",
                    "source": "harness",
                    "summary": f"{model} ended its run without confirming the pod works "
                    f"({outcome['status'].replace('_', ' ')}).",
                    "detail": "Check the pod and the agent's report below.",
                }
            )
    return out


def _problem(start: dict[str, Any]) -> dict[str, Any]:
    incidents = start.get("podd_incidents")
    kickoff = str(start.get("kickoff") or "")
    if not isinstance(incidents, list):  # older transcripts: the JSON lines in the kickoff
        incidents = []
        for line in kickoff.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("kind"):
                    incidents.append({k: rec.get(k) for k in ("kind", "severity", "summary")})
    first = kickoff.split("\n", 1)[0].strip()
    if incidents:
        text = "; ".join(
            f"podd opened {i.get('kind')} ({i.get('severity')}): {i.get('summary')}"
            for i in incidents
        )
        if first.startswith("The robot arm"):
            text = f"{first} {text}"
    else:
        text = first or "The run started without a stated problem."
    return {"text": _clip(text, 1500), "incidents": incidents, "kickoff": _clip(kickoff, 4000)}


def _summary(problem, steps, fw, outcome, stop, report_text) -> list[dict[str, str]]:
    out = [{"label": "Problem", "text": problem["text"]}]

    reads = [s for s in steps if s["phase"] in ("investigate", "verify") and s["gate"] == "allow"]
    looked: list[str] = []
    paths: list[str] = []
    for s in reads:
        t = s["tool"]
        if t in ("src_read", "src_edit") and s["args"].get("path"):
            paths.append(str(s["args"]["path"]))
            continue
        word = {
            "triage": "triage",
            "telemetry_recent": "live telemetry",
            "telemetry_stats": "telemetry statistics",
            "chip_status": "the chip over SWD",
            "fw_images": "the image registry",
            "src_diff": "the release diff",
            "src_search": "source searches",
            "src_log": "the source history",
            "events": "the event log",
            "status": "the pod's status",
            "incidents": "podd's incidents",
            "arm_run": "the arm's job",
            "arm_report": "the arm's motion reports",
            "arm_state": "the arm's joints",
            "pod_cmd": "pod commands",
        }.get(t, t.replace("_", " "))
        if word not in looked:
            looked.append(word)
    if paths:
        looked.append("source files " + ", ".join(dict.fromkeys(paths)))
    if reads:
        out.append(
            {
                "label": "Investigation",
                "text": f"{len(reads)} read-only step{'s' if len(reads) != 1 else ''}: "
                f"{', '.join(looked)}.",
            }
        )

    noticed = []
    tri = next((s for s in steps if s["tool"] == "triage" and s["gate"] == "allow"), None)
    if tri:
        noticed.append(f"First triage (step {tri['n']}): {tri['effect']}")
    for s in steps:
        if s["gate"] != "allow":
            continue
        reason = s["args"].get("reason") if s["tool"] in FLASH_TOOLS else None
        if s["tool"] == "note" and s["args"].get("text"):
            noticed.append(f"Step {s['n']}, note: {_clip(str(s['args']['text']), 500)}")
        elif reason:
            noticed.append(f"Step {s['n']}, why it flashed: {_clip(str(reason), 400)}")
    if noticed:
        out.append({"label": "Noticed", "text": " ".join(dict.fromkeys(noticed))})

    acts = [
        (e["step"], f"Step {e['step']}: {_fw_line(e)}")
        for e in fw
        if e["event"] != "running_at_start"
    ]
    acts += [
        (s["n"], f"Step {s['n']}: {s['action'].lower()}: {s['effect']}")
        for s in steps
        if s["tool"] in ("src_edit", "src_write", "src_commit") and s["gate"] == "allow"
    ]
    if acts:
        out.append(
            {
                "label": "Actions",
                "text": " ".join(text for _, text in sorted(acts, key=lambda a: a[0] or 0)),
            }
        )

    result = outcome["headline"]
    if outcome.get("reasons"):
        result += " " + "; ".join(outcome["reasons"]) + "."
    if outcome.get("caveats"):
        result += " Note: " + "; ".join(outcome["caveats"]) + "."
    if stop:
        result += f" The run ended because {STOP_TEXT.get(stop, stop)}."
    out.append({"label": "Result", "text": result})
    if report_text.strip():
        out.append({"label": "Agent's report", "text": _clip(report_text.strip(), 600)})
    return out


def _fw_line(e: dict[str, Any]) -> str:
    what = {
        "flashed": "flashed",
        "rolled_back": "rolled back to",
        "flash_failed": "tried to flash",
        "auto_rolled_back": "auto-rollback to",
        "refused": "was refused a flash of",
        "not_flashed": "couldn't flash",
        "built": "built",
        "build_failed": "a build failed",
    }.get(e["event"], e["event"])
    label = e["label"] if e["event"] != "build_failed" else ""
    return (
        f"{what} {label}".strip() + f" — {e['verdict']}" + (" [BAD]" if e.get("bad") else "") + "."
    )


def _final_text(records) -> str:
    return next(
        (
            str(r.get("text") or "")
            for r in reversed(records)
            if r.get("type") == "model_turn" and str(r.get("text") or "").strip()
        ),
        "",
    )


def _stop_text(stop, records) -> str | None:
    if stop is None:
        return None
    text = STOP_TEXT.get(stop, stop)
    err = next((r.get("error") for r in reversed(records) if r.get("type") == "model_error"), None)
    return f"{text} ({err})" if err else text


def _stats(records, end, steps) -> dict[str, Any]:
    usage = end.get("usage") or {}
    if not end:
        usage = {}
        for r in records:
            if r.get("type") == "model_turn":
                for k, v in (r.get("usage") or {}).items():
                    if isinstance(v, int | float):
                        usage[k] = usage.get(k, 0) + v
    started = float(records[0]["t"])
    return {
        "turns": end.get("turns", len({s["turn"] for s in steps})),
        "tool_calls": end.get("tool_calls", sum(1 for s in steps if s["gate"] == "allow")),
        "refused": sum(1 for s in steps if s["gate"] != "allow"),
        "flashes": end.get(
            "flashes_allowed",
            sum(1 for s in steps if s["tool"] in FLASH_TOOLS and s["gate"] == "allow"),
        ),
        "arm_runs": end.get(
            "arm_runs_allowed",
            sum(1 for s in steps if s["tool"] == "arm_run" and s["gate"] == "allow"),
        ),
        "tokens_in": usage.get("input_tokens"),
        "tokens_out": usage.get("output_tokens"),
        "seconds": end.get("seconds", round(float(records[-1].get("t", started)) - started, 1)),
    }


def _verification(score: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "passed",
        "checks",
        "running",
        "running_build",
        "final_loops",
        "final_triage",
        "motion_check",
        "found_exact_defect",
        "forward_fix",
        "error",
        "gate_now",
    )
    return {k: score.get(k) for k in keys if k in score}


# -- helpers ---------------------------------------------------------------------------------


def result_obj(text: Any) -> Any:
    """The JSON value at the start of a tool result (the harness may append a note after it)."""
    if not isinstance(text, str):
        return text
    s = text.lstrip()
    if not s.startswith(("{", "[")):
        return None
    try:
        return json.JSONDecoder().raw_decode(s)[0]
    except ValueError:
        return None


def _passed(checks: list[dict[str, Any]]) -> str:
    return f"{sum(1 for c in checks if c.get('ok'))}/{len(checks)}"


def _kinds(findings: list[Any]) -> list[str]:
    return [str(f.get("kind")) for f in findings if isinstance(f, dict)]


def _isnum(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _short(v: Any) -> Any:
    if isinstance(v, str):
        return _clip(v, 200)
    if isinstance(v, dict | list):
        return _clip(json.dumps(v), 200)
    return v


def _short_args(args: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in args.items():
        out[k] = _clip(v, ARG_MAX) if isinstance(v, str) else v
    return out


def _excerpt(text: Any, limit: int) -> str:
    if text is None:
        return ""
    return _clip(str(text), limit)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _clip_middle(text: str, limit: int) -> str:
    """Long thinking keeps its start and, mostly, its end (where the decision is)."""
    if len(text) <= limit:
        return text
    head = limit // 4
    tail = limit - head - 5
    return text[:head].rstrip() + " … " + text[-tail:].lstrip()
