"""F1 · Version sync (flows doc F1): check in → assigned → pull with hash → install → running.

Everything goes through the HTTP API with the contract examples as payloads.
"""

import hashlib
import io
import json
import tarfile

from sqlalchemy import func, select

from romulus_cloud import events
from romulus_cloud.models import Device, Event, InstallReport
from romulus_cloud.services.registry import build_tarball
from romulus_contracts import SHA256_HEADER, manifest_sha256


def _device(client, device_id):
    resp = client.get(f"/v1/devices/{device_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _install(client, device_id, type_, version, result, occurred_at, detail=None):
    body = {"type": type_, "version": version, "result": result, "occurred_at": occurred_at}
    if detail:
        body["detail"] = detail
    return client.post(f"/v1/devices/{device_id}/installs", json=body)


def _pull_harness(client, version):
    resp = client.get(f"/v1/harness/{version}/package")
    assert resp.status_code == 200
    assert resp.headers[SHA256_HEADER] == hashlib.sha256(resp.content).hexdigest()
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        return json.loads(tar.extractfile("harness.json").read())


def test_f1_happy_path(client, seeded, example, count_events):
    # 1. A device the cloud has never seen checks in with nothing installed.
    resp = client.post("/v1/devices/arm-9/checkin", json=example("checkin_first_boot"))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "assigned": {"firmware": 1, "harness": 1},
        "drop_overlay": False,
        "frozen": False,
    }
    dev = _device(client, "arm-9")
    assert dev["assigned"] == {"firmware": 1, "harness": 1}
    assert dev["running"]["firmware"] is None and dev["last_good"]["firmware"] is None
    assert count_events("device.first_seen", {"device": "arm-9"}) == 1

    # 2. It pulls firmware v1 (manifest + sha256), harness v1 and code c0de001 (X-Sha256).
    fw = client.get("/v1/firmware/1").json()
    assert manifest_sha256(fw["manifest"]) == fw["sha256"]
    assert fw == example("firmware_response")  # the seeded v1 is the contract example
    assert _pull_harness(client, 1)["version"] == 1
    code = client.get(f"/v1/code/{fw['manifest']['code_rev']}/package")
    assert code.headers[SHA256_HEADER] == hashlib.sha256(code.content).hexdigest()

    # 3. It installs, passes its health check and reports `installed` for each.
    t = "2026-11-07T22:01:00Z"
    assert _install(client, "arm-9", "firmware", 1, "installed", t).status_code == 204
    assert _install(client, "arm-9", "harness", 1, "installed", t).status_code == 204
    assert _install(client, "arm-9", "code", "c0de001", "installed", t).status_code == 204

    # 4. The next check-in reports what it runs: running == assigned, last_good == v1.
    resp = client.post("/v1/devices/arm-9/checkin", json=example("checkin"))
    assert resp.json()["assigned"] == {"firmware": 1, "harness": 1}
    dev = _device(client, "arm-9")
    assert dev["running"] == {
        "firmware": 1,
        "harness": 1,
        "code_rev": "c0de001",
        "mcu_build": "stm32-f042-0007",
    }
    assert dev["running"]["firmware"] == dev["assigned"]["firmware"]
    assert dev["running"]["harness"] == dev["assigned"]["harness"]
    assert dev["last_good"] == {"firmware": 1, "harness": 1}
    assert count_events("install.reported", {"device": "arm-9"}) == 3


def test_f1_routine_checkins_write_no_events(client, seeded, example, count_events, db_session):
    client.post("/v1/devices/arm-1/checkin", json=example("checkin"))
    before = db_session.scalar(select(func.count()).select_from(Event))
    for _ in range(3):
        assert client.post("/v1/devices/arm-1/checkin", json=example("checkin")).status_code == 200
    assert db_session.scalar(select(func.count()).select_from(Event)) == before
    # A change (health) does write one.
    degraded = example("checkin_degraded_overlay")
    client.post("/v1/devices/arm-1/checkin", json=degraded)
    assert count_events("device.health_changed", {"device": "arm-1"}) == 1
    assert count_events("device.overlay_changed", {"device": "arm-1"}) == 1
    assert _device(client, "arm-1")["last_seen_at"] is not None


def test_f1_check_in_stores_raw_payload(client, seeded, example, db_session):
    body = example("checkin") | {"extra_field_from_newer_device": {"x": 1}}
    client.post("/v1/devices/arm-1/checkin", json=body)
    db_session.expire_all()
    assert db_session.get(Device, "arm-1").raw == body


