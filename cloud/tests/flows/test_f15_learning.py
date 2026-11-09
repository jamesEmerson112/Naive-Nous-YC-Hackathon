"""F15 · Fleet learning, the data spine (docs/recursive-self-improvement/design.md §2–3).

Every heal attempt becomes a labelled run whose verdict only a verifier gives, and the
standing `fleet-runs` dataset snapshots and exports them as Hermes trajectories on its own
(plain code, actor `agent:fleet-learning`), with `completed` = the verdict.

Real shape (`cloud/CLAUDE.md`, "Real device data"): every run report is built by the
unmodified bridge (`edge_bridge.agent_runs.build`) from a harness run folder
(`fake_healer.agent_run_01`), with the bench's `score.json` when there is one, and sent
through the device API. Pods are registered as `sensor_pod` before anything else.
"""

import io
import json
import tarfile
import uuid
from datetime import timedelta

import pytest

from edge_bridge import agent_runs as bridge_runs
from edge_bridge.fake_healer import T0 as POD_T0
from edge_bridge.fake_healer import agent_run_01
from edge_bridge.podd import read_transcript
from romulus_cloud import events
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.domain import runs as dr
from romulus_cloud.models import Dataset, Export
from romulus_cloud.services import datasets, runs
from romulus_cloud.worker import Step, run_tick

# What `arm/heal_test.py` writes once it has checked the bench itself.
BENCH_PASSED = {
    "passed": True,
    "running": "agent build",
    "running_build": "pod v1.1-1-dirty",
    "final_triage": [],
    "final_loops": {"check": "exit 0: 3 loops ok"},
    "checks": {"good_firmware_running": True, "triage_clean": True, "final_loops_ok": True},
}
BENCH_FAILED = BENCH_PASSED | {
    "passed": False,
    "final_loops": {"check": "exit 4: reflex gave up"},
    "checks": {"good_firmware_running": True, "triage_clean": True, "final_loops_ok": False},
}


@pytest.fixture
def learn(make_settings):
    return make_settings(learning_mode="data", learning_min_new=1)


@pytest.fixture
def steps(learn):
    store = build_blobstore(learn.blob_dir)
    return [
        Step("learning.tick", lambda s, n: runs.ensure_standing_dataset(s, n, learn)),
        Step(
            "datasets.run_exports",
            lambda s, n: datasets.run_exports(s, n, export_dir=learn.export_dir, store=store),
        ),
    ]


def tick(steps, sessions, now):
    assert run_tick(steps, sessions, now, {}) == {s.name: "ok" for s in steps}


def _pod(client, device_id, kind):
    resp = client.post(
        "/v1/devices",
        json={"id": device_id, "kind": kind, "device_class": "sensor_pod", "by": "test"},
    )
    assert resp.status_code == 201, resp.text


def _incident(client, example, device_id, kind="us_mismatch"):
    body = example("incident_rules_patch")
    body |= {"incident_id": str(uuid.uuid4()), "kind": kind, "overlay": None}
    resp = client.post(f"/v1/devices/{device_id}/incidents", json=body)
    assert resp.status_code == 201, resp.text
    return body["incident_id"]


def _run(client, tmp_path, device_id, incident_id, score=None, score_at=None):
    run = agent_run_01(tmp_path / device_id, POD_T0, ("p1", "p2", "p3"))
    body = bridge_runs.build(
        run.dir.name,
        read_transcript(run.path),
        score=score,
        score_at=score_at,
        incident_ids=[incident_id],
    )
    resp = client.put(f"/v1/devices/{device_id}/agent-runs/{run.dir.name}", json=body)
    assert resp.status_code == 200, resp.text
    return run.dir.name


def _files(export_dir_path):
    with tarfile.open(fileobj=io.BytesIO(export_dir_path.read_bytes())) as tar:
        return {
            m.name.split("/", 1)[1]: tar.extractfile(m).read()
            for m in tar.getmembers()
            if m.isfile()
        }


@pytest.fixture
def fleet(client, seeded, example, tmp_path):
    """Three pods, one heal run each: the bench passed one, failed one, and never checked the
    third (the device says fixed, nothing independent behind it). pod-2 is a sim pod."""
    _pod(client, "pod-1", "real")
    _pod(client, "pod-2", "sim")
    _pod(client, "pod-3", "real")
    ids = {d: _incident(client, example, d) for d in ("pod-1", "pod-2", "pod-3")}
    names = {
        "pod-1": _run(client, tmp_path, "pod-1", ids["pod-1"], BENCH_PASSED, POD_T0 + 300),
        "pod-2": _run(client, tmp_path, "pod-2", ids["pod-2"], BENCH_FAILED, POD_T0 + 300),
        "pod-3": _run(client, tmp_path, "pod-3", ids["pod-3"]),
    }
    return {"incidents": ids, "runs": names}


def test_labelled_runs_take_their_verdict_from_a_verifier(fleet, db_session, now):
    by = {r.device_id: r for r in runs.load(db_session, now=now)}
    assert (by["pod-1"].verdict, by["pod-1"].verifier) == (
        dr.PASS,
        "bench check passed (score.json)",
    )
    assert by["pod-2"].verdict == dr.FAIL and "reflex gave up" in by["pod-2"].verifier
    assert (by["pod-3"].verdict, by["pod-3"].verifier) == (
        dr.UNVERIFIED,
        "device says fixed; nothing checked it",
    )
    pod2 = by["pod-2"]
    assert pod2.device_kind == "sim" and pod2.device_class == "sensor_pod"
    assert pod2.id == f"run:pod-2/{fleet['runs']['pod-2']}" and pod2.kind == "us_mismatch"
    assert pod2.model == "qwen3.5:4b" and pod2.steps[0]["tool"] == "triage"
    assert pod2.steps[0]["thought"].startswith("The pod is crash-looping")
    assert pod2.final.startswith("Root cause: unaligned halfword store")


