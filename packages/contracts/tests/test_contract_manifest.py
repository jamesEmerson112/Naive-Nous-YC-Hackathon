"""Firmware manifest, canonical-JSON hash, firmware response and harness metadata."""

import hashlib
import json

import pytest
from pydantic import ValidationError
from romulus_contracts import FirmwareManifest, FirmwareResponse, HarnessMeta, evaluate
from romulus_contracts.manifest import canonical_json, firmware_response, manifest_sha256


def apis_manifest():
    return {
        "version": 8,
        "device_class": "so101",
        "code_rev": "a1b2c3d",
        "rules": {
            "camera_occluded": {"signal": "brightness", "below": 0.35, "action": "safe_pose"},
            "gripper_stall": {
                "signal": "gripper_current_ma",
                "above": 900,
                "action": "backoff_retry",
            },
        },
        "params": {"max_speed": 0.6},
        "policy": {"name": "pick_cube", "rev": "hf:org/act@3f9e"},
    }


def test_manifest_apis_example_and_rules_evaluate():
    m = FirmwareManifest.model_validate(apis_manifest())
    assert m.policy.rev == "hf:org/act@3f9e"
    assert [f.rule for f in evaluate(m.rules, {"brightness": [0.1]})] == ["camera_occluded"]


def test_manifest_defaults():
    m = FirmwareManifest.model_validate({"version": 1, "device_class": "so101", "code_rev": "x"})
    assert m.rules == {} and m.params == {} and m.policy is None


def test_manifest_rejects_malformed_rules():
    body = apis_manifest()
    body["rules"]["broken"] = {"signal": "s", "action": "a"}
    with pytest.raises(ValidationError, match="needs 'below' or 'above'"):
        FirmwareManifest.model_validate(body)


@pytest.mark.parametrize("missing", ["version", "device_class", "code_rev"])
def test_manifest_required_fields(missing):
    body = apis_manifest()
    del body[missing]
    with pytest.raises(ValidationError):
        FirmwareManifest.model_validate(body)


def test_manifest_keeps_extra_fields_and_rule_annotations():
    body = apis_manifest() | {"notes": "seed"}
    body["rules"]["camera_occluded"]["description"] = "dark frames"
    m = FirmwareManifest.model_validate(body)
    assert m.model_extra == {"notes": "seed"}
    assert m.rules["camera_occluded"]["description"] == "dark frames"


def test_canonical_json_is_sorted_compact_utf8():
    assert canonical_json({"b": 1, "a": [1.5, "é"]}) == '{"a":[1.5,"é"],"b":1}'.encode()
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_manifest_sha256_ignores_key_order_but_not_values():
    m = apis_manifest()
    reordered = dict(reversed(list(m.items())))
    assert manifest_sha256(m) == manifest_sha256(reordered)
    assert manifest_sha256(m) == hashlib.sha256(canonical_json(m)).hexdigest()
    assert manifest_sha256(m) != manifest_sha256(m | {"version": 9})
    # 900 and 900.0 are different JSON: hash what was sent.
    as_float = apis_manifest()
    as_float["rules"]["gripper_stall"]["above"] = 900.0
    assert manifest_sha256(m) != manifest_sha256(as_float)


def test_manifest_sha256_of_model_equals_dict_when_fully_specified():
    m = apis_manifest()
    assert manifest_sha256(FirmwareManifest.model_validate(m)) == manifest_sha256(m)


def test_firmware_response_parses_and_hash_verifies():
    m = apis_manifest()
    resp = FirmwareResponse.model_validate({"manifest": m, "sha256": manifest_sha256(m)})
    assert resp.sha256 == manifest_sha256(m)
    with pytest.raises(ValidationError):
        FirmwareResponse.model_validate({"manifest": m, "sha256": "…"})


def test_firmware_response_builder_hashes_its_own_dump_for_sparse_manifests():
    sparse = {"version": "8", "device_class": "so101", "code_rev": "a1b2c3d", "note": "x"}
    # The raw dict and the model disagree (defaults, "8" vs 8): the reason for the builder.
    assert manifest_sha256(sparse) != manifest_sha256(FirmwareManifest.model_validate(sparse))
    body = firmware_response(sparse)
    assert body["manifest"] == {
        "version": 8,
        "device_class": "so101",
        "code_rev": "a1b2c3d",
        "rules": {},
        "params": {},
        "policy": None,
        "note": "x",
    }
    # What the device receives after a JSON round trip hashes to the served sha256.
    received = json.loads(json.dumps(body))
    assert manifest_sha256(received["manifest"]) == received["sha256"]
    assert FirmwareResponse.model_validate(received).sha256 == body["sha256"]
    # Model in, same answer as dict in; and building from the served dump is stable.
    assert firmware_response(FirmwareManifest.model_validate(sparse)) == body
    assert firmware_response(body["manifest"]) == body


def test_firmware_response_builder_rejects_invalid_manifests():
    with pytest.raises(ValidationError):
        firmware_response({"version": 1, "device_class": "so101"})
    with pytest.raises(ValidationError):
        firmware_response(apis_manifest() | {"rules": {"r": {"signal": "s", "action": "a"}}})


def test_harness_meta_apis_example():
    h = HarnessMeta.model_validate({"version": 5, "framework": "pi", "knowledge_dir": "skills/"})
    assert h.knowledge_dir == "skills/"
    assert HarnessMeta.model_validate({"version": 5, "framework": "pi"}).knowledge_dir == "skills/"


@pytest.mark.parametrize("bad", ["", "/etc", "../skills", "skills/../../x", "skills\\x"])
def test_harness_knowledge_dir_must_stay_inside(bad):
    with pytest.raises(ValidationError):
        HarnessMeta.model_validate({"version": 5, "framework": "pi", "knowledge_dir": bad})


@pytest.mark.parametrize("ok", ["skills", "skills/", "kb/skills/", "./skills"])
def test_harness_knowledge_dir_relative_ok(ok):
    assert HarnessMeta.model_validate({"version": 5, "framework": "pi", "knowledge_dir": ok})
