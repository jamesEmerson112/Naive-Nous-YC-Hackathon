"""L3 device-tool contract (tools.py, PR 8): draft, D27 deferred, not wired to any route.

The catalog matches interfaces §11, offers no shell, every params schema is closed, and
`validate_args` catches what a bad call would carry.
"""

import re

import pytest
from pydantic import ValidationError

from romulus_contracts import (
    TOOL_CATALOG,
    ToolCall,
    ToolResult,
    ToolSpec,
    tool,
    validate_args,
)
from romulus_contracts.common import CloudModel, DeviceModel
from romulus_contracts.schema import MODELS

READ_TOOLS = {
    "read_logs",
    "read_audit",
    "grab_frame",
    "stack_dump",
    "list_devices",
    "read_file",
    "run_health",
    "run_tests_scratch",
}
ACT_TOOLS = {"apply_overlay", "park", "set_mode", "rollback", "build_and_flash"}

PATCH = "--- a/control/hold.py\n+++ b/control/hold.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"

# One valid call per tool; the coverage test keeps this in step with the catalog.
GOOD_ARGS = {
    "read_logs": {"since_s": 300, "max_lines": 200, "grep": "hold"},
    "read_audit": {"since_s": 600.5, "max_entries": 50},
    "grab_frame": {"camera": "wrist", "max_width": 640},
    "stack_dump": {"target": "healer"},
    "list_devices": {},
    "read_file": {"path": "control/hold.py", "max_bytes": 65536},
    "run_health": {"tier": 2},
    "run_tests_scratch": {
        "code_patch": PATCH,
        "tests": ["tests/test_hold.py::test_no_drop[proximity]"],
        "timeout_s": 120,
    },
    "apply_overlay": {"code_patch": PATCH, "proposal_id": "prop_1"},
    "park": {"reason": "servo 4 silent"},
    "set_mode": {"mode": "safe"},
    "rollback": {"target": "overlay"},
    "build_and_flash": {"code_rev": "c0de002", "board": "pod"},
}

# Anything that would hand out a shell or run arbitrary programs.
SHELL_WORDS = re.compile(
    r"shell|bash|zsh|\bsh\b|exec|eval|subprocess|spawn|popen|terminal|tty|command|\bcmd\b"
    r"|sudo|ssh|script|argv|run_any|system",
    re.IGNORECASE,
)


# --- the catalog --------------------------------------------------------------------------


def test_catalog_matches_interfaces_section_11():
    names = [spec.name for spec in TOOL_CATALOG]
    assert len(names) == len(set(names))
    assert {s.name for s in TOOL_CATALOG if s.tier == "read"} == READ_TOOLS
    assert {s.name for s in TOOL_CATALOG if s.tier == "act"} == ACT_TOOLS


def test_no_tool_offers_a_shell():
    for spec in TOOL_CATALOG:
        assert not SHELL_WORDS.search(spec.name), spec.name
        assert not SHELL_WORDS.search(spec.summary), (spec.name, spec.summary)
        for param in spec.params["properties"]:
            assert not SHELL_WORDS.search(param), (spec.name, param)


def test_shell_check_would_catch_one():
    for name in ("shell", "run_shell", "exec", "bash", "run_command", "eval_python"):
        assert SHELL_WORDS.search(name), name


@pytest.mark.parametrize("spec", TOOL_CATALOG, ids=lambda s: s.name)
def test_params_are_closed_objects_and_returns_open(spec):
    assert spec.params["type"] == "object"
    assert spec.params["additionalProperties"] is False
    assert set(spec.params["required"]) <= set(spec.params["properties"])
    assert spec.returns["type"] == "object"
    assert spec.returns.get("additionalProperties", True) is not False
    assert set(spec.returns["required"]) <= set(spec.returns["properties"])
    assert spec.summary.strip()


@pytest.mark.parametrize("spec", TOOL_CATALOG, ids=lambda s: s.name)
def test_spec_round_trips(spec):
    assert ToolSpec.model_validate_json(spec.model_dump_json()) == spec


def test_tool_lookup():
    assert tool("read_file").tier == "read"
    assert tool("build_and_flash").tier == "act"
    with pytest.raises(KeyError, match="unknown tool 'shell'"):
        tool("shell")


# --- validate_args ------------------------------------------------------------------------


def test_every_tool_has_a_good_example():
    assert set(GOOD_ARGS) == {s.name for s in TOOL_CATALOG}


