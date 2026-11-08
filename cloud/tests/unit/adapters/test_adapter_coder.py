import sys
from pathlib import Path

import pytest

from romulus_cloud.adapters.coder import (
    CodeDraft,
    CoderError,
    FakeCoder,
    LocalAgent,
    Sandbox,
    build_coder,
    build_task_prompt,
    device_relative,
    harness_flags,
    safe_write,
)
from romulus_contracts.codepatch import summarize_code_patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fixtures"))
import coder_fakes as fakes  # noqa: E402

PATCH = (
    "--- a/control/gripper.py\n+++ b/control/gripper.py\n@@ -1 +1 @@\n-LIMIT = 900\n+LIMIT = 800\n"
)
KEY = "sk-test-SECRET-123"
INCIDENT = {"id": "inc-7", "device_id": "arm-1", "fault_kind": "dropped_object"}


def make_agent(docker: "fakes.FakeDocker", **kw) -> LocalAgent:
    return LocalAgent(
        base_url="https://llm.example/v1",
        model="test-model",
        api_key=KEY,
        sandbox=Sandbox(image="romulus-coder:test", runner=docker),
        **kw,
    )


def test_fake_coder_passes_patch_through_and_says_so():
    draft = FakeCoder().draft(PATCH, {"id": "inc-1"}, "rev-3")
    assert isinstance(draft, CodeDraft)
    assert draft.diff == PATCH
    assert draft.passed is True
    assert "pass-through: coding agent not wired" in draft.notes
    assert "rev-3" in draft.notes and "inc-1" in draft.notes


def test_factory():
    assert isinstance(build_coder("fake"), FakeCoder)
    agent = build_coder(
        "local_agent", "", "", base_url="https://llm.example/v1", model="m", api_key=KEY
    )
    assert isinstance(agent, LocalAgent)
    assert agent.sandbox.image == "romulus-coder" and agent.step_limit == 30
    with pytest.raises(CoderError, match="LLM_BASE_URL and LLM_MODEL"):
        build_coder("local_agent")
    with pytest.raises(CoderError, match="only mini-swe-agent"):
        build_coder("local_agent", "aider", base_url="u", model="m")
    with pytest.raises(NotImplementedError, match="M8 stub"):
        build_coder("naive_session")
    with pytest.raises(ValueError):
        build_coder("claude")


def test_prompt_has_the_incident_the_hint_and_the_rules():
    prompt = build_task_prompt(fakes.TOY_PATCH, INCIDENT, "c0de001")
    assert "c0de001" in prompt and "inc-7" in prompt and "dropped_object" in prompt
    assert fakes.TOY_PATCH.strip() in prompt
    for rule in ("clean, minimal fix", "regression test", "SPEC.md", "python -m pytest -q"):
        assert rule in prompt


def test_good_agent_run_returns_a_clean_diff_that_passes():
    docker = fakes.FakeDocker(agent=fakes.fix_like_a_careful_engineer)
    draft = make_agent(docker).draft(fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files())

    assert draft.passed is True, draft.test_output
    assert docker.run_call["stdin"] == build_task_prompt(fakes.TOY_PATCH, INCIDENT, "c0de001")
    # The diff is a code_patch: paths relative to device/, new files included, no caches.
    summary = summarize_code_patch(draft.diff)
    assert summary.ok, summary.problems
    assert summary.paths == ("control/gripper.py", "tests/test_regression_d1.py")
    assert "-        if dist_mm is not None and dist_mm < PROXIMITY_MM:" in draft.diff
    assert "__pycache__" not in draft.diff
    assert "passed" in draft.test_output
    for fact in ("7 steps (limit 30)", "exit Submitted", "1200 in / 300 out", "test-model"):
        assert fact in draft.notes
    assert "tests `python -m pytest -q`: exit 0" in draft.notes


def test_a_given_task_replaces_the_default_prompt():
    """The Fleet Doctor drafts from scratch: its own task text is the agent's whole prompt."""
    docker = fakes.FakeDocker(agent=fakes.fix_like_a_careful_engineer)
    task = "Write a fix for D1 from scratch and a test that reproduces it."
    draft = make_agent(docker).draft("", INCIDENT, "c0de001", fakes.toy_files(), task=task)
    assert docker.run_call["stdin"] == task
    assert draft.passed is True, draft.notes


