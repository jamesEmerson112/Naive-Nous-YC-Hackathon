"""F7 · Telemetry sync (flows doc F7): recordings arrive as metadata, then bytes, and show
up as evidence in the incident's story.

Everything goes through the HTTP API. The evidence files are small and synthetic: a real
MCAP log window, a JSONL agent trace, a placeholder tar labeled `lerobot_v3_tar`, a
plain-text log and an audit JSON (CLOUD-PLATFORM.md §7: logs may arrive as text first).
"""

import hashlib
import io
import json
import tarfile
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from mcap.reader import make_reader
from mcap.writer import Writer
from sqlalchemy import func, select

from fake_device import recordings as fake_recordings
from fake_device.client import DeviceClient
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.models import Blob, Label, MetricSample, Recording
from romulus_contracts import Running

AT = datetime(2026, 11, 7, 22, 2, 11, tzinfo=UTC)  # incident_rules_patch.json occurred_at


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def mcap_log_window() -> bytes:
    buf = io.BytesIO()
    writer = Writer(buf)
    writer.start(profile="", library="test_f7")
    schema = writer.register_schema(
        name="foxglove.Log",
        encoding="jsonschema",
        data=json.dumps({"type": "object", "properties": {"message": {"type": "string"}}}).encode(),
    )
    channel = writer.register_channel(topic="/log", message_encoding="json", schema_id=schema)
    start = int((AT - timedelta(seconds=30)).timestamp() * 1e9)
    for i, line in enumerate(["S safe_pose: camera dark", "E 17 brightness 0.12", "S resumed"]):
        t = start + i * 1_000_000_000
        writer.add_message(
            channel, log_time=t, publish_time=t, data=json.dumps({"message": line}).encode()
        )
    writer.finish()
    return buf.getvalue()


def jsonl_trace() -> bytes:
    msgs = [
        {"role": "user", "content": "Health check failed; diagnose and fix."},
        {"role": "tool", "name": "safe_pose", "content": "ok"},
        {"role": "assistant", "content": "Occlusion, not hardware. Raised the threshold."},
    ]
    return "".join(json.dumps(m) + "\n" for m in msgs).encode()


def placeholder_tar() -> bytes:
    info = json.dumps({"placeholder": True, "codebase_version": "v3.0"}).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        entry = tarfile.TarInfo("meta/info.json")
        entry.size = len(info)
        tar.addfile(entry, io.BytesIO(info))
    return buf.getvalue()


def text_log() -> bytes:
    return b"T 22:01:41 brightness=0.41\nT 22:01:44 brightness=0.18\nS safe_pose camera dark\n"


def audit_json() -> bytes:
    return json.dumps({"firmware": 1, "harness": 1, "disk_free_gb": 21.4}).encode()


def meta(data: bytes, kind: str, fmt: str, incident_id: str | None, **extra) -> dict:
    return {
        "recording_id": str(uuid.uuid4()),
        "kind": kind,
        "format": fmt,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "started_at": _iso(AT - timedelta(seconds=30)),
        "ended_at": _iso(AT + timedelta(seconds=30)),
        "running": {"firmware": 1, "harness": 1},
        "overlay_id": None,
        "incident_id": incident_id,
        "streams": [],
        "labels": [],
        **extra,
    }


def evidence_set(incident_id: str | None) -> list[tuple[dict, bytes]]:
    files = [
        (mcap_log_window(), "log_window", "mcap", {"streams": ["/log"], "overlay_id": "ov-12"}),
        (jsonl_trace(), "agent_trace", "jsonl", {"labels": ["fix-config"]}),
        (placeholder_tar(), "incident_window", "lerobot_v3_tar", {"schema_version": "0.1"}),
        (text_log(), "log_window", "text", {}),
        (audit_json(), "audit", "json", {}),
    ]
    return [(meta(data, k, f, incident_id, **extra), data) for data, k, f, extra in files]


def put_blob(client, m: dict, data: bytes, sha: str | None = None):
    headers = {"X-Sha256": sha or m["sha256"], "Content-Type": "application/octet-stream"}
    return client.put(f"/v1/recordings/{m['recording_id']}/blob", content=data, headers=headers)


def upload(client, m: dict, data: bytes) -> dict:
    resp = client.post("/v1/devices/arm-1/recordings", json=m)
    assert resp.status_code == 201, resp.text
    if resp.json()["upload"] == "needed":
        assert put_blob(client, m, data).status_code == 204
    return resp.json()


def incident(example, recordings: list[str] | None = None) -> dict:
    body = example("incident_rules_patch")
    body["incident_id"] = str(uuid.uuid4())
    body["recordings"] = recordings or []
    return body