@pytest.mark.parametrize(("name", "args"), sorted(GOOD_ARGS.items()))
def test_good_args_pass(name, args):
    assert validate_args(tool(name), args) == []


def test_optional_args_may_be_left_out():
    assert validate_args(tool("read_logs"), {"since_s": 0, "max_lines": 1}) == []
    assert validate_args(tool("grab_frame"), {}) == []
    assert validate_args(tool("run_tests_scratch"), {"timeout_s": 30}) == []


@pytest.mark.parametrize(
    ("name", "args", "problem"),
    [
        ("read_logs", {"since_s": 300}, "missing required 'max_lines'"),
        ("read_logs", {"since_s": 300, "max_lines": 10, "cmd": "rm -rf /"}, "unknown argument"),
        ("read_logs", {"since_s": "300", "max_lines": 10}, "expected number, got str"),
        ("read_logs", {"since_s": True, "max_lines": 10}, "expected number, got bool"),
        ("read_logs", {"since_s": 300, "max_lines": 2.5}, "expected integer, got float"),
        ("read_logs", {"since_s": -1, "max_lines": 10}, "below the minimum"),
        ("read_logs", {"since_s": 300, "max_lines": 5000}, "above the maximum"),
        ("read_logs", {"since_s": 300, "max_lines": 10, "grep": ""}, "shorter than"),
        ("read_logs", {"since_s": float("nan"), "max_lines": 10}, "expected number"),
        ("read_file", {"path": "/etc/passwd", "max_bytes": 10}, "does not match"),
        ("read_file", {"path": "../secrets.env", "max_bytes": 10}, "does not match"),
        ("read_file", {"path": "control/../../x", "max_bytes": 10}, "does not match"),
        ("read_file", {"path": "control/..", "max_bytes": 10}, "does not match"),
        ("set_mode", {"mode": "turbo"}, "not one of"),
        ("rollback", {"target": "everything"}, "not one of"),
        ("run_tests_scratch", {"timeout_s": 30, "tests": ["ok", "bad; rm"]}, "args.tests[1]"),
        ("run_tests_scratch", {"timeout_s": 30, "tests": "tests/"}, "expected array"),
        ("stack_dump", {"target": "Healer Process"}, "does not match"),
        ("apply_overlay", {"proposal_id": "prop_1"}, "missing required 'code_patch'"),
        ("park", {"reason": "x" * 501}, "longer than"),
        ("list_devices", {"all": True}, "unknown argument 'all'"),
    ],
)
def test_bad_args_are_reported(name, args, problem):
    problems = validate_args(tool(name), args)
    assert any(problem in p for p in problems), problems


def test_args_that_are_not_an_object():
    assert validate_args(tool("park"), ["servo 4"]) == ["args: expected object, got list"]
    assert validate_args(tool("list_devices"), None) == ["args: expected object, got NoneType"]


def test_every_problem_is_listed():
    problems = validate_args(tool("read_file"), {"path": "/x", "extra": 1})
    assert len(problems) == 3  # missing max_bytes, bad path, unknown extra


def test_whole_float_counts_as_integer():
    assert validate_args(tool("read_logs"), {"since_s": 1, "max_lines": 10.0}) == []


def test_path_may_contain_dots_that_arent_a_parent_segment():
    assert validate_args(tool("read_file"), {"path": "a/..b/.c", "max_bytes": 1}) == []


@pytest.mark.parametrize(
    "path", ["control/hold.py", "tests/test_hold.py", "_private/x.py", "README.md", "a-b/c_d.e"]
)
def test_good_paths(path):
    assert validate_args(tool("read_file"), {"path": path, "max_bytes": 1}) == []


@pytest.mark.parametrize(
    "path",
    [
        "~",
        "~/x",
        "..\\x",
        "C:/x",
        "\\etc\\passwd",
        "a\x00b",
        "a//b",
        "./x",
        "a/",
        ".git/config",
        "a/.git/HEAD",
        "a/./b",
        "..",
        ".",
        "-x",
        "a b",
        "control/hold.py\n",
    ],
)
def test_bad_paths(path):
    problems = validate_args(tool("read_file"), {"path": path, "max_bytes": 1})
    assert any("does not match" in p for p in problems), (path, problems)


def test_a_trailing_newline_never_passes_an_anchored_pattern():
    assert validate_args(tool("grab_frame"), {"camera": "a\n"})
    assert validate_args(tool("grab_frame"), {"camera": "a"}) == []


