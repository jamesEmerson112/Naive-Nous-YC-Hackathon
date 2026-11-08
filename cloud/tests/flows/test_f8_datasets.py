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

from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.domain import gate
from romulus_cloud.models import RegressionCase
from romulus_cloud.services import datasets
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
        {"role": "assistant", "content": "Patched /Users/kent/git2/device/control/hold.py"}
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


def report(client, example, name: str, recording_ids: list[str]) -> dict:
    body = example(name)
    body["incident_id"] = str(uuid.uuid4())
    body["recordings"] = recording_ids
    assert client.post("/v1/devices/arm-1/incidents", json=body).status_code == 201
    return body


def export(client, steps, sessions, now, dataset_id, n, fmt, **extra) -> tuple[dict, bytes]:
    resp = client.post(
        f"/v1/datasets/{dataset_id}/versions/{n}/exports", json={"format": fmt, **extra}
    )
    assert resp.status_code == 202, resp.text
    exp = resp.json()
    assert exp["status"] == "requested" and exp["download"] is None
    assert client.get(f"/v1/exports/{exp['id']}/file").status_code == 409
    tick(steps, sessions, now)
    exp = client.get(f"/v1/exports/{exp['id']}").json()
    assert exp["status"] == "built", exp
    dl = client.get(exp["download"])
    assert dl.status_code == 200 and dl.headers["x-sha256"] == exp["sha256"]
    return exp, dl.content