def test_f1_rolled_back_keeps_last_good(client, v2, example, count_events):
    t0 = "2026-11-07T22:01:00Z"
    _install(client, "arm-1", "firmware", 1, "installed", t0)
    _install(client, "arm-1", "code", "c0de001", "installed", t0)
    # The operator assigns firmware v2; the device installs it, fails health, rolls back.
    resp = client.patch("/v1/devices/arm-1", json={"assigned": {"firmware": 2}, "by": "kent"})
    assert resp.json()["assigned"]["firmware"] == 2
    checkin = client.post("/v1/devices/arm-1/checkin", json=example("checkin")).json()
    assert checkin["assigned"]["firmware"] == 2

    detail = "tier 1: health failing after 10 s; restored v1"
    resp = _install(client, "arm-1", "firmware", 2, "rolled_back", "2026-11-07T22:05:00Z", detail)
    assert resp.status_code == 204
    assert (
        client.post(
            "/v1/devices/arm-1/installs", json=example("install_result_code_rolled_back")
        ).status_code
        == 204
    )

    dev = _device(client, "arm-1")
    assert dev["running"]["firmware"] == 1 and dev["last_good"]["firmware"] == 1
    assert dev["running"]["code_rev"] == "c0de001"
    assert count_events("install.reported", {"device": "arm-1", "firmware": 2}) == 1
    assert count_events("install.reported", {"device": "arm-1", "code_rev": "c0de002"}) == 1


def test_f1_rolled_back_why_is_plain_english(client, v2, db_session):
    _install(client, "arm-1", "firmware", 1, "installed", "2026-11-07T22:01:00Z")
    _install(client, "arm-1", "firmware", 2, "rolled_back", "2026-11-07T22:05:00Z", "tier 1")
    (event,) = events.find(db_session, {"device": "arm-1", "firmware": 2})
    assert event.why == (
        "arm-1 rolled back firmware 2 after a failed health check (tier 1); "
        "it stays on last good firmware 1."
    )
    assert event.actor == "device:arm-1" and event.actor_class == "automation"


def test_f1_hash_mismatch_changes_nothing_but_is_recorded(client, v2, db_session):
    _install(client, "arm-1", "harness", 1, "installed", "2026-11-07T22:01:00Z")
    resp = _install(client, "arm-1", "harness", 2, "hash_mismatch", "2026-11-07T22:06:00Z")
    assert resp.status_code == 204
    dev = _device(client, "arm-1")
    assert dev["running"]["harness"] == 1 and dev["last_good"]["harness"] == 1
    (event,) = events.find(db_session, {"device": "arm-1", "harness": 2})
    assert "sha256 did not match" in event.why
    assert event.data["result"] == "hash_mismatch"


def test_f1_install_resend_is_deduplicated(client, v2, example, db_session, count_events):
    body = example("install_result_firmware")
    assert client.post("/v1/devices/arm-1/installs", json=body).status_code == 204
    resend = client.post("/v1/devices/arm-1/installs", json=body)
    assert resend.status_code == 200
    rows = db_session.scalar(
        select(func.count()).select_from(InstallReport).where(InstallReport.device_id == "arm-1")
    )
    assert rows == 1
    assert count_events("install.reported", {"device": "arm-1"}) == 1


def test_f1_install_without_occurred_at_is_always_recorded(client, seeded, count_events):
    body = {"type": "firmware", "version": 1, "result": "installed"}
    assert client.post("/v1/devices/arm-1/installs", json=body).status_code == 204
    assert client.post("/v1/devices/arm-1/installs", json=body).status_code == 204
    assert count_events("install.reported", {"device": "arm-1"}) == 2


def test_f1_contract_skew_is_rejected_clearly(client, v2, example):
    body = example("checkin") | {"contract_version": "9.9"}
    resp = client.post("/v1/devices/arm-7/checkin", json=body)
    assert resp.status_code == 400
    assert resp.json()["error"] == "unsupported_contract_version"
    assert "0.1" in resp.json()["detail"] and "'9.9'" in resp.json()["detail"]
    # Nothing was registered by the rejected request.
    assert client.get("/v1/devices/arm-7").status_code == 404

    missing = example("checkin")
    del missing["contract_version"]
    resp = client.post("/v1/devices/arm-1/checkin", json=missing)
    assert resp.json()["error"] == "unsupported_contract_version"

    install = example("install_result_invalid") | {"contract_version": "0.0"}
    resp = client.post("/v1/devices/arm-1/installs", json=install)
    assert resp.status_code == 400
    # Without a contract_version (optional on installs) it is accepted.
    assert (
        client.post(
            "/v1/devices/arm-1/installs", json=example("install_result_firmware")
        ).status_code
        == 204
    )


