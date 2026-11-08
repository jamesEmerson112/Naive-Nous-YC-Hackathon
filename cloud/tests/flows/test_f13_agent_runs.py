"""F13: heal-agent run reports (APIs §3.11) reach the incident page.

The edge bridge, unmodified, reads a healer `var/` and a harness run folder written the way
the real ones are (`edge_bridge.fake_healer`), and sends the incident and the run's report.
The incident page then leads with what the agent did: whether it left the pod fixed, its
requests for help, every image it flashed (bad ones flagged), its write-up and its steps.
Direct `PUT`s cover the upsert rules: newest wins, finished never goes back to running, and
a run reported before its incident is tied to it when the incident arrives."""

import html
import uuid
from datetime import UTC, datetime, timedelta

from edge_bridge.bridge import Bridge
from edge_bridge.fake_healer import (
    BAD_CRC,
    T0,
    FakeSocket,
    PoddVar,
    agent_run_01,
    scenario_01_fixed,
)
from fake_device.client import DeviceClient
from fake_device.outbox import Outbox
from romulus_cloud import events


def _text(resp) -> str:
    assert resp.status_code == 200, resp.text
    return html.unescape(resp.text)


def _pod(client):
    resp = client.post(
        "/v1/devices",
        json={"id": "pod-1", "kind": "real", "device_class": "sensor_pod", "by": "olir"},
    )
    assert resp.status_code == 201, resp.text


def test_the_agent_run_leads_the_incident_page(client, device_http, seeded, tmp_path):
    _pod(client)
    pod, sock = PoddVar(tmp_path / "pod"), FakeSocket()
    sock.last_boot = {
        "type": "E",
        "ev": "boot",
        "build": "pod v1.1 x",
        "crc": f"0x{BAD_CRC}",
        "ts": T0,
    }
    state = tmp_path / "bridge"
    bridge = Bridge(
        pod.paths,
        "pod-1",
        DeviceClient(device_http, "pod-1"),
        Outbox(state / "outbox"),
        sock,
        state / "state.json",
    )
    bridge.tick(T0)
    t = T0 + 60
    ids = scenario_01_fixed(pod, t)
    run = agent_run_01(pod.paths.harness_runs, t + 10, ids)
    result = bridge.tick(t + 200)
    assert result.runs == 1
    (incident_id,) = result.reported

    stored = client.get(f"/v1/devices/pod-1/agent-runs/{run.dir.name}").json()
    assert stored["incident_ids"] == [incident_id] and stored["status"] == "fixed"

    story = client.get(f"/v1/incidents/{incident_id}/story").json()
    kinds = [s["kind"] for s in story["steps"]]
    assert {"agent.run_started", "agent.help_requested", "agent.run_finished"} <= set(kinds)
    assert "agent: fixed" in story["chain"]

    page = _text(client.get(f"/incidents/{incident_id}"))
    assert "Fixed: the pod runs v1.1-1-dirty (5E6F7081)" in page
    assert "Requests for help" in page and "Needs a stronger model" in page
    assert "Why does frame_pack fault on the M0?" in page
    assert 'class="fw fw-bad"' in page and "flash failed" in page and "left running" in page
    assert "Write-up" in page and "Root cause: unaligned halfword store" in page
    assert "Every step" in page and "The pod is crash-looping" in page
    assert "pod-1 right now:" in page
    # Bookkeeping steps are one quiet line each; the story's order is kept.
    assert 'class="step step-quiet' in page
    positions = [page.index(s["why"]) for s in story["steps"]]
    assert positions == sorted(positions)

    timeline = _text(client.get("/"))
    assert "qwen3.5:4b: fixed" in timeline and "1 help request" in timeline
    assert "fixed by the agent" in timeline and "2 bad images" in timeline
    assert "fixed by the agent" in _text(client.get("/partials/overview"))


