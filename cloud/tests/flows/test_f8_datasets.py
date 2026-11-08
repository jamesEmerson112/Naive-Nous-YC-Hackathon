"""F8 · Dataset snapshot and export (flows doc F8): label → snapshot → the snapshot stays
fixed after a label change → export the regression corpus and the agent traces.

Everything goes through the HTTP API except the worker steps, which run via `run_tick`.
Recording and incident payloads come from M4's F7 helpers.
"""

import importlib.util
import io
import json
import tarfile
import uuid
from pathlib import Path

import pytest

from romulus_cloud import events
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.domain import datasets as dd
from romulus_cloud.domain import gate
from romulus_cloud.ids import new_id
from romulus_cloud.models import RegressionCase, WorkOrder
from romulus_cloud.services import datasets
from romulus_cloud.services.seed import FIRMWARE_V1
from romulus_cloud.worker import Step, run_tick

_spec = importlib.util.spec_from_file_location(
    "f7_helpers", Path(__file__).with_name("test_f7_telemetry.py")
)
f7 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(f7)

SECRET_TRACE = (
    json.dumps(
        {"role": "user", "content": "Health check failed. OPENAI_API_KEY=sk-abcdefghij1234567890"}
    )
    + "\n"
    + json.dumps(
        {
            "role": "assistant",
            "content": "Patched /Users/kent/git2/device/control/hold.py",
            "tool_calls": [{"name": "http", "arguments": {"api_key": "plain words secret"}}],
        }
    )
    + "\n"
).encode()


@pytest.fixture
def steps(settings):
    store = build_blobstore(settings.blob_dir)
    return [
        Step("datasets.sync_regression_cases", datasets.sync_regression_cases),
        Step(
            "datasets.run_exports",
            lambda s, n: datasets.run_exports(s, n, export_dir=settings.export_dir, store=store),
        ),
    ]


def tick(steps, sessions, now):
    assert run_tick(steps, sessions, now, {}) == {s.name: "ok" for s in steps}


def report(client, example, name: str, recording_ids=(), **outcome) -> dict:
    body = example(name)
    body["incident_id"] = str(uuid.uuid4())
    body["recordings"] = list(recording_ids)
    if outcome:
        body["overlay"] = None
        body["outcome"] |= outcome
    assert client.post("/v1/devices/arm-1/incidents", json=body).status_code == 201
    return body


@pytest.fixture
def fleet(client, seeded, example, db_session):
    """Overnight data: a resolved rules fix with five pieces of evidence (one a trace with
    secrets in it), a code-patch fix, a hardware park, a fix by `park` and a fix that went
    to a work order."""
    assert client.post("/v1/devices/arm-1/checkin", json=example("checkin")).status_code == 200
    files = f7.evidence_set(None)
    files[1] = (f7.meta(SECRET_TRACE, "agent_trace", "jsonl", None), SECRET_TRACE)
    occluded = report(
        client, example, "incident_rules_patch", [m["recording_id"] for m, _ in files]
    )
    for m, data in files:
        m["incident_id"] = occluded["incident_id"]
        f7.upload(client, m, data)
    escalated = report(client, example, "incident_rules_patch", resolving_action="backoff_retry")
    db_session.add(
        WorkOrder(
            id=new_id("wo"),
            incident_id=escalated["incident_id"],
            device_id="arm-1",
            part="servo",
            max_usd=20,
        )
    )
    db_session.commit()
    return {
        "occluded": occluded,
        "trace": files[1][0],
        "files": files,
        "code_fix": report(client, example, "incident_d1_code_patch"),
        "park": report(client, example, "incident_park_hardware"),
        "parked_ok": report(client, example, "incident_rules_patch", resolving_action="park"),
        "escalated": escalated,
    }


def create(client, name: str, purpose: str, filter_: dict) -> dict:
    resp = client.post("/v1/datasets", json={"name": name, "purpose": purpose, "filter": filter_})
    assert resp.status_code == 201, resp.text
    return resp.json()