def story(client, incident_id: str) -> dict:
    resp = client.get(f"/v1/incidents/{incident_id}/story")
    assert resp.status_code == 200, resp.text
    return resp.json()


def blob_rows(db_session, *datas: bytes) -> int:
    """Blob rows for these files (the seed's packages are blobs too)."""
    shas = [hashlib.sha256(d).hexdigest() for d in datas]
    return db_session.scalar(select(func.count()).select_from(Blob).where(Blob.sha256.in_(shas)))


def labels(client, recording_id: str) -> list[str]:
    return client.get(f"/v1/recordings/{recording_id}").json()["labels"]


@pytest.fixture
def arm(client, seeded, example):
    """F2's starting point: arm-1 has checked in."""
    assert client.post("/v1/devices/arm-1/checkin", json=example("checkin")).status_code == 200
    return "arm-1"


def test_f7_incident_with_all_evidence(client, arm, example, count_events, db_session, settings):
    files = evidence_set(None)
    inc = incident(example, [m["recording_id"] for m, _ in files])
    assert client.post("/v1/devices/arm-1/incidents", json=inc).status_code == 201
    for m, _ in files:
        m["incident_id"] = inc["incident_id"]

    for m, data in files:
        assert upload(client, m, data) == {"recording_id": m["recording_id"], "upload": "needed"}

    # Resending metadata and bytes changes nothing.
    for m, data in files:
        again = client.post("/v1/devices/arm-1/recordings", json=m)
        assert again.status_code == 200
        assert again.json() == {"recording_id": m["recording_id"], "upload": "exists"}
        assert put_blob(client, m, data).status_code == 204
        assert count_events("recording.received", {"recording": m["recording_id"]}) == 1
        assert count_events("recording.blob_stored", {"recording": m["recording_id"]}) == 1
    assert db_session.scalar(select(func.count()).select_from(Recording)) == 5
    assert blob_rows(db_session, *(d for _, d in files)) == 5

    # Each recording is indexed with device, versions, overlay, incident and time range.
    log_meta, log_bytes = files[0]
    rec = client.get(f"/v1/recordings/{log_meta['recording_id']}").json()
    assert rec["device_id"] == "arm-1" and rec["incident_id"] == inc["incident_id"]
    assert (rec["firmware"], rec["harness"], rec["overlay_id"]) == (1, 1, "ov-12")
    assert rec["started_at"].startswith("2026-11-07T22:01:41")
    assert rec["ended_at"].startswith("2026-11-07T22:02:41")
    assert rec["kind"] == "log_window" and rec["format"] == "mcap" and rec["stored"] is True

    # The story shows every piece of evidence: kind, format, size and a download link.
    s = story(client, inc["incident_id"])
    assert s["chain"].startswith("camera_occluded → agent acted → overlay ov-12 → evidence: ")
    assert s["chain"].count("evidence: ") == 5
    assert "evidence: audit" in s["chain"] and "stored" not in s["chain"]
    by_id = {e["recording"]: e for e in s["evidence"]}
    assert set(by_id) == {m["recording_id"] for m, _ in files}
    for m, data in files:
        ev = by_id[m["recording_id"]]
        assert (ev["kind"], ev["format"], ev["size_bytes"]) == (m["kind"], m["format"], len(data))
        assert ev["stored"] is True
        assert ev["download"] == f"/v1/recordings/{m['recording_id']}/blob"
    received = [st for st in s["steps"] if st["kind"] == "recording.received"]
    assert len(received) == 5 and all("evidence" in st for st in received)
    assert all(st["why"] for st in s["steps"])
    stored = [st for st in s["steps"] if st["kind"] == "recording.blob_stored"]
    assert all("Download: /v1/recordings/" in st["line"] for st in stored)

    # Download returns the identical bytes, and the MCAP file still reads as MCAP.
    for m, data in files:
        dl = client.get(f"/v1/recordings/{m['recording_id']}/blob")
        assert dl.status_code == 200 and dl.content == data
        assert dl.headers["x-sha256"] == m["sha256"]
        assert dl.headers["x-content-type-options"] == "nosniff"
    messages = list(make_reader(io.BytesIO(log_bytes)).iter_messages())
    assert [ch.topic for _, ch, _ in messages] == ["/log"] * 3

    # Automatic labels from the incident and the recording, plus the device's suggestion.
    trace_labels = labels(client, files[1][0]["recording_id"])
    for want in [
        "kind:camera_occluded",
        "outcome:resolved",
        "cause:software",
        "healer:fix-config",
        "firmware:v1",
        "fix-config",
    ]:
        assert want in trace_labels
    auto = db_session.scalars(
        select(Label.source).where(Label.target_id == files[1][0]["recording_id"])
    ).all()
    assert set(auto) == {"auto", "agent"}

    # Blobs live on disk under their hash, once each.
    store = build_blobstore(settings.blob_dir)
    assert all(store.exists(m["sha256"]) for m, _ in files)


