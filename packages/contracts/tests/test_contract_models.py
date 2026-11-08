"""Every contract model: the APIs doc examples parse, extras survive, bad values are rejected."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from romulus_contracts import (
    Assigned,
    CheckIn,
    CheckInResponse,
    ErrorResponse,
    IncidentAccepted,
    IncidentReport,
    InstallResult,
    OverlayStatus,
    RecordingAccepted,
    RecordingMeta,
    Running,
)
from romulus_contracts.common import CloudModel, DeviceModel
from romulus_contracts.incident import DEVICE_OUTCOMES, SUSPECTED_CAUSES
from romulus_contracts.telemetry import EXPECTED_FORMAT, FLEET_FEATURES, RecordingKind

INCIDENT_ID = "0b6c6f3e-8d2a-4c1b-b5e3-6f7a8b9c0d1e"
SHA = "9c1f" * 16


# --- APIs doc examples, verbatim apart from completing the elided ids -----------------


def apis_checkin():
    return {
        "running": {
            "firmware": 7,
            "harness": 4,
            "code_rev": "a1b2c3d",
            "mcu_build": "stm32-f042-0007",
        },
        "overlay_id": "ov-12",
        "health": "ok",
        "metrics": {"task_success_rate": 0.92, "servo_temp_c_max": 41.5, "bus_voltage_v": 12.1},
        "contract_version": "0.1",
    }


def apis_incident():
    return {
        "incident_id": INCIDENT_ID,
        "occurred_at": "2026-11-07T22:02:11Z",
        "kind": "camera_occluded",
        "summary": "Camera brightness fell below threshold during pick",
        "running": {"firmware": 7, "harness": 4},
        "agent": {
            "reasoning": "Frames dark for 3 s; occlusion, not hardware.",
            "actions": ["safe_pose", "patch rules.camera_occluded.below 0.2 -> 0.35"],
        },
        "overlay": {
            "overlay_id": "ov-12",
            "diff": {"rules": {"camera_occluded": {"below": 0.35}}},
            "code_patch": None,
        },
        "outcome": {
            "resolved": True,
            "resolving_action": "safe_pose",
            "suspected_cause": "software",
        },
        "signals": {"brightness": [0.41, 0.18, 0.12, 0.33]},
        "recordings": ["5d1e0000-0000-4000-8000-000000000001"],
    }


def apis_recording():
    return {
        "recording_id": "5d1e0000-0000-4000-8000-000000000001",
        "kind": "incident_window",
        "format": "lerobot_v3_tar",
        "sha256": SHA,
        "size_bytes": 18322110,
        "started_at": "2026-11-07T22:01:41Z",
        "ended_at": "2026-11-07T22:02:41Z",
        "running": {"firmware": 7, "harness": 4},
        "overlay_id": "ov-12",
        "incident_id": INCIDENT_ID,
        "episode": None,
        "streams": list(FLEET_FEATURES),
        "schema_version": "0.1",
        "labels": ["camera_occluded"],
    }


# --- base classes ----------------------------------------------------------------------


def test_device_models_keep_extra_fields():
    for model in (CheckIn, IncidentReport, InstallResult, OverlayStatus, RecordingMeta, Running):
        assert issubclass(model, DeviceModel), model
        assert model.model_config["extra"] == "allow"


def test_cloud_models_ignore_extra_fields():
    for model in (CheckInResponse, Assigned, IncidentAccepted, RecordingAccepted, ErrorResponse):
        assert issubclass(model, CloudModel), model
    resp = CheckInResponse.model_validate(
        {"assigned": {"firmware": 8, "harness": 4}, "drop_overlay": False, "frozen": False, "x": 1}
    )
    assert "x" not in resp.model_dump()


# --- check-in --------------------------------------------------------------------------


def test_checkin_apis_example():
    c = CheckIn.model_validate(apis_checkin())
    assert c.running.firmware == 7 and c.running.mcu_build == "stm32-f042-0007"
    assert c.health == "ok" and c.overlay_id == "ov-12"


def test_checkin_first_boot_running_null():
    body = apis_checkin() | {"running": None, "overlay_id": None}
    assert CheckIn.model_validate(body).running is None


def test_checkin_running_is_required_even_if_null():
    body = apis_checkin()
    del body["running"]
    with pytest.raises(ValidationError):
        CheckIn.model_validate(body)


@pytest.mark.parametrize("health", ["ok", "degraded", "failing"])
def test_checkin_health_values(health):
    assert CheckIn.model_validate(apis_checkin() | {"health": health}).health == health


@pytest.mark.parametrize("health", ["OK", "bad", "", None, 3])
def test_checkin_rejects_unknown_health(health):
    with pytest.raises(ValidationError):
        CheckIn.model_validate(apis_checkin() | {"health": health})


def test_checkin_requires_contract_version():
    body = apis_checkin()
    del body["contract_version"]
    with pytest.raises(ValidationError):
        CheckIn.model_validate(body)


def test_checkin_unsupported_contract_version_still_parses():
    # The server, not the model, decides skew (version.is_supported).
    assert CheckIn.model_validate(apis_checkin() | {"contract_version": "9.9"})


def test_checkin_extra_fields_and_free_form_metrics_survive():
    body = apis_checkin() | {"health_detail": {"tier": 2, "check": "heartbeat_age"}, "x": 1}
    body["metrics"] |= {"note": "fan loud", "tiers": [1, 2], "flag": True, "nan": float("nan")}
    c = CheckIn.model_validate(body)
    assert c.health_detail == {"tier": 2, "check": "heartbeat_age"}
    assert c.model_extra == {"x": 1}
    assert c.model_dump()["health_detail"]["tier"] == 2
    assert c.numeric_metrics() == {
        "task_success_rate": 0.92,
        "servo_temp_c_max": 41.5,
        "bus_voltage_v": 12.1,
    }


@pytest.mark.parametrize("detail", [None, "tier 2: heartbeat stale", [1, 2]])
def test_checkin_health_detail_is_any_json(detail):
    assert (
        CheckIn.model_validate(apis_checkin() | {"health_detail": detail}).health_detail == detail
    )


def test_checkin_null_metrics_reads_as_empty():
    assert CheckIn.model_validate(apis_checkin() | {"metrics": None}).metrics == {}


def test_running_partial_and_extra():
    r = Running.model_validate({"firmware": 7, "harness": 4})
    assert r.code_rev is None and r.mcu_build is None
    assert Running.model_validate({"firmware": 7, "jetson": "xavier"}).model_extra == {
        "jetson": "xavier"
    }


@pytest.mark.parametrize("bad", [-1, True, 1.5, "seven"])
def test_running_rejects_bad_versions(bad):
    with pytest.raises(ValidationError):
        Running.model_validate({"firmware": bad, "harness": 4})


def test_checkin_response_example_and_defaults():
    r = CheckInResponse.model_validate(
        {"assigned": {"firmware": 8, "harness": 4}, "drop_overlay": False, "frozen": False}
    )
    assert r.assigned.firmware == 8
    r2 = CheckInResponse(assigned=Assigned())
    assert r2.model_dump() == {
        "assigned": {"firmware": None, "harness": None},
        "drop_overlay": False,
        "frozen": False,
    }


# --- install result --------------------------------------------------------------------


def test_install_result_apis_example():
    i = InstallResult.model_validate(
        {"type": "firmware", "version": 8, "result": "installed", "detail": "health ok after 5 s"}
    )
    assert i.version == 8 and i.occurred_at is None


@pytest.mark.parametrize("result", ["installed", "rolled_back", "hash_mismatch", "invalid"])
def test_install_result_values(result):
    assert InstallResult(type="harness", version=4, result=result).result == result


@pytest.mark.parametrize(
    "body",
    [
        {"type": "firmware", "version": 8, "result": "ok"},
        {"type": "model", "version": 8, "result": "installed"},
        {"type": "firmware", "version": True, "result": "installed"},
        {"type": "firmware", "version": -1, "result": "installed"},
        {"type": "firmware", "version": "-1", "result": "installed"},
        {"type": "firmware", "version": "v8", "result": "installed"},
        {"type": "harness", "version": "a1b2c3d", "result": "installed"},
        {"type": "code", "version": 7, "result": "installed"},
        {"type": "code", "version": "", "result": "installed"},
        {"type": "firmware", "result": "installed"},
    ],
)
def test_install_result_rejects(body):
    with pytest.raises(ValidationError):
        InstallResult.model_validate(body)


def test_install_result_numeric_string_version_reads_as_int():
    assert InstallResult(type="firmware", version="8", result="installed").version == 8


def test_install_result_invalid_manifest():
    i = InstallResult.model_validate(
        {
            "type": "firmware",
            "version": 9,
            "result": "invalid",
            "detail": "rule 'x': needs 'below' or 'above'",
            "contract_version": "0.1",
        }
    )
    assert i.result == "invalid" and i.contract_version == "0.1"


def test_install_result_code_uses_rev_string():
    i = InstallResult(type="code", version="a1b2c3d", result="rolled_back")
    assert i.version == "a1b2c3d"


# --- incidents -------------------------------------------------------------------------


def test_incident_apis_example():
    inc = IncidentReport.model_validate(apis_incident())
    assert inc.incident_id == UUID(INCIDENT_ID)
    assert inc.occurred_at == datetime(2026, 11, 7, 22, 2, 11, tzinfo=UTC)
    assert inc.overlay.diff == {"rules": {"camera_occluded": {"below": 0.35}}}
    assert inc.outcome.resolving_action == "safe_pose"
    assert inc.recordings == [UUID("5d1e0000-0000-4000-8000-000000000001")]


def test_incident_minimal():
    inc = IncidentReport.model_validate(
        {
            "incident_id": INCIDENT_ID,
            "occurred_at": "2026-11-07T22:02:11Z",
            "kind": "x",
            "outcome": {"resolved": False},
        }
    )
    assert inc.overlay is None and inc.agent is None and inc.signals == {}
    assert inc.outcome.suspected_cause == "unknown" and inc.outcome.resolving_action is None
    assert inc.outcome.category is None and inc.tier is None and inc.log_tail == []


@pytest.mark.parametrize("outcome", [None, "missing"])
def test_incident_without_outcome_is_accepted(outcome):
    body = apis_incident()
    if outcome == "missing":
        del body["outcome"]
    else:
        body["outcome"] = None
    assert IncidentReport.model_validate(body).outcome is None


def test_outcome_defaults():
    o = IncidentReport.model_validate(apis_incident() | {"outcome": {}}).outcome
    assert (o.resolved, o.resolving_action, o.suspected_cause, o.category) == (
        False,
        None,
        "unknown",
        None,
    )


@pytest.mark.parametrize("missing", ["incident_id", "occurred_at", "kind"])
def test_incident_required_fields(missing):
    body = apis_incident()
    del body[missing]
    with pytest.raises(ValidationError):
        IncidentReport.model_validate(body)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("incident_id",), "0b6c6f3e-…"),
        (("occurred_at",), "yesterday"),
        (("outcome", "resolved"), "maybe"),
        (("outcome", "suspected_cause"), 7),
        (("recordings",), ["not-a-uuid"]),
        (("overlay", "overlay_id"), None),
    ],
)
def test_incident_rejects_bad_values(path, value):
    body = apis_incident()
    target = body
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        IncidentReport.model_validate(body)


@pytest.mark.parametrize("cause", [*SUSPECTED_CAUSES, "mechanical"])
def test_incident_suspected_cause_is_open(cause):
    body = apis_incident()
    body["outcome"]["suspected_cause"] = cause
    assert IncidentReport.model_validate(body).outcome.suspected_cause == cause


@pytest.mark.parametrize("category", [*DEVICE_OUTCOMES, "something-new"])
def test_device_outcome_category_is_an_open_declared_field(category):
    body = apis_incident()
    body["outcome"]["category"] = category
    inc = IncidentReport.model_validate(body)
    assert inc.outcome.category == category and inc.outcome.model_extra == {}
    assert inc.model_dump(mode="json")["outcome"]["category"] == category


def test_incident_extras_survive_round_trip():
    log = ["S HOLDING->RELEASE", {"level": "error", "msg": "boom"}]
    body = apis_incident() | {"tier": 3, "log_tail": log, "audit": {"disk_pct": 41}}
    body["agent"]["model"] = "qwen3-4b"
    inc = IncidentReport.model_validate(body)
    assert inc.tier == 3 and inc.log_tail == log and inc.model_extra == {"audit": {"disk_pct": 41}}
    dumped = inc.model_dump(mode="json")
    assert dumped["tier"] == 3 and dumped["agent"]["model"] == "qwen3-4b"
    assert IncidentReport.model_validate(dumped) == inc


def test_agent_actions_may_be_dicts():
    body = apis_incident()
    body["agent"]["actions"] = ["safe_pose", {"tool": "bash", "cmd": "systemctl restart app"}]
    inc = IncidentReport.model_validate(body)
    assert inc.agent.actions[1] == {"tool": "bash", "cmd": "systemctl restart app"}


def test_null_lists_and_objects_read_as_empty():
    body = apis_incident() | {"signals": None, "recordings": None, "log_tail": None}
    body["agent"]["actions"] = None
    body["overlay"]["diff"] = None
    inc = IncidentReport.model_validate(body)
    assert (inc.signals, inc.recordings, inc.log_tail) == ({}, [], [])
    assert inc.agent.actions == [] and inc.overlay.diff == {}


def test_proposed_fields_are_in_the_json_schema():
    props = IncidentReport.model_json_schema()
    assert {"tier", "log_tail", "contract_version"} <= set(props["properties"])
    assert "category" in props["$defs"]["Outcome"]["properties"]
    assert "health_detail" in CheckIn.model_json_schema()["properties"]
    for model in (InstallResult, OverlayStatus, RecordingMeta):
        assert "contract_version" in model.model_json_schema()["properties"], model


def test_incident_signals_are_loose():
    body = apis_incident() | {"signals": {"dist_mm": [31, None, 0], "frame_ok": True}}
    assert IncidentReport.model_validate(body).signals["dist_mm"] == [31, None, 0]


def test_overlay_code_patch_is_kept_verbatim_even_if_malformed():
    body = apis_incident()
    body["overlay"]["code_patch"] = "not a diff at all\n"
    assert IncidentReport.model_validate(body).overlay.code_patch == "not a diff at all\n"


# --- times -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stamp",
    ["2026-11-07T22:02:11Z", "2026-11-07T22:02:11+00:00", "2026-11-07T14:02:11-08:00"],
)
def test_times_normalize_to_utc(stamp):
    inc = IncidentReport.model_validate(apis_incident() | {"occurred_at": stamp})
    assert inc.occurred_at == datetime(2026, 11, 7, 22, 2, 11, tzinfo=UTC)
    assert inc.occurred_at.utcoffset().total_seconds() == 0


def test_naive_time_is_read_as_utc():
    inc = IncidentReport.model_validate(apis_incident() | {"occurred_at": "2026-11-07T22:02:11"})
    assert inc.occurred_at.tzinfo is not None
    assert inc.occurred_at == datetime(2026, 11, 7, 22, 2, 11, tzinfo=UTC)


# --- overlay status --------------------------------------------------------------------


@pytest.mark.parametrize("status", ["held", "reverted"])
def test_overlay_status_values(status):
    s = OverlayStatus.model_validate({"status": status, "reason": "no recurrence for 20 min"})
    assert s.status == status and s.occurred_at is None


@pytest.mark.parametrize("status", ["locked_in", "HELD", None])
def test_overlay_status_rejects_other_values(status):
    with pytest.raises(ValidationError):
        OverlayStatus.model_validate({"status": status})


# --- recordings ------------------------------------------------------------------------


def test_recording_apis_example():
    r = RecordingMeta.model_validate(apis_recording())
    assert r.sha256 == SHA and r.incident_id == UUID(INCIDENT_ID)


def test_recording_kind_and_format_are_open_strings():
    r = RecordingMeta.model_validate(apis_recording() | {"kind": "audit2", "format": "txt"})
    assert (r.kind, r.format) == ("audit2", "txt")


def test_recording_sha256_normalized_and_validated():
    upper = RecordingMeta.model_validate(apis_recording() | {"sha256": " " + SHA.upper() + " "})
    assert upper.sha256 == SHA
    for bad in ["9c1f…", SHA[:-1], SHA + "0", "z" * 64]:
        with pytest.raises(ValidationError):
            RecordingMeta.model_validate(apis_recording() | {"sha256": bad})


def test_recording_minimal_and_episode():
    r = RecordingMeta.model_validate(
        {
            "recording_id": "5d1e0000-0000-4000-8000-000000000001",
            "kind": "episode",
            "format": "lerobot_v3_tar",
            "sha256": SHA,
            "size_bytes": 0,
            "started_at": "2026-11-07T22:01:41Z",
            "episode": {"task": "pick_cube", "success": True, "index": 412},
        }
    )
    assert r.episode.index == 412 and r.ended_at is None and r.labels == []


def test_recording_rejects_negative_size():
    with pytest.raises(ValidationError):
        RecordingMeta.model_validate(apis_recording() | {"size_bytes": -1})


def test_recording_accepted_and_expected_formats():
    assert (
        RecordingAccepted.model_validate(
            {"recording_id": "5d1e0000-0000-4000-8000-000000000001", "upload": "exists"}
        ).upload
        == "exists"
    )
    with pytest.raises(ValidationError):
        RecordingAccepted.model_validate(
            {"recording_id": "5d1e0000-0000-4000-8000-000000000001", "upload": "maybe"}
        )
    assert EXPECTED_FORMAT[RecordingKind.LOG_WINDOW] == "mcap"
    assert EXPECTED_FORMAT["incident_window"] == "lerobot_v3_tar"


def test_fleet_features_match_telemetry_section_8():
    assert list(FLEET_FEATURES) == [
        "observation.state",
        "action",
        "observation.images.front",
        "servo_temp",
        "servo_load",
        "servo_voltage",
        "fault_code",
        "reflex_active",
        "intervention",
    ]


# --- responses -------------------------------------------------------------------------


def test_incident_accepted_and_error_response():
    assert IncidentAccepted.model_validate({"incident_id": INCIDENT_ID}).incident_id == UUID(
        INCIDENT_ID
    )
    err = ErrorResponse.model_validate({"error": "sha256_mismatch", "detail": "Blob hash differs."})
    assert err.error == "sha256_mismatch"