def test_the_standing_dataset_snapshots_and_exports_trajectories_on_its_own(
    fleet, client, sessions, db_session, steps, now, learn
):
    tick(steps, sessions, now)

    (ds,) = db_session.query(Dataset).filter(Dataset.name == "fleet-runs").all()
    assert ds.purpose == "harness" and ds.filter == {}
    written = events.find(db_session, {"dataset": ds.id})
    kinds = [e.kind for e in written]
    assert kinds[:3] == ["dataset.created", "dataset.snapshotted", "export.requested"]
    assert "export.built" in kinds
    # The digest counts these as agent work, not a person's.
    for e in written[:3]:
        assert (e.actor, e.actor_class) == ("agent:fleet-learning", "agent")

    (exp,) = db_session.query(Export).filter(Export.dataset_id == ds.id).all()
    assert exp.format == "trajectories" and exp.status == "built", exp.error
    files = _files(datasets.export_path(learn.export_dir, exp))
    assert set(files) == {"trajectories.jsonl", "DATASET_CARD.md", "manifest.json"}

    rows = [json.loads(line) for line in files["trajectories.jsonl"].splitlines()]
    completed = {r["metadata"]["device_id"]: r["completed"] for r in rows}
    assert completed == {"pod-1": True, "pod-2": False}  # the unverified run is no row
    row = next(r for r in rows if r["metadata"]["device_id"] == "pod-2")
    assert set(row) == {"conversations", "timestamp", "model", "completed", "metadata"}
    assert row["metadata"]["verdict"] == "fail" and row["metadata"]["sim_fleet"] is True
    assert row["model"] == "qwen3.5:4b"
    turns = row["conversations"]
    assert {t["from"] for t in turns} == {"system", "human", "gpt", "tool"}
    assert "<tools>" in turns[0]["value"] and '"name": "triage"' in turns[0]["value"]
    assert turns[2]["value"].startswith("<think>\nThe pod is crash-looping")
    assert "<tool_call>" in turns[2]["value"] and "<tool_response>" in turns[3]["value"]
    assert "reflex gave up" not in json.dumps(turns)  # the label never leaks into the text

    card = files["DATASET_CARD.md"].decode()
    assert "SIM FLEET" in card and "| unverified | 1 |" in card
    assert fleet["runs"]["pod-3"] in card and "device says fixed; nothing checked it" in card
    snap = client.get(f"/v1/datasets/{ds.id}").json()["versions"][0]
    assert snap["manifest_sha256"] in card
    manifest = json.loads(files["manifest.json"])
    assert manifest["counts"] == {
        "trajectories": 2,
        "runs_pass": 1,
        "runs_fail": 1,
        "runs_unverified": 1,
        "runs_excluded": 0,
    }
    # Deterministic from the frozen manifest: the same snapshot gives the same bytes.
    store = build_blobstore(learn.blob_dir)
    assert (
        datasets.build_export(db_session, exp, store)[0]
        == datasets.export_path(learn.export_dir, exp).read_bytes()
    )

    # Nothing new: the next tick snapshots nothing.
    tick(steps, sessions, now + timedelta(seconds=31))
    assert len(client.get(f"/v1/datasets/{ds.id}").json()["versions"]) == 1


def test_a_new_verdict_grows_the_dataset(fleet, client, sessions, db_session, steps, now, tmp_path):
    tick(steps, sessions, now)
    # The bench checks pod-3's fix after the fact; the bridge sends the newer report.
    _run(
        client, tmp_path / "again", "pod-3", fleet["incidents"]["pod-3"], BENCH_PASSED, POD_T0 + 900
    )
    tick(steps, sessions, now + timedelta(seconds=31))

    (ds,) = db_session.query(Dataset).filter(Dataset.name == "fleet-runs").all()
    assert len(client.get(f"/v1/datasets/{ds.id}").json()["versions"]) == 2
    exports = db_session.query(Export).filter(Export.dataset_id == ds.id).order_by(Export.n).all()
    assert [(e.n, e.status) for e in exports] == [(1, "built"), (2, "built")]


def test_off_does_nothing(db_session, make_settings, now, seeded):
    assert runs.ensure_standing_dataset(db_session, now, make_settings()) is None
    assert db_session.query(Dataset).count() == 0


def test_a_fake_arm_with_no_run_falls_back_to_its_incident(
    client, seeded, example, db_session, now
):
    """The fake arms send no agent runs: their incident's `agent` is the run, and the
    overlay holding on the device (the mark-good timer) is the verifier."""
    body = example("incident_rules_patch")
    assert client.post("/v1/devices/sim-1/incidents", json=body).status_code == 201
    (lr,) = runs.load(db_session, now=now)
    assert (lr.id, lr.verdict, lr.device_kind) == (
        f"inc:{body['incident_id']}",
        dr.UNVERIFIED,
        "sim",
    )
    assert lr.verifier == "device says resolved; nothing checked it"
    assert [s["args"]["action"] for s in lr.steps] == body["agent"]["actions"]

    held = example("overlay_status_held")
    assert client.post("/v1/devices/sim-1/overlays/ov-12", json=held).status_code == 204
    (lr,) = runs.load(db_session, now=now)
    assert (lr.verdict, lr.verifier) == (dr.PASS, "overlay ov-12 held on device (mark-good timer)")