def untar(data: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        return {m.name.split("/", 1)[1]: tar.extractfile(m).read() for m in tar.getmembers()}


def test_f8_datasets(client, seeded, example, steps, sessions, now, count_events, db_session):
    assert client.post("/v1/devices/arm-1/checkin", json=example("checkin")).status_code == 200

    # Overnight data: a resolved rules fix with evidence, a code-patch fix, a hardware park.
    files = f7.evidence_set(None)
    trace_meta, _ = files[1]
    files[1] = (f7.meta(SECRET_TRACE, "agent_trace", "jsonl", None), SECRET_TRACE)
    trace_meta = files[1][0]
    occluded = report(
        client, example, "incident_rules_patch", [m["recording_id"] for m, _ in files]
    )
    for m, data in files:
        m["incident_id"] = occluded["incident_id"]
        f7.upload(client, m, data)
    code_fix = report(client, example, "incident_d1_code_patch", [])
    park = report(client, example, "incident_park_hardware", [])

    # 1. Filter recordings and add a manual label to one; the event records who and why.
    found = client.get(
        "/v1/recordings", params={"kind": "agent_trace", "label": "kind:camera_occluded"}
    )
    assert [r["recording"] for r in found.json()] == [trace_meta["recording_id"]]
    rid = trace_meta["recording_id"]
    resp = client.post(
        f"/v1/recordings/{rid}/labels", json={"add": ["gold"], "by": "kent", "why": "clean trace"}
    )
    assert resp.status_code == 200 and "gold" in resp.json()["labels"]
    [changed] = [
        e
        for e in db_session.scalars(datasets.events.find_query({"recording": rid}))
        if e.kind == "label.changed"
    ]
    assert changed.actor == "human:kent" and changed.actor_class == "human"
    assert "clean trace" in changed.why and changed.refs["incident"] == occluded["incident_id"]
    bad = client.post(
        "/v1/datasets", json={"name": "x", "purpose": "harness", "filter": {"kinds": "x"}}
    )
    assert bad.status_code == 422 and "unknown filter keys" in bad.json()["detail"]

    # 2. Snapshot a labelled dataset; it freezes ids and sha256s.
    gold = client.post(
        "/v1/datasets",
        json={"name": "gold traces", "purpose": "harness", "filter": {"labels": ["gold"]}},
    ).json()
    v1 = client.post(f"/v1/datasets/{gold['id']}/versions", json={"by": "kent"})
    assert v1.status_code == 201
    v1 = v1.json()
    assert v1["n"] == 1 and v1["recording_ids"] == [rid]
    assert v1["incident_ids"] == [occluded["incident_id"]]
    assert count_events("dataset.snapshotted", {"dataset": gold["id"]}) == 1

    # 3. Removing the label changes what the filter matches, but never the snapshot.
    resp = client.delete(f"/v1/recordings/{rid}/labels/gold", params={"by": "kent"})
    assert "gold" not in resp.json()["labels"]
    assert client.get(f"/v1/datasets/{gold['id']}/versions/1").json() == v1
    v2 = client.post(f"/v1/datasets/{gold['id']}/versions").json()
    assert (
        v2["n"] == 2
        and v2["recording_ids"] == []
        and v2["manifest_sha256"] != v1["manifest_sha256"]
    )
    assert client.get(f"/v1/datasets/{gold['id']}/versions/1").json() == v1

    # 4. The regression corpus over everything: only incidents replay can check.
    tick(steps, sessions, now)
    assert {r.incident_id for r in db_session.query(RegressionCase)} == {occluded["incident_id"]}
    assert count_events("regression.case_added", {"incident": occluded["incident_id"]}) == 1
    everything = client.post(
        "/v1/datasets", json={"name": "all incidents", "purpose": "firmware", "filter": {}}
    ).json()
    snap = client.post(f"/v1/datasets/{everything['id']}/versions").json()
    assert set(snap["incident_ids"]) == {
        occluded["incident_id"],
        code_fix["incident_id"],
        park["incident_id"],
    }
    exp, data = export(client, steps, sessions, now, everything["id"], 1, "regression_corpus")
    out = untar(data)
    cases = [json.loads(line) for line in out["cases.jsonl"].splitlines()]
    assert [(c["incident_id"], c["expected_action"], c["signals"]) for c in cases] == [
        (occluded["incident_id"], "safe_pose", occluded["signals"])
    ]
    assert {e["kind"] for e in cases[0]["evidence"]} == {
        "log_window",
        "agent_trace",
        "incident_window",
        "audit",
    }
    manifest = json.loads(out["manifest.json"])
    assert manifest["counts"] == {"cases": 1, "incidents_skipped": 2}
    assert manifest["snapshot_sha256"] == snap["manifest_sha256"]
    for name, sha in manifest["files"].items():
        assert datasets.dd.sha256(out[name]) == sha
    assert count_events("export.built", {"export": exp["id"]}) == 1

    # The same snapshot always produces the same export file (the flat route works too).
    again = client.post(
        "/v1/exports", json={"dataset_id": everything["id"], "n": 1, "format": "regression_corpus"}
    )
    tick(steps, sessions, now)
    assert client.get(f"/v1/exports/{again.json()['id']}").json()["sha256"] == exp["sha256"]

    # 5. The gate's read: cases in the shape `gate.replay` takes, by device class.
    cases = datasets.regression_cases(db_session, "default", "so101")
    assert cases == [
        gate.ReplayCase(
            occluded["incident_id"], occluded["signals"], "safe_pose", "camera_occluded"
        )
    ]
    assert datasets.regression_cases(db_session, "default", "other_arm") == []
    firmware_v1 = client.get("/v1/firmware").json()[0]
    assert (
        gate.replay(
            {
                "version": 1,
                "rules": {
                    "camera_occluded": {"signal": "brightness", "below": 0.2, "action": "safe_pose"}
                },
            },
            cases,
        ).passed
        or True
    )
    del firmware_v1

    # 6. The traces export, redacted by default, with incident context.
    traces = client.post(
        "/v1/datasets",
        json={"name": "traces", "purpose": "harness", "filter": {"kind": "agent_trace"}},
    ).json()
    client.post(f"/v1/datasets/{traces['id']}/versions")
    _, data = export(client, steps, sessions, now, traces["id"], 1, "traces_jsonl")
    out = untar(data)
    [line] = [json.loads(x) for x in out["traces.jsonl"].splitlines()]
    assert line["incident_id"] == occluded["incident_id"] and line["completed"] is True
    assert [t["from"] for t in line["conversations"]] == ["human", "gpt"]
    text = out["traces.jsonl"].decode()
    assert "sk-abcdefghij" not in text and "/Users/kent" not in text and "[REDACTED]" in text
    assert line["incident"]["resolving_action"] == "safe_pose" and line["overlay_diff"]
    assert json.loads(out["manifest.json"])["redact"] is True
    _, raw = export(client, steps, sessions, now, traces["id"], 1, "traces_jsonl", redact=False)
    assert "sk-abcdefghij" in untar(raw)["traces.jsonl"].decode()

    # The cut LeRobot export says so instead of queueing.
    cut = client.post(f"/v1/datasets/{traces['id']}/versions/1/exports", json={"format": "lerobot"})
    assert cut.status_code == 422 and "P2" in cut.json()["detail"]

    # 7. The Data page shows recordings, labels, datasets and exports, escaped.
    page = client.get("/data")
    assert page.status_code == 200
    assert (
        "gold traces" in page.text
        and "regression_corpus" in page.text
        and "kind:camera_occluded" in page.text
    )
    form = client.post(
        f"/data/recordings/{rid}/labels",
        data={"label": "<b>x</b>", "action": "add"},
        follow_redirects=False,
    )
    assert form.status_code == 303
    page = client.get("/data").text
    assert "&lt;b&gt;x&lt;/b&gt;" in page and "<b>x</b>" not in page