def snapshot(client, dataset_id: str) -> dict:
    resp = client.post(f"/v1/datasets/{dataset_id}/versions", json={"by": "kent"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def request(client, dataset_id, n, fmt, **extra) -> dict:
    resp = client.post(
        f"/v1/datasets/{dataset_id}/versions/{n}/exports", json={"format": fmt, **extra}
    )
    assert resp.status_code == 202, resp.text
    exp = resp.json()
    assert exp["status"] == "requested" and exp["download"] is None
    assert client.get(f"/v1/exports/{exp['id']}/file").status_code == 409
    return exp


def export(client, steps, sessions, now, dataset_id, n, fmt, **extra) -> tuple[dict, bytes]:
    exp = request(client, dataset_id, n, fmt, **extra)
    tick(steps, sessions, now)
    exp = client.get(f"/v1/exports/{exp['id']}").json()
    assert exp["status"] == "built", exp
    dl = client.get(exp["download"])
    assert dl.status_code == 200 and dl.headers["x-sha256"] == exp["sha256"]
    return exp, dl.content


def untar(data: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        return {m.name.split("/", 1)[1]: tar.extractfile(m).read() for m in tar.getmembers()}


def test_f8_label_then_snapshot_stays_fixed(client, fleet, db_session, count_events):
    occluded, rid = fleet["occluded"], fleet["trace"]["recording_id"]

    # Filter recordings and add a manual label; the event records who and why.
    found = client.get(
        "/v1/recordings", params={"kind": "agent_trace", "label": "kind:camera_occluded"}
    )
    assert [r["recording"] for r in found.json()] == [rid]
    resp = client.post(
        f"/v1/recordings/{rid}/labels", json={"add": ["gold"], "by": "kent", "why": "clean trace"}
    )
    assert resp.status_code == 200 and "gold" in resp.json()["labels"]
    [changed] = events.find(db_session, {"recording": rid}, kinds=["label.changed"])
    assert changed.actor == "human:kent" and changed.actor_class == "human"
    assert "clean trace" in changed.why and changed.refs["incident"] == occluded["incident_id"]
    bad = client.post(
        "/v1/datasets", json={"name": "x", "purpose": "harness", "filter": {"kinds": "x"}}
    )
    assert bad.status_code == 422 and "unknown filter keys" in bad.json()["detail"]

    # Snapshot: ids and sha256s frozen, with an event.
    gold = create(client, "gold traces", "harness", {"labels": ["gold"]})
    v1 = snapshot(client, gold["id"])
    assert v1["n"] == 1 and v1["recording_ids"] == [rid]
    assert v1["incident_ids"] == [occluded["incident_id"]]
    assert count_events("dataset.snapshotted", {"dataset": gold["id"]}) == 1

    # Removing the label changes what the filter matches, never the snapshot.
    resp = client.delete(f"/v1/recordings/{rid}/labels/gold", params={"by": "kent"})
    assert "gold" not in resp.json()["labels"]
    assert client.get(f"/v1/datasets/{gold['id']}/versions/1").json() == v1
    v2 = snapshot(client, gold["id"])
    assert v2["n"] == 2 and v2["recording_ids"] == []
    assert v2["manifest_sha256"] != v1["manifest_sha256"]
    assert client.get(f"/v1/datasets/{gold['id']}/versions/1").json() == v1


def test_f8_regression_corpus(client, fleet, steps, sessions, now, db_session, count_events):
    occluded = fleet["occluded"]

    # Only incidents a rule could fire join: not the code fix, the hardware park, the fix by
    # `park` or the one escalated to a work order. A second tick adds nothing.
    tick(steps, sessions, now)
    assert {r.incident_id for r in db_session.query(RegressionCase)} == {occluded["incident_id"]}
    tick(steps, sessions, now)
    assert count_events("regression.case_added") == 1

    everything = create(client, "all incidents", "firmware", {})
    snap = snapshot(client, everything["id"])
    assert set(snap["incident_ids"]) == {
        fleet[k]["incident_id"] for k in ("occluded", "code_fix", "park", "parked_ok", "escalated")
    }
    exp, data = export(client, steps, sessions, now, everything["id"], 1, "regression_corpus")
    out = untar(data)
    cases = [json.loads(line) for line in out["cases.jsonl"].splitlines()]
    assert [(c["incident_id"], c["expected_action"], c["signals"]) for c in cases] == [
        (occluded["incident_id"], "safe_pose", occluded["signals"])
    ]
    kinds = {e["kind"] for e in cases[0]["evidence"]}
    assert kinds == {"log_window", "agent_trace", "incident_window", "audit"}
    manifest = json.loads(out["manifest.json"])
    assert manifest["counts"] == {"cases": 1, "incidents_skipped": 4}
    assert manifest["snapshot_sha256"] == snap["manifest_sha256"]
    for name, sha in manifest["files"].items():
        assert dd.sha256(out[name]) == sha
    assert count_events("export.built", {"export": exp["id"]}) == 1

    # The same snapshot always produces the same export file (the flat route works too).
    again = client.post(
        "/v1/exports", json={"dataset_id": everything["id"], "n": 1, "format": "regression_corpus"}
    )
    assert again.status_code == 202
    tick(steps, sessions, now)
    assert client.get(f"/v1/exports/{again.json()['id']}").json()["sha256"] == exp["sha256"]

    # The gate's read: gate-shaped cases by device class, and a gate-safe replay.
    cases = datasets.regression_cases(db_session, "default", "so101")
    assert cases == [
        gate.ReplayCase(
            occluded["incident_id"], occluded["signals"], "safe_pose", "camera_occluded"
        )
    ]
    assert datasets.regression_cases(db_session, "default", "other_arm") == []
    assert dd.replay_corpus(FIRMWARE_V1, cases).passed
    assert not dd.replay_corpus(FIRMWARE_V1 | {"rules": {}}, cases).passed
    assert dd.replay_corpus(FIRMWARE_V1, []).passed  # an empty corpus regresses nothing

    # M7's seam: every blob a snapshot refers to is pinned.
    shas = {m["sha256"] for m, _ in fleet["files"]}
    assert shas <= datasets.pinned_blob_shas(db_session)


def test_f8_traces_export(client, fleet, steps, sessions, now, settings, count_events):
    occluded, trace = fleet["occluded"], fleet["trace"]
    traces = create(client, "traces", "harness", {"kind": "agent_trace"})
    snapshot(client, traces["id"])

    # Redacted by default, with the incident context frozen at snapshot time.
    first, data = export(client, steps, sessions, now, traces["id"], 1, "traces_jsonl")
    out = untar(data)
    [line] = [json.loads(x) for x in out["traces.jsonl"].splitlines()]
    assert line["incident_id"] == occluded["incident_id"] and line["completed"] is True
    assert [t["from"] for t in line["conversations"]] == ["human", "gpt"]
    text = out["traces.jsonl"].decode()
    for leak in ("sk-abcdefghij", "/Users/kent", "plain words secret"):
        assert leak not in text
    assert line["conversations"][1]["tool_calls"][0]["arguments"]["api_key"] == "[REDACTED]"
    assert line["incident"]["resolving_action"] == "safe_pose" and line["overlay_diff"]
    assert line["overlay_outcome"] == "active"
    assert json.loads(out["manifest.json"])["redact"] is True

    # The overlay is reverted after the snapshot: the export doesn't change.
    resp = client.post(
        "/v1/devices/arm-1/overlays/ov-12",
        json={"status": "reverted", "reason": "recurred", "occurred_at": "2026-11-07T23:00:00Z"},
    )
    assert resp.status_code == 204, resp.text
    second, data = export(client, steps, sessions, now, traces["id"], 1, "traces_jsonl")
    assert second["sha256"] == first["sha256"]
    assert json.loads(untar(data)["traces.jsonl"])["overlay_outcome"] == "active"

    # Unredacted only when asked.
    _, raw = export(client, steps, sessions, now, traces["id"], 1, "traces_jsonl", redact=False)
    assert "sk-abcdefghij" in untar(raw)["traces.jsonl"].decode()

    # A blob deleted after the snapshot fails the export with the reason; nothing is skipped.
    build_blobstore(settings.blob_dir).delete(trace["sha256"])
    exp = request(client, traces["id"], 1, "traces_jsonl")
    tick(steps, sessions, now)
    exp = client.get(f"/v1/exports/{exp['id']}").json()
    assert exp["status"] == "failed" and exp["download"] is None
    assert trace["recording_id"] in exp["error"] and "missing" in exp["error"]
    assert count_events("export.failed", {"export": exp["id"]}) == 1

    # The cut LeRobot export says so instead of queueing.
    cut = client.post(f"/v1/datasets/{traces['id']}/versions/1/exports", json={"format": "lerobot"})
    assert cut.status_code == 422 and "P2" in cut.json()["detail"]


def test_f8_data_page(client, fleet, db_session):
    rid = fleet["trace"]["recording_id"]
    gold = create(client, "gold traces", "harness", {"labels": ["kind:camera_occluded"]})
    page = client.get("/data")
    assert page.status_code == 200
    assert "gold traces" in page.text and "kind:camera_occluded" in page.text
    assert 'id="by-name"' in page.text and page.text.count('name="by"') >= 3

    # Forms record the name typed on the page, and every value renders escaped.
    form = client.post(
        f"/data/recordings/{rid}/labels",
        data={"label": "<b>x</b>", "action": "add", "by": "kent"},
        follow_redirects=False,
    )
    assert form.status_code == 303
    [changed] = events.find(db_session, {"recording": rid}, kinds=["label.changed"])
    assert changed.actor == "human:kent"
    snap = client.post(
        f"/data/datasets/{gold['id']}/versions", data={"by": "ana"}, follow_redirects=False
    )
    assert snap.status_code == 303
    [snapped] = events.find(db_session, {"dataset": gold["id"]}, kinds=["dataset.snapshotted"])
    assert snapped.actor == "human:ana"
    page = client.get("/data").text
    assert "&lt;b&gt;x&lt;/b&gt;" in page and "<b>x</b>" not in page