def test_f7_wrong_sha_keeps_nothing(client, arm, example, db_session, settings):
    inc = incident(example)
    client.post("/v1/devices/arm-1/incidents", json=inc)
    data = jsonl_trace()
    m = meta(data, "agent_trace", "jsonl", inc["incident_id"])
    assert client.post("/v1/devices/arm-1/recordings", json=m).status_code == 201

    # Bytes that don't match the declared sha256: a clear 409, nothing stored.
    bad = put_blob(client, m, data + b"corrupted")
    assert bad.status_code == 409 and bad.json()["error"] == "sha256_mismatch"
    assert "Nothing was stored" in bad.json()["detail"]
    # A header that disagrees with the metadata is refused too.
    wrong_header = put_blob(client, m, data, sha="0" * 64)
    assert wrong_header.status_code == 409 and wrong_header.json()["error"] == "sha256_mismatch"

    assert blob_rows(db_session, data, data + b"corrupted") == 0
    store = build_blobstore(settings.blob_dir)
    assert not store.exists(m["sha256"])
    missing = client.get(f"/v1/recordings/{m['recording_id']}/blob")
    assert missing.status_code == 404 and missing.json()["error"] == "blob_not_uploaded"
    ev = story(client, inc["incident_id"])["evidence"]
    assert ev[0]["stored"] is False

    # The right bytes then go through.
    assert put_blob(client, m, data).status_code == 204
    assert client.get(f"/v1/recordings/{m['recording_id']}/blob").content == data
    assert story(client, inc["incident_id"])["evidence"][0]["stored"] is True


def test_f7_recording_before_incident(client, arm, example, count_events, db_session):
    inc = incident(example)
    # One recording names the incident, one doesn't but the incident lists it.
    named_meta, named = meta(text_log(), "log_window", "text", inc["incident_id"]), text_log()
    listed_meta, listed = meta(audit_json(), "audit", "json", None), audit_json()
    upload(client, named_meta, named)
    upload(client, listed_meta, listed)
    assert client.get(f"/v1/incidents/{inc['incident_id']}/story").status_code == 404
    assert "kind:camera_occluded" not in labels(client, named_meta["recording_id"])
    assert "firmware:v1" in labels(client, named_meta["recording_id"])

    inc["recordings"] = [listed_meta["recording_id"]]
    assert client.post("/v1/devices/arm-1/incidents", json=inc).status_code == 201

    linked = db_session.get(Recording, listed_meta["recording_id"])
    db_session.refresh(linked)
    assert linked.incident_id == inc["incident_id"]
    for m in (named_meta, listed_meta):
        assert "kind:camera_occluded" in labels(client, m["recording_id"])
        assert count_events("recording.linked", {"recording": m["recording_id"]}) == 1
    s = story(client, inc["incident_id"])
    # The named recording shows from its `received` step, the listed one from its link.
    assert s["chain"].count("evidence: log window") == 1
    assert s["chain"].count("evidence: audit") == 1
    assert {e["recording"] for e in s["evidence"]} == {
        named_meta["recording_id"],
        listed_meta["recording_id"],
    }
    assert all(e["stored"] for e in s["evidence"])
    assert "under this incident" in next(
        st["line"] for st in s["steps"] if st["kind"] == "recording.linked"
    )

    # Resending the incident links nothing again.
    assert client.post("/v1/devices/arm-1/incidents", json=inc).status_code == 200
    assert count_events("recording.linked", {"incident": inc["incident_id"]}) == 2


def test_f7_identical_bytes_are_stored_once(client, arm, example, count_events, db_session):
    inc = incident(example)
    client.post("/v1/devices/arm-1/incidents", json=inc)
    data = mcap_log_window()
    first = meta(data, "log_window", "mcap", inc["incident_id"])
    second = meta(data, "log_window", "mcap", inc["incident_id"])
    assert upload(client, first, data)["upload"] == "needed"
    assert upload(client, second, data)["upload"] == "exists"  # no PUT needed
    assert blob_rows(db_session, data) == 1
    assert client.get(f"/v1/recordings/{second['recording_id']}/blob").content == data
    assert count_events("recording.blob_stored", {"recording": second["recording_id"]}) == 0
    assert all(e["stored"] for e in story(client, inc["incident_id"])["evidence"])

    # Two recordings both told to upload before either did: the second PUT dedupes.
    third = meta(data + b"!", "log_window", "mcap", inc["incident_id"])
    fourth = meta(data + b"!", "log_window", "mcap", inc["incident_id"])
    for m in (third, fourth):
        assert client.post("/v1/devices/arm-1/recordings", json=m).json()["upload"] == "needed"
    for m in (third, fourth):
        assert put_blob(client, m, data + b"!").status_code == 204
        assert count_events("recording.blob_stored", {"recording": m["recording_id"]}) == 1
    assert blob_rows(db_session, data, data + b"!") == 2