def test_f1_bad_payloads_and_unknown_things(client, seeded, example):
    bad = example("checkin") | {"health": "great"}
    resp = client.post("/v1/devices/arm-1/checkin", json=bad)
    assert resp.status_code == 422 and resp.json()["error"] == "invalid_request"
    assert client.get("/v1/firmware/99").json()["error"] == "unknown_firmware"
    assert client.get("/v1/harness/99/package").status_code == 404
    assert client.get("/v1/code/nope/package").status_code == 404
    resp = client.post("/v1/devices/ghost/installs", json=example("install_result_firmware"))
    assert resp.status_code == 404 and resp.json()["error"] == "unknown_device"


def test_f1_operator_uploads_and_assigns(client, seeded, count_events):
    harness_v2 = build_tarball(
        {"harness.json": json.dumps({"version": 2, "framework": "mini-swe-agent"}).encode()}
    )
    resp = client.post("/v1/harness", files={"file": ("h.tar.gz", harness_v2)}, data={"by": "kent"})
    assert resp.status_code == 201 and resp.json()["version"] == 2
    again = client.post("/v1/harness", files={"file": ("h.tar.gz", harness_v2)})
    assert again.status_code == 200
    assert _pull_harness(client, 2)["version"] == 2

    code = build_tarball({"device/app.py": b"print('hi')\n"})
    resp = client.post("/v1/code", files={"file": ("c.tar.gz", code)}, data={"rev": "c0de002"})
    assert resp.status_code == 201 and resp.json()["rev"] == "c0de002"
    pulled = client.get("/v1/code/c0de002/package")
    assert pulled.content == code

    resp = client.patch("/v1/devices/sim-1", json={"assigned": {"harness": 2}, "by": "kent"})
    assert resp.json()["assigned"] == {"firmware": 1, "harness": 2}
    assert count_events("device.assigned", {"device": "sim-1"}) == 1
    resp = client.post(
        "/v1/devices/sim-1/checkin",
        json={"running": None, "health": "ok", "contract_version": "0.1"},
    )
    assert resp.json()["assigned"] == {"firmware": 1, "harness": 2}

    listing = {d["id"]: d for d in client.get("/v1/devices").json()}
    assert set(listing) == {"arm-1", "sim-1", "sim-2", "sim-3"}
    assert listing["sim-2"]["role"] == "canary"
    assert listing["arm-1"]["kind"] == "real" and listing["sim-1"]["kind"] == "sim"
    assert client.patch("/v1/devices/sim-1", json={"assigned": {"firmware": 42}}).status_code == 404


def test_f1_unknown_version_is_refused(client, seeded, count_events):
    t = "2026-11-07T22:01:00Z"
    resp = _install(client, "arm-1", "firmware", 99, "installed", t)
    assert resp.status_code == 422 and resp.json()["error"] == "unknown_version"
    assert _install(client, "arm-1", "code", "nope", "installed", t).status_code == 422
    dev = _device(client, "arm-1")
    assert dev["running"]["firmware"] is None and dev["last_good"]["firmware"] is None
    assert count_events("install.reported", {"device": "arm-1"}) == 0


def test_f1_late_older_report_moves_nothing(client, v2, db_session):
    """The reviewer's reproduction: installed v2 at T+10, then a late installed v1 at T0."""
    client.patch("/v1/devices/arm-1", json={"assigned": {"firmware": 2}})
    assert (
        _install(client, "arm-1", "firmware", 2, "installed", "2026-11-07T22:10:00Z").status_code
        == 204
    )
    late = _install(client, "arm-1", "firmware", 1, "installed", "2026-11-07T22:00:00Z")
    assert late.status_code == 204
    dev = _device(client, "arm-1")
    assert dev["running"]["firmware"] == 2 and dev["last_good"]["firmware"] == 2
    assert dev["assigned"]["firmware"] == 2
    (event,) = events.find(db_session, {"device": "arm-1", "firmware": 1})
    assert "Ignored for running and last good: a newer firmware report" in event.why
    assert event.data["stale"] is True


def test_f1_installed_but_not_assigned_moves_running_not_last_good(client, v2, db_session):
    _install(client, "arm-1", "firmware", 1, "installed", "2026-11-07T22:00:00Z")
    _install(client, "arm-1", "firmware", 2, "installed", "2026-11-07T22:05:00Z")
    dev = _device(client, "arm-1")
    assert dev["running"]["firmware"] == 2 and dev["last_good"]["firmware"] == 1
    (event,) = events.find(db_session, {"device": "arm-1", "firmware": 2})
    assert event.why.endswith("It is not the assigned firmware (1), so last good stays 1.")