def _report(run_id, state="running", status="working", updated=None, **extra):
    started = datetime(2026, 11, 8, 10, 0, tzinfo=UTC)
    body = {
        "run_id": run_id,
        "state": state,
        "started_at": started.isoformat(),
        "updated_at": (updated or started + timedelta(seconds=30)).isoformat(),
        "agent": {"model": "qwen3.5:4b"},
        "outcome": {"status": status, "headline": f"{status} headline"},
        "steps": [{"n": 1, "action": "Ran triage", "effect": "No findings.", "ok": True}],
        "contract_version": "0.1",
    }
    if state == "finished":
        body["ended_at"] = (started + timedelta(minutes=3)).isoformat()
    return body | extra


def test_reports_are_upserts_newest_first_and_never_unfinish(client, seeded, db_session):
    _pod(client)
    url = "/v1/devices/pod-1/agent-runs/run-1"
    first = client.put(url, json=_report("run-1"))
    assert first.status_code == 200 and first.json() == {
        "run_id": "run-1",
        "created": True,
        "stored": True,
    }

    done = _report(
        "run-1",
        "finished",
        "broken",
        updated=datetime(2026, 11, 8, 10, 5, tzinfo=UTC),
        help_requests=[{"to": "person", "source": "agent", "summary": "reseat the ECHO wire"}],
    )
    assert client.put(url, json=done).json()["created"] is False
    # An older report, or one that would take it back to running, is ignored.
    assert client.put(url, json=_report("run-1")).json()["stored"] is False
    assert client.get(url).json()["state"] == "finished"

    verified = done | {
        "updated_at": "2026-11-08T10:09:00+00:00",
        "outcome": {
            "status": "fixed",
            "headline": "Fixed after the bench check",
            "verified_by": "bench",
        },
    }
    assert client.put(url, json=verified).json()["stored"] is True

    kinds = [e.kind for e in events.find(db_session, {"agent_run": "run-1"})]
    assert kinds == [
        "agent.run_started",
        "agent.help_requested",
        "agent.run_finished",
        "agent.outcome_changed",
    ]
    assert (
        client.put("/v1/devices/pod-1/agent-runs/other", json=_report("run-1")).status_code == 422
    )
    assert client.put("/v1/devices/nope/agent-runs/run-1", json=_report("run-1")).status_code == 404


def test_a_run_reported_before_its_incident_is_tied_to_it_when_it_arrives(
    client, seeded, example, db_session
):
    _pod(client)
    incident = example("incident_rules_patch")
    incident_id = incident["incident_id"]
    occurred = datetime.fromisoformat(incident["occurred_at"].replace("Z", "+00:00"))
    window = [
        (occurred - timedelta(minutes=5)).isoformat(),
        (occurred + timedelta(minutes=5)).isoformat(),
    ]
    body = _report("run-early", "finished", "fixed", incident_window=window)
    client.put("/v1/devices/pod-1/agent-runs/run-early", json=body)
    assert client.get("/v1/devices/pod-1/agent-runs/run-early").json()["incident_ids"] == []

    incident.pop("overlay", None)  # a pod reports no overlay
    resp = client.post("/v1/devices/pod-1/incidents", json=incident)
    assert resp.status_code == 201, resp.text
    assert client.get("/v1/devices/pod-1/agent-runs/run-early").json()["incident_ids"] == [
        incident_id
    ]
    assert "agent.run_linked" in [
        e.kind for e in events.find(db_session, {"incident": incident_id})
    ]
    assert "fixed headline" in _text(client.get(f"/incidents/{incident_id}"))


def test_a_run_with_no_incident_has_its_own_page(client, seeded):
    _pod(client)
    rid = f"20261108-031447-arm08-sim-{uuid.uuid4().hex[:6]}"
    client.put(
        f"/v1/devices/pod-1/agent-runs/{rid}", json=_report(rid, "finished", "fixed", sim=True)
    )
    timeline = _text(client.get("/"))
    assert "Agent runs with no incident" in timeline and f"/agent-runs/pod-1/{rid}" in timeline
    page = _text(client.get(f"/agent-runs/pod-1/{rid}"))
    assert "fixed headline" in page and "simulated rehearsal" in page
    partial = _text(client.get(f"/partials/agent-runs/pod-1/{rid}"))
    assert "<html" not in partial and "fixed headline" in partial
    assert client.get("/agent-runs/pod-1/missing").status_code == 404