def test_f7_any_kind_and_format_is_accepted(client, arm):
    data = b"\x00\x01opaque"
    m = meta(data, "thermal_snapshot", "x-custom", None, extra_field={"kept": True})
    assert upload(client, m, data)["upload"] == "needed"
    rec = client.get(f"/v1/recordings/{m['recording_id']}").json()
    assert (rec["kind"], rec["format"], rec["incident_id"]) == (
        "thermal_snapshot",
        "x-custom",
        None,
    )
    dl = client.get(f"/v1/recordings/{m['recording_id']}/blob")
    assert dl.content == data and "x-custom" in dl.headers["content-disposition"]


def test_f7_errors(client, arm, app, example, tmp_path):
    data = text_log()
    m = meta(data, "log_window", "text", None)
    # Unknown device and unknown recording: 404 with a sentence.
    unknown_device = client.post("/v1/devices/ghost/recordings", json=m)
    assert unknown_device.status_code == 404 and unknown_device.json()["error"] == "unknown_device"
    unknown_rec = put_blob(client, m, data)
    assert unknown_rec.status_code == 404 and unknown_rec.json()["error"] == "unknown_recording"
    assert client.get(f"/v1/recordings/{m['recording_id']}").status_code == 404

    # Bad metadata: a clear 422.
    bad = client.post("/v1/devices/arm-1/recordings", json={**m, "sha256": "nope"})
    assert bad.status_code == 422 and bad.json()["error"] == "invalid_request"

    # The same recording id from another device is a conflict.
    assert client.post("/v1/devices/arm-1/recordings", json=m).status_code == 201
    client.post("/v1/devices/sim-1/checkin", json=example("checkin"))
    clash = client.post("/v1/devices/sim-1/recordings", json=m)
    assert clash.status_code == 409 and clash.json()["error"] == "recording_id_conflict"

    # Over the size cap: 413, nothing stored.
    app.state.settings = app.state.settings.model_copy(update={"recording_max_bytes": 10})
    big = put_blob(client, m, data)
    assert big.status_code == 413 and big.json()["error"] == "blob_too_large"
    # Chunked, with no Content-Length: the per-chunk check catches it.
    url = f"/v1/recordings/{m['recording_id']}/blob"
    chunked = client.put(url, content=(data[i : i + 4] for i in range(0, len(data), 4)))
    assert "content-length" not in chunked.request.headers
    assert chunked.status_code == 413 and chunked.json()["error"] == "blob_too_large"
    assert client.get(url).status_code == 404
    incoming = tmp_path / "blobs" / "incoming"
    assert incoming.is_dir() and not list(incoming.glob("*"))  # no temp file left behind


def test_f7_checkin_metrics_are_kept_as_history(client, arm, example, db_session):
    client.post("/v1/devices/arm-1/checkin", json=example("checkin"))
    rows = db_session.scalars(
        select(MetricSample).where(MetricSample.device_id == "arm-1").order_by(MetricSample.id)
    ).all()
    assert len(rows) == 2  # one per check-in (the fixture's and this one)
    assert rows[-1].metrics == {
        "task_success_rate": 0.92,
        "servo_temp_c_max": 41.5,
        "bus_voltage_v": 12.1,
    }


def test_f7_fake_device_uploads_through_the_real_api(client, arm, example):
    """The fake device's own evidence files and client agree with the cloud's routes."""
    inc = incident(example)
    assert client.post("/v1/devices/arm-1/incidents", json=inc).status_code == 201
    device = DeviceClient(client, "arm-1")
    iid, running = uuid.UUID(inc["incident_id"]), Running(firmware=1, harness=1)
    made = [
        fake_recordings.log_window(["S safe_pose"], AT, iid, running, ["camera_occluded"]),
        fake_recordings.agent_trace("dark frames", ["safe_pose"], AT, iid, running, []),
        fake_recordings.incident_window(AT, iid, running, ["camera_occluded"]),
    ]
    for meta_model, data in made:
        assert device.upload_recording(meta_model, data).upload == "needed"
        assert device.upload_recording(meta_model, data).upload == "exists"  # a resend
    evidence = story(client, inc["incident_id"])["evidence"]
    assert sorted(e["kind"] for e in evidence) == ["agent_trace", "incident_window", "log_window"]
    assert all(e["stored"] for e in evidence)
    for meta_model, data in made:
        assert client.get(f"/v1/recordings/{meta_model.recording_id}/blob").content == data