def test_fake_coder_ignores_a_task():
    draft = FakeCoder().draft(PATCH, {"id": "inc-1"}, "rev-3", task="anything")
    assert draft.diff == PATCH and "pass-through" in draft.notes


def test_factory_passes_the_scratch_dir(tmp_path):
    agent = build_coder("local_agent", base_url="u", model="m", scratch_dir=str(tmp_path))
    assert agent.scratch_dir == str(tmp_path)


def test_agent_never_sees_git_metadata_or_the_verify_scripts():
    docker = fakes.FakeDocker(agent=fakes.fix_like_a_careful_engineer)
    draft = make_agent(docker).draft(fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files())
    assert docker.saw_git_in_work is False
    assert docker.saw_verify_in_work is False
    assert "verify/ held out" in draft.notes
    assert "advisory only" in draft.notes


def test_failing_tests_mean_not_passed():
    def breaks_idle(work: Path) -> None:
        gripper = work / "control" / "gripper.py"
        gripper.write_text(
            gripper.read_text().replace('return "IDLE", "open"', 'return "IDLE", "x"')
        )

    docker = fakes.FakeDocker(agent=breaks_idle)
    draft = make_agent(docker).draft(fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files())
    assert draft.passed is False
    assert "test_idle_stays_open_until_a_grasp" in draft.test_output
    assert "exit 1" in draft.notes and "the device tests did not pass" in draft.notes


def test_no_change_is_not_a_pass_even_if_tests_pass():
    draft = make_agent(fakes.FakeDocker()).draft(
        fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files()
    )
    assert draft.passed is False and draft.diff == ""
    assert "the agent changed nothing" in draft.notes


def test_a_symlink_left_in_work_fails_the_draft():
    def plants_a_link(work: Path) -> None:
        fakes.fix_like_a_careful_engineer(work)
        (work / "evil_link").symlink_to("/etc/passwd")

    draft = make_agent(fakes.FakeDocker(agent=plants_a_link)).draft(
        fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files()
    )
    assert draft.passed is False
    assert "evil_link is a symlink" in draft.notes
    assert "symlink (mode 120000)" in draft.notes  # the diff check catches it too


def test_a_nested_repo_fails_the_draft():
    def plants_a_repo(work: Path) -> None:
        fakes.fix_like_a_careful_engineer(work)
        (work / "sub" / ".git").mkdir(parents=True)
        (work / "sub" / ".git" / "config").write_text("[core]\n\tfsmonitor = touch /tmp/pwned\n")

    draft = make_agent(fakes.FakeDocker(agent=plants_a_repo)).draft(
        fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files()
    )
    assert draft.passed is False
    assert "sub/.git is a nested git repository" in draft.notes


def test_touching_test_config_or_existing_tests_is_flagged():
    def games_the_suite(work: Path) -> None:
        fakes.fix_like_a_careful_engineer(work)
        (work / "conftest.py").write_text('collect_ignore_glob = ["tests/test_g*"]\n')
        test = work / "tests" / "test_gripper.py"
        test.write_text(test.read_text() + "\n# weakened\n")

    draft = make_agent(fakes.FakeDocker(agent=games_the_suite)).draft(
        fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files()
    )
    assert "test_harness: conftest.py (added)" in draft.flags
    assert "existing_test: tests/test_gripper.py (modified)" in draft.flags
    assert not any("test_regression_d1" in f for f in draft.flags)  # new tests are welcome
    assert "flagged for review" in draft.notes


def test_timeout_kills_the_container_and_fails():
    docker = fakes.FakeDocker(agent=fakes.fix_like_a_careful_engineer, timed_out=True)
    agent = make_agent(docker, time_limit_s=5, test_timeout_s=5)
    draft = agent.draft(fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files())

    assert draft.passed is False
    assert "sandbox timed out without a result" in draft.notes
    assert "did not run" in draft.test_output
    run = docker.run_call
    kill = docker.call("kill")
    assert kill["argv"][1:] == ["kill", run["argv"][run["argv"].index("--name") + 1]]
    assert docker.calls.index(run) < docker.calls.index(kill)
    assert docker.calls[-1]["argv"][1:3] == ["network", "rm"]  # cleaned up after the kill
    assert draft.diff  # the partial work is still returned for the timeline