ID_FIELDS = [
    ("build_and_flash", "code_rev", {}),
    ("rollback", "to", {"target": "code"}),
    ("apply_overlay", "proposal_id", {"code_patch": PATCH}),
]


@pytest.mark.parametrize(("name", "field", "rest"), ID_FIELDS)
@pytest.mark.parametrize("value", ["c0de002", "v1.2.3", "prop_1", "7", "a" * 64])
def test_good_ids(name, field, rest, value):
    assert validate_args(tool(name), rest | {field: value}) == []


@pytest.mark.parametrize(("name", "field", "rest"), ID_FIELDS)
@pytest.mark.parametrize(
    "value",
    ["", "-rf", "--upload-pack=x", "..", ".hidden", "a b", "c0de;rm", "a/b", "a" * 65, "rev\n"],
)
def test_bad_ids(name, field, rest, value):
    assert validate_args(tool(name), rest | {field: value}), value


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("build_and_flash", {"code_rev": "c0de002", "board": "-x"}),
        ("stack_dump", {"target": "-p"}),
        ("run_tests_scratch", {"timeout_s": 30, "tests": ["-pevil"]}),
        ("run_tests_scratch", {"timeout_s": 30, "tests": ["tests/../../x.py"]}),
    ],
)
def test_names_and_test_ids_cant_pass_for_options_or_escape(name, args):
    assert validate_args(tool(name), args)


# --- grants -------------------------------------------------------------------------------


def test_every_act_tool_has_a_grant_and_read_tools_none():
    for spec in TOOL_CATALOG:
        if spec.tier == "act":
            assert spec.grant and spec.grant.startswith("act:"), spec.name
        else:
            assert spec.grant is None, spec.name
    grants = [s.grant for s in TOOL_CATALOG if s.grant]
    assert len(grants) == len(set(grants)) == len(ACT_TOOLS)


@pytest.mark.parametrize(
    ("tier", "grant"), [("act", None), ("act", ""), ("act", "flash"), ("read", "act:x")]
)
def test_spec_rejects_a_wrong_grant(tier, grant):
    spec = tool("park").model_dump() | {"tier": tier, "grant": grant}
    with pytest.raises(ValidationError, match="grant"):
        ToolSpec.model_validate(spec)


# --- ToolCall and ToolResult --------------------------------------------------------------


def test_call_and_result_directions():
    assert issubclass(ToolSpec, CloudModel) and issubclass(ToolCall, CloudModel)
    assert issubclass(ToolResult, DeviceModel)
    assert ToolResult.model_config["extra"] == "allow"


def test_call_round_trips_and_ignores_extras():
    call = ToolCall.model_validate(
        {"call_id": "c1", "name": "read_file", "args": GOOD_ARGS["read_file"], "x": 1}
    )
    assert call.lease_id is None and "x" not in call.model_dump()
    assert ToolCall.model_validate_json(call.model_dump_json()) == call
    assert validate_args(tool(call.name), call.args) == []


def test_call_null_args_read_as_empty():
    assert (
        ToolCall.model_validate({"call_id": "c1", "name": "list_devices", "args": None}).args == {}
    )


@pytest.mark.parametrize("missing", ["call_id", "name"])
def test_call_requires(missing):
    body = {"call_id": "c1", "name": "park"}
    del body[missing]
    with pytest.raises(ValidationError):
        ToolCall.model_validate(body)


def test_result_keeps_extras_and_reads_error_leniently():
    r = ToolResult.model_validate(
        {"call_id": "c1", "ok": True, "result": {"parked": True}, "took_ms": 812}
    )
    assert r.error == "" and r.model_extra == {"took_ms": 812}
    assert ToolResult.model_validate_json(r.model_dump_json()) == r
    bad = ToolResult.model_validate({"call_id": "c2", "ok": False, "error": None})
    assert bad.error == "" and bad.result is None
    assert ToolResult.model_validate({"call_id": "c3", "ok": False, "error": 13}).error == "13"


def test_result_requires_ok():
    with pytest.raises(ValidationError):
        ToolResult.model_validate({"call_id": "c1"})


def test_models_are_in_the_schema_list():
    for name in ("ToolSpec", "ToolCall", "ToolResult"):
        assert name in MODELS
        MODELS[name].model_json_schema()
