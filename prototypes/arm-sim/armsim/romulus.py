"""The arm host's reports to Romulus (contract v0.1, docs/cloud-platform-planning/cloud-platform-apis.md),
through fake_device's DeviceClient and on-disk Outbox, the same reference client the edge bridge uses.

  every 10 s     a check-in: health (failing while a critical incident is open), arm metrics
  on resolve     one IncidentReport per watchdog incident, sent once the story is over
                 (incidents are idempotent and can't be amended), plus a `log_window` recording:
                 the distance rows (measured and predicted) around the fault, as JSONL
  on hand-off    the same, unresolved with suspected_cause "hardware": Romulus opens a Naïve
                 work order (NAIVE_MODE=live) or logs it (fake)

Tonight the device is the MuJoCo arm (`arm-twin-1`, kind sim); tomorrow the real arm (`arm-1`, real).
"""
import hashlib
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fake_device.client import DeviceClient
from fake_device.outbox import Outbox
from romulus_contracts import CONTRACT_VERSION
from romulus_contracts.device import CheckIn
from romulus_contracts.incident import Agent, IncidentReport, Outcome
from romulus_contracts.telemetry import RecordingMeta

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "romulus:armsim")
WINDOW_BEFORE_S = 30.0


def utc(ts):
    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


class ArmReporter:
    def __init__(self, url, device_id, *, kind, state_dir, arm, dog, interval=10.0):
        self.url, self.device_id, self.kind = url, device_id, kind
        self.arm, self.dog, self.interval = arm, dog, interval
        self.client = DeviceClient(httpx.Client(base_url=url, timeout=5.0), device_id)
        self.outbox = Outbox(Path(state_dir) / device_id / "outbox")
        self.session = uuid.uuid4().hex[:8]          # incident ids: unique per host session
        self.rows = {}                               # incident id -> rows seen around it
        self.last = {"checkin": None, "error": None, "reported": []}
        self.running = False
        dog.on_open.append(self._opened)
        # A handed-off incident was already reported (unresolved, hardware); reports are idempotent,
        # so the later fix by a person isn't sent again.
        dog.on_resolve.append(lambda inc: None if inc.get("handed_off") else self._report(inc))
        dog.on_handoff.append(lambda inc: self._report(inc, handed_off=True))

    def start(self):
        self.running = True
        threading.Thread(target=self._loop, name="romulus", daemon=True).start()
        return self

    def stop(self):
        self.running = False

    # ---- check-ins ---------------------------------------------------------------------------
    def _loop(self):
        while self.running:
            self.checkin()
            self.drain()
            time.sleep(self.interval)

    def checkin(self):
        open_ = self.dog.open()
        health = "failing" if any(i["severity"] == "critical" for i in open_) else ("degraded" if open_ else "ok")
        last = next((r for r in reversed(self.arm.d_rows) if r.get("st") == "ok"), None)
        grasp = self.arm.grasp_check()
        metrics = {"arm_open_incidents": len(open_), "arm_status_alarms": sum(1 for s in self.arm.status() if s),
                   "arm_gripper": grasp["gripper"], "arm_holding": int(grasp["holding"])}
        if last:
            metrics["arm_distance_mm"] = float(last["mm"])
            if last.get("pred_mm") is not None:
                metrics["arm_predicted_mm"] = float(last["pred_mm"])
        detail = {"open_incidents": [{"id": i["id"], "kind": i["kind"], "severity": i["severity"]} for i in open_]}
        try:
            resp = self.client.checkin(CheckIn(running=None, health=health, metrics=metrics, health_detail=detail,
                                               contract_version=CONTRACT_VERSION, kind=self.kind, source="armsim"))
            self.last.update(checkin=time.time(), error=None, frozen=getattr(resp, "frozen", None))
        except (httpx.HTTPError, OSError, ValueError) as exc:
            self.last["error"] = f"{type(exc).__name__}: {exc}"
        return self.last

    def drain(self):
        try:
            return self.outbox.drain(self.client, datetime.now(timezone.utc))
        except (httpx.HTTPError, OSError) as exc:
            self.last["error"] = f"{type(exc).__name__}: {exc}"
            return 0

    # ---- incidents ---------------------------------------------------------------------------
    def _opened(self, inc):
        t0 = inc["opened_at"] - WINDOW_BEFORE_S
        self.rows[inc["id"]] = [r for r in list(self.arm.d_rows) if r["ts"] >= t0]

    def _report(self, inc, *, handed_off=False):
        now = time.time()
        seen = {r["ts"]: r for r in self.rows.pop(inc["id"], [])}
        for r in list(self.arm.d_rows):
            if r["ts"] >= inc["opened_at"] - WINDOW_BEFORE_S:
                seen[r["ts"]] = r
        rows = [seen[k] for k in sorted(seen)]
        incident_id = uuid.uuid5(NAMESPACE, f"{self.device_id}/{self.session}/{inc['id']}")
        recordings = []
        if rows:
            data = "".join(json.dumps({"log": "distance", **r}, separators=(",", ":")) + "\n" for r in rows).encode()
            meta = RecordingMeta(
                recording_id=uuid.uuid5(incident_id, "log_window"), kind="log_window", format="jsonl",
                sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
                started_at=utc(rows[0]["ts"]), ended_at=utc(rows[-1]["ts"]), running=None,
                incident_id=incident_id, streams=["distance"], labels=[inc["kind"]],
                contract_version=CONTRACT_VERSION, source="armsim")
            recordings.append((meta, data))
        fix = inc.get("fix") or ""
        if handed_off:
            outcome = Outcome(resolved=False, resolving_action="notify_human", suspected_cause="hardware")
        elif fix.startswith("pod healed"):
            outcome = Outcome(resolved=True, resolving_action="pod healer: fw_rollback", suspected_cause="software",
                              category="ops")
        elif "restart_sensor" in fix:
            outcome = Outcome(resolved=True, resolving_action="restart_sensor", suspected_cause="software",
                              category="ops")
        else:
            outcome = Outcome(resolved=True, resolving_action=fix.removeprefix("repair: ") or None,
                              suspected_cause="unknown", category="ops")
        agent = Agent(reasoning="; ".join(filter(None, [inc["summary"], *(inc.get("pod_summaries") or []),
                                                        inc.get("handoff_reason")])),
                      actions=inc.get("actions", []))
        ok_rows = [r for r in rows if r.get("st") == "ok"]
        report = IncidentReport(
            incident_id=incident_id, occurred_at=utc(inc["opened_at"]), kind=inc["kind"], summary=inc["summary"],
            running=None, agent=agent, outcome=outcome,
            signals={"arm_distance_mm": [float(r["mm"]) for r in ok_rows][-120:],
                     "arm_predicted_mm": [float(r["pred_mm"]) for r in ok_rows if r.get("pred_mm") is not None][-120:]},
            recordings=[m.recording_id for m, _ in recordings],
            log_tail=[{k: v for k, v in i.items() if k in ("id", "kind", "summary", "state", "fix", "opened_at", "resolved_at")}
                      for i in self.dog.incidents[-5:]],
            contract_version=CONTRACT_VERSION, source="armsim",
            evidence=inc.get("evidence"), hypotheses=inc.get("hypotheses"), next_steps=inc.get("next_steps"),
            pod_incidents=inc.get("pod_incidents"))
        self.outbox.add_incident(report)                 # the incident first, then its recordings
        for meta, data in recordings:
            self.outbox.add_recording(meta, data)
        sent = self.drain()
        self.last["reported"].append({"incident": inc["id"], "uuid": str(incident_id), "handed_off": handed_off,
                                      "sent_now": sent, "at": now})
        print(f"ROMULUS reported {inc['id']} as {incident_id} ({'hand-off' if handed_off else 'resolved'})", flush=True)