def test_sandbox_argv_mounts_only_work_and_keeps_the_key_out_of_argv():
    docker = fakes.FakeDocker(agent=fakes.fix_like_a_careful_engineer)
    make_agent(docker, step_limit=12).draft(fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files())
    call = docker.run_call
    argv = call["argv"]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv.count("-v") == 1 and argv[argv.index("-v") + 1].endswith(":/work")
    assert "ALL" == argv[argv.index("--cap-drop") + 1]
    assert argv[argv.index("--cpus") + 1] == "2" and argv[argv.index("--memory") + 1] == "2g"
    assert argv[-2:] == ["romulus-coder:test", "agent"]
    assert call["env"]["CODER_STEP_LIMIT"] == "12"


def test_agent_joins_only_an_internal_network_and_never_holds_the_real_key():
    """B5: the agent's docker command, its env and its argv carry no real key, and its one
    network is the internal one the egress proxy sits on."""
    docker = fakes.FakeDocker(agent=fakes.fix_like_a_careful_engineer)
    make_agent(docker, step_limit=12).draft(fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files())
    create_net = docker.call("network", "create")["argv"]
    assert (
        "--internal" in create_net and "com.docker.network.bridge.inhibit_ipv4=true" in create_net
    )
    network = create_net[-1]
    assert network.startswith("romulus-coder-net-")

    run = docker.run_call
    argv, env = run["argv"], run["env"]
    assert argv.count("--network") == 1 and argv[argv.index("--network") + 1] == network
    assert not any(a.startswith("--network=") or a in ("--net", "--add-host") for a in argv)
    assert KEY not in " ".join(argv)
    assert not any(KEY in v for v in env.values())  # nothing docker could copy in
    assert env["LLM_API_KEY"].startswith("romulus-job-") and env["LLM_API_KEY"] != KEY
    assert env["LLM_BASE_URL"] == "http://llm-proxy:8080/v1"
    assert "LLM_API_KEY" in argv and "LLM_BASE_URL" in argv  # by name only

    # The proxy holds the key (by name in argv), on the internal network plus bridge.
    create = docker.call("create")
    p_argv = create["argv"]
    assert KEY not in " ".join(p_argv) and "LLM_PROXY_KEY" in p_argv
    assert create["env"]["LLM_PROXY_KEY"] == KEY
    assert create["env"]["LLM_PROXY_TOKEN"] == env["LLM_API_KEY"]
    assert create["env"]["LLM_PROXY_UPSTREAM"] == "https://llm.example/v1"
    assert create["env"]["LLM_PROXY_MAX_REQUESTS"] == "29"  # 2 x 12 steps + 5
    assert p_argv[p_argv.index("--network") + 1] == network
    assert p_argv[p_argv.index("--network-alias") + 1] == "llm-proxy"
    proxy = p_argv[p_argv.index("--name") + 1]
    assert docker.call("network", "connect")["argv"][3:] == ["bridge", proxy]
    order = [c["argv"][1:3] for c in docker.calls]
    assert order.index(["start", proxy]) < order.index(argv[1:3])  # proxy before the agent
    assert ["rm", "-f"] in order and ["network", "rm"] in order  # torn down afterwards


def test_refused_proxy_requests_are_noted_and_flagged():
    logs = (
        "llm_proxy ready on :8080\nllm_proxy forwarded POST /v1/chat/completions -> 200\n"
        "llm_proxy refused GET /v1/models -> 405: method GET not allowed\n"
    )
    docker = fakes.FakeDocker(agent=fakes.fix_like_a_careful_engineer, proxy_logs=logs)
    draft = make_agent(docker).draft(fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files())
    assert "llm proxy: 1 forwarded, 1 refused" in draft.notes
    assert any(f.startswith("egress_refused: ") for f in draft.flags)


def test_egress_setup_failure_is_a_no_result_draft_and_runs_no_agent():
    docker = fakes.FakeDocker(agent=fakes.fix_like_a_careful_engineer, fail="network")
    draft = make_agent(docker).draft(fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files())
    assert draft.passed is False and draft.no_result is True  # the batch retries it later
    assert "egress setup failed" in draft.notes
    assert not any(c["argv"][1] == "run" for c in docker.calls)


def test_secrets_never_reach_notes_or_test_output():
    leaky = {
        "agent": "mini-swe-agent",
        "exit_status": "AuthenticationError",
        "error": f"bad key {KEY}",
        "steps": 1,
    }
    docker = fakes.FakeDocker(
        agent=fakes.fix_like_a_careful_engineer, agent_result=leaky, extra_output=f"env: {KEY}"
    )
    draft = make_agent(docker).draft(fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files())
    assert KEY not in draft.notes and KEY not in draft.test_output and KEY not in draft.diff
    assert "[redacted]" in draft.notes and "[redacted]" in draft.test_output
    assert draft.passed is False and "the LLM key appeared" in draft.notes


def test_a_key_written_into_the_code_is_redacted_from_the_diff_and_fails():
    def exfiltrates(work: Path) -> None:
        fakes.fix_like_a_careful_engineer(work)
        (work / "control" / "notes.py").write_text(f'KEY = "{KEY}"\n')

    draft = make_agent(fakes.FakeDocker(agent=exfiltrates)).draft(
        fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files()
    )
    assert KEY not in draft.diff and "[redacted]" in draft.diff
    assert KEY not in draft.notes + draft.test_output + "".join(draft.flags)
    assert draft.passed is False and "the LLM key appeared" in draft.notes


def test_harness_flags_ignore_case_and_dot_slash():
    flags = harness_flags(
        [
            ("M", "VERIFY/test_verify_d1.py"),
            ("A", "CONFTEST.py"),
            ("M", "./Pytest.ini"),
            ("M", "Tests/test_gripper.py"),
            ("A", "TESTS/test_new.py"),
        ]
    )
    assert flags == (
        "verify_scripts: VERIFY/test_verify_d1.py (modified)",
        "test_harness: CONFTEST.py (added)",
        "test_harness: ./Pytest.ini (modified)",
        "existing_test: Tests/test_gripper.py (modified)",
    )


def test_verify_dir_in_any_case_is_held_out_of_the_agents_copy():
    files = fakes.toy_files()
    files["device/Verify/test_more.py"] = b"def test_x():\n    pass\n"
    docker = fakes.FakeDocker(agent=fakes.fix_like_a_careful_engineer)
    seen = {}

    def spy(work: Path) -> None:
        seen["names"] = {p.name for p in work.iterdir()}
        fakes.fix_like_a_careful_engineer(work)

    docker.agent = spy
    make_agent(docker).draft(fakes.TOY_PATCH, INCIDENT, "c0de001", files)
    assert not {n.casefold() for n in seen["names"]} & {"verify"}


def test_safe_write_refuses_symlinks_and_escapes(tmp_path):
    root, outside = tmp_path / "work", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "vdir").symlink_to(outside)
    (root / "f.py").symlink_to(outside / "target.py")
    for rel in ("vdir/test_v.py", "f.py", "../x.py", "/abs.py"):
        with pytest.raises(CoderError, match="refusing to write"):
            safe_write(root, rel, b"x")
    assert list(outside.iterdir()) == []
    safe_write(root, "ok/new.py", b"fine")
    assert (root / "ok" / "new.py").read_bytes() == b"fine"


def test_bad_inputs_raise_coder_error():
    agent = make_agent(fakes.FakeDocker())
    with pytest.raises(CoderError, match="needs the code package"):
        agent.draft(fakes.TOY_PATCH, INCIDENT, "c0de001")
    with pytest.raises(CoderError, match="no files under device/"):
        agent.draft(fakes.TOY_PATCH, INCIDENT, "c0de001", {"README.md": b"x"})


def test_device_relative():
    assert device_relative("device/control/gripper.py") == "control/gripper.py"
    assert device_relative("./device/a.py") == "a.py"
    assert device_relative("romulus/patches/ov-1.diff") is None
    assert device_relative("device/../etc/passwd") is None
    assert device_relative("device") is None
