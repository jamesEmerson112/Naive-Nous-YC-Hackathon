"""ManagerClient (armsim/manager.py) against the fake Hermes API server (tests/fake_manager.py), one test
per mode, plus the plan check on what came back. No Hermes, no key, no network beyond 127.0.0.1.
Run from prototypes/arm-sim:

    PYTHONPATH=. .venv/bin/python tests/test_manager.py      (or: python -m pytest tests/)
"""
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import fake_manager  # noqa: E402
from armsim.escalation import SAFETY, check_plan  # noqa: E402
from armsim.manager import ManagerClient, fake_client  # noqa: E402
from test_escalation import ESC, STATE  # noqa: E402


def client(mode, *, deadline=None, **opts):
    srv, url = fake_manager.start(mode, **opts)
    return srv, ManagerClient(url, key=opts.get("key"), poll_s=0.1), (deadline or 10)


def ask(mode, **opts):
    srv, m, deadline = client(mode, **opts)
    t0 = time.time()
    try:
        text, why = m.ask(ESC, deadline_s=deadline)
    finally:
        if srv:
            srv.shutdown()
    return text, why, time.time() - t0, m, srv


def check(text):
    return check_plan(text, ESC, {**STATE, "now": time.time(), "asked_at": time.time() - 1})


def test_good_answers_a_plan_that_passes_the_check():
    text, why, secs, m, srv = ask("good")
    assert text and "completed" in why and secs < 5, (text, why, secs)
    assert check(text) == (True, "ok"), check(text)
    assert json.loads(text)["action"] == "restart_sensor"
    post = next(r for r in srv.requests if r["method"] == "POST")
    body = json.loads(post["body"])
    assert post["headers"]["Idempotency-Key"] == ESC["incident_id"] and body["session_id"] == ESC["incident_id"]
    assert json.loads(body["input"]) == ESC and "arm-fixplan/v1" in body["instructions"]
    assert body["conversation_history"] == [], body        # caller-supplied history: delegation runs inline (H:L39622-39628)
    assert m.last["run_id"].startswith("run_") and m.last["status"] == "completed"
    assert "stop" not in m.last and not srv.stops         # a completed run is never stopped


def test_bad_json_comes_back_and_the_check_refuses_it():
    text, why, secs, m, srv = ask("bad_json")
    assert text and "completed" in why, why
    ok, reason = check(text)
    assert not ok and "not one JSON object" in reason, reason


def test_off_list_is_refused_by_the_check():
    text, why, secs, m, srv = ask("off_list")
    ok, reason = check(text)
    assert not ok and "not on this incident's list" in reason, reason


def test_unsafe_is_refused_as_a_safety_change():
    text, why, secs, m, srv = ask("unsafe")
    ok, reason = check(text)
    assert not ok and reason.startswith(SAFETY), reason


def test_slow_gives_up_at_the_deadline():
    text, why, secs, m, srv = ask("slow", deadline=3)
    assert text is None and "within its 3 s deadline" in why, why
    assert 2.8 <= secs < 4.5, secs
    polls = [r for r in srv.requests if r["method"] == "GET"]
    assert len(polls) >= 5, len(polls)                     # it kept polling until the deadline
    assert srv.stops == [m.last["run_id"]] and m.last["stop"] == 200, (srv.stops, m.last)   # finding 5: stopped


def test_429_backs_off_then_gets_the_same_plan():
    text, why, secs, m, srv = ask("429", n429=2)
    assert text and check(text)[0], (text, why)
    assert m.last["http"] == [429, 429, 202] and secs >= 1.9, (m.last, secs)   # Retry-After: 1, twice


def test_429_past_the_deadline_gives_up():
    text, why, secs, m, srv = ask("429", n429=100, deadline=3)
    assert text is None and "didn't take the run" in why and secs < 4.5, (why, secs)


def test_500_fails_fast():
    text, why, secs, m, srv = ask("500")
    assert text is None and "HTTP 500" in why and secs < 1.5, (why, secs)
    assert len([r for r in srv.requests if r["method"] == "POST"]) == 1


def test_a_failed_run_gives_up():
    text, why, secs, m, srv = ask("failed")
    assert text is None and "ended failed" in why, why
    assert not srv.stops and "stop" not in m.last, srv.stops     # it is over: nothing to stop


def test_connection_refused_fails_fast():
    text, why, secs, m, srv = ask("refused")
    assert text is None and "isn't reachable" in why and secs < 1.5, (why, secs)


def test_a_wrong_key_is_401_and_the_right_one_works():
    srv, url = fake_manager.start("good", key="s3cret")
    try:
        text, why = ManagerClient(url, key="wrong", poll_s=0.1).ask(ESC, deadline_s=5)
        assert text is None and "HTTP 401" in why, why
        text, why = ManagerClient(url, key="s3cret", poll_s=0.1).ask(ESC, deadline_s=5)
        assert text and check(text)[0], why
    finally:
        srv.shutdown()


def test_a_retried_post_gets_the_same_run_back():
    srv, url = fake_manager.start("good", delay=0.0)
    try:
        m = ManagerClient(url, poll_s=0.1)
        m.ask(ESC, deadline_s=5)
        first = m.last["run_id"]
        m.ask(ESC, deadline_s=5)
        assert m.last["run_id"] == first and m.last["http"] == [202], m.last
        text, why = m.ask({**ESC, "deadline_s": 30}, deadline_s=5)      # same key, another body: 409
        assert text is None and "HTTP 409" in why, why
    finally:
        srv.shutdown()


def test_the_dispatcher_fake_is_the_same_server_over_http():
    m, srv = fake_client("good", delay=0.0)
    try:
        text, why = m.ask(ESC, deadline_s=5)
        assert m.label == "fake" and m.url.startswith("http://127.0.0.1:") and check(text)[0], (m.url, why)
    finally:
        srv.shutdown()


def test_a_trickling_server_is_cut_at_the_wall_clock_deadline():
    # finding 4: urllib's timeout is per socket read; a body that trickles one byte every 0.4 s used to run
    # 22.8 s past a 3 s deadline (the reviewer's probe). Each call now runs in a thread joined for what's left.
    text, why, secs, m, srv = ask("trickle", deadline=3)
    assert text is None and "within its 3 s deadline" in why, why
    assert 2.8 <= secs < 3.0 + 2.0 + 1.0, secs                # the deadline, plus the stop's own 2 s budget at most
    assert srv.stops == [m.last["run_id"]], srv.stops         # and the run it gave up on was stopped (finding 5)


def test_the_stop_is_bounded_even_when_the_server_trickles():
    srv, url = fake_manager.start("trickle")
    try:
        m = ManagerClient(url, poll_s=0.1)
        t0 = time.time()
        code = m.stop("run_nope", timeout=1.0)                # 404 from the fake, answered at once
        assert code == 404 and time.time() - t0 < 1.5, code
    finally:
        srv.shutdown()
    t0 = time.time()
    got = ManagerClient(fake_manager.start("refused")[1]).stop("run_x")
    assert isinstance(got, str) and "Error" in got and time.time() - t0 < 2.5, got   # never raises


def test_an_error_that_is_a_string_does_not_crash_the_client():
    text, why, secs, m, srv = ask("errstr")                  # finding 10: {"error": "text"} on a 400
    assert text is None and why == "the manager refused the run: HTTP 400 (fake manager: Too many concurrent runs (max 2))", why
    assert ManagerClient._error_text({"error": {"message": "m"}}) == "m"
    assert ManagerClient._error_text({"error": None}) is None and ManagerClient._error_text(None) is None


def test_toolsets_clean_dirty_and_unreadable():
    from armsim.manager import toolset_problems
    srv, url = fake_manager.start("good", key="k")
    try:
        bad, why = ManagerClient(url, key="k").check_tools()
        assert bad == [] and "delegation (delegate_task), memory (memory) and skills" in why, (bad, why)
        bad, why = ManagerClient(url, key="wrong").check_tools()       # 401: can't read it, fail closed
        assert bad is None and "HTTP 401" in why, (bad, why)
    finally:
        srv.shutdown()
    srv, url = fake_manager.start("good", toolsets="dirty")
    try:
        bad, why = ManagerClient(url).check_tools()
        assert bad and any("terminal is enabled" in b for b in bad) and any("so101_arm is enabled" in b for b in bad), bad
        assert any("arm_restart_sensor" in b and "reaches the arm" in b for b in bad), bad
    finally:
        srv.shutdown()
    for ts in ("garbage", "missing"):
        srv, url = fake_manager.start("good", toolsets=ts)
        try:
            bad, why = ManagerClient(url).check_tools()
            assert bad is None, (ts, bad, why)
        finally:
            srv.shutdown()
    bad, why = ManagerClient(fake_manager.start("refused")[1]).check_tools()
    assert bad is None and "can't be read" in why, why
    # the shapes: a {"data": [...]} wrapper is fine; a delegation toolset that sneaks in an arm tool is not
    D = {"name": "delegation", "enabled": True, "tools": ["delegate_task"]}
    assert toolset_problems({"data": [D]}) == []
    assert toolset_problems([{"name": "delegation", "enabled": True, "tools": ["delegate_task", "arm_wave"]}]) == \
        ["toolset delegation exposes arm_wave, not only its own delegate_task"]
    assert toolset_problems([D, {"name": "file", "enabled": True, "tools": ["read_file", "write_file", "patch"]}]) == \
        ["toolset file is enabled (patch, read_file, write_file)"]


def test_toolsets_the_round_two_shapes_all_refuse():
    # round 2, finding 5 (the reviewer's p4_toolsets.py): each of these passed the first rule
    from armsim.manager import toolset_problems
    D, M = {"name": "delegation", "enabled": True, "tools": ["delegate_task"]}, {"name": "memory", "enabled": True,
                                                                                  "tools": ["memory"]}
    refused = {
        "an empty list": ([], "delegation is not on"),
        "memory only": ([M], "delegation is not on"),
        "so101_arm with no enabled key": ([D, M, {"name": "so101_arm", "tools": ["arm_approach"]}],
                                          "so101_arm is not off (enabled: null)"),
        "terminal with enabled null": ([D, {"name": "terminal", "enabled": None, "tools": ["terminal"]}],
                                       "terminal is not off"),
        "terminal with enabled 'false'": ([D, {"name": "terminal", "enabled": "false", "tools": ["terminal"]}],
                                          'terminal is not off (enabled: "false")'),
        "memory with execute_code": ([D, {**M, "tools": ["memory", "execute_code"]}], "memory exposes execute_code"),
        "memory with process_manage": ([D, {**M, "tools": ["memory", "process_manage"]}], "exposes process_manage"),
        "memory with cronjob_manage": ([D, {**M, "tools": ["memory", "cronjob_manage"]}], "exposes cronjob_manage"),
        "memory with read_file": ([D, {**M, "tools": ["memory", "read_file"]}], "exposes read_file"),
        "memory with an MCP board tool": ([D, {**M, "tools": ["memory", "mcp_naive_board_move_card"]}],
                                          "exposes mcp_naive_board_move_card"),
        "delegation with browser_exec": ([{**D, "tools": ["delegate_task", "browser_exec"]}, M], "exposes browser_exec"),
        "tools as a string": ([D, {**M, "tools": "terminal"}], "tools aren't a list of names"),
        "an upper-case arm tool": ([D, {**M, "tools": ["memory", "Arm_approach"]}], "exposes Arm_approach"),
        "a docs-example core toolset (H:L39805)": ([D, {"name": "core", "enabled": True, "tools": ["read_file", "write_file"]}],
                                                   "toolset core is enabled"),
        "the api_server default with the first config": (fake_manager.TOOLSETS["default"], "toolset cronjob is enabled"),
    }
    for name, (data, want) in refused.items():
        bad = toolset_problems(data)
        assert bad and any(want in b for b in bad), (name, bad)
    bad = toolset_problems(fake_manager.TOOLSETS["default"])
    for ts in ("vision", "image_gen", "todo", "cronjob", "session_search"):
        assert any(b.startswith(f"toolset {ts} is enabled") for b in bad), (ts, bad)
    assert not any("skills" in b or "memory" in b or "delegation" in b for b in bad), bad
    # passes: delegation alone, the three allowed ones, an enabled toolset with no tools (context_engine,
    # H:L56049), and off toolsets whatever they list
    for data in ([D], [D, M, {"name": "skills", "enabled": True, "tools": ["skill_manage", "skill_view", "skills_list"]}],
                 [D, {"name": "context_engine", "enabled": True, "tools": []}],
                 [D, {"name": "terminal", "enabled": False, "tools": ["terminal"]}], fake_manager.TOOLSETS["clean"]):
        assert toolset_problems(data) == [], (data, toolset_problems(data))


# ---- hermes-manager/: setup.sh, config_pairs.py, check.sh, ask.sh, in a copy of the folder ------------
HM = HERE.parent / "hermes-manager"


def hm_copy():
    """hermes-manager/ in a temp dir, with armsim/manager.py beside it (check.sh loads the worker's rule)."""
    d = Path(tempfile.mkdtemp(prefix="armsim-hm-test-"))
    shutil.copytree(HM, d / "hm", ignore=shutil.ignore_patterns("manager.key.env", "__pycache__"))
    (d / "armsim").mkdir()
    shutil.copy(HERE.parent / "armsim" / "manager.py", d / "armsim" / "manager.py")
    return d, d / "hm"


def test_config_pairs_reads_config_manager_yaml():
    spec = importlib.util.spec_from_file_location("config_pairs", HM / "config_pairs.py")
    cp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cp)
    got = dict(cp.pairs((HM / "config.manager.yaml").read_text()))
    assert got["delegation.gateway_mode"] == "inline" and got["gateway.api_server.max_concurrent_runs"] == "2", got
    off = json.loads(got["agent.disabled_toolsets"])
    assert off == ["terminal", "code_execution", "file", "browser", "web", "vision", "image_gen", "todo", "cronjob",
                   "session_search", "so101_arm", "mcp-naive_board"], got
    assert json.loads(got["platform_toolsets.api_server"]) == ["delegation", "memory", "skills", "no_mcp"], got
    assert got["skills.write_approval"] == "true", got
    assert got["approvals.unattended_mode"] == "deny" and len(got) == 12, got
    # round 2, finding 9: what the shipped config leaves on, out of the API server's default, passes the worker's rule
    from armsim.manager import toolset_problems
    left = fake_manager.listing(set(fake_manager.API_SERVER_DEFAULT) - set(off))
    assert toolset_problems(left) == [], toolset_problems(left)
    assert not {"coding", "safe", "debugging"} & set(off), off          # composites would strip the allowed tools
    for bad in ("a:\n   b: 1\n", "a: 'quoted'\n", "a: [x,\n", "- item\n"):
        try:
            cp.pairs(bad)
            raise AssertionError(f"accepted {bad!r}")
        except ValueError:
            pass


STAND_IN = """#!/usr/bin/env bash
# a stand-in `hermes`: logs its argv; `profile create` makes a cloned profile; `config set` can fail on demand
echo "$*" >> "$HERMES_HOME/calls.log"
case "$*" in
  --version) echo "Hermes Agent v0.23.0 (stand-in)";;
  "profile create manager"*) p="$HERMES_HOME/profiles/manager"; mkdir -p "$p/memories"
     echo "the arm commander's soul" > "$p/SOUL.md"; echo "commander memory" > "$p/memories/MEMORY.md"
     echo "user memory" > "$p/memories/USER.md"; echo "API_SERVER_KEY=default-key" > "$p/.env";;
  "-p manager config set"*) [[ -n "${FAIL_SET:-}" && "$*" == *"$FAIL_SET"* ]] && exit 3; exit 0;;
esac
"""


def run_setup(env_extra=None):
    d, hm = hm_copy()
    home, bin_ = d / "hermes-home", d / "bin"
    home.mkdir()
    bin_.mkdir()
    (bin_ / "hermes").write_text(STAND_IN)
    (bin_ / "hermes").chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "HERMES_HOME": str(home), **(env_extra or {})}
    r = subprocess.run(["bash", str(hm / "setup.sh"), "b", "127.0.0.1"], capture_output=True, text=True, env=env,
                       timeout=60)
    return r, home, hm


def test_setup_sh_applies_the_config_and_blanks_the_cloned_memory():
    r, home, hm = run_setup()
    assert r.returncode == 0, r.stdout + r.stderr
    calls = (home / "calls.log").read_text().splitlines()
    sets = [c for c in calls if c.startswith("-p manager config set ")]
    assert len(sets) == 12 and "-p manager config set delegation.gateway_mode inline" in sets, calls
    assert '-p manager config set agent.disabled_toolsets ["terminal", "code_execution", "file", "browser", "web", ' \
           '"vision", "image_gen", "todo", "cronjob", "session_search", "so101_arm", "mcp-naive_board"]' in sets, sets
    assert '-p manager config set platform_toolsets.api_server ["delegation", "memory", "skills", "no_mcp"]' in sets
    assert "-p manager config set skills.write_approval true" in sets, sets
    assert "-p manager curator lock arm-incident-manager" in calls, calls      # the skill can't be changed
    p = home / "profiles" / "manager"
    assert not (p / "memories" / "MEMORY.md").exists() and not (p / "memories" / "USER.md").exists()
    assert (p / "memories" / "MEMORY.md.cloned-from-default").read_text() == "commander memory\n"
    soul = (p / "SOUL.md").read_text()
    assert "incident manager" in soul and "skill_view" in soul and (p / "SOUL.md.cloned-from-default").exists(), soul
    env = (p / ".env").read_text()
    assert "default-key" not in env and "API_SERVER_KEY=" in env and "API_SERVER_HOST=127.0.0.1" in env, env
    assert stat.S_IMODE((hm / "manager.key.env").stat().st_mode) == 0o600
    r, home, hm = run_setup({"FAIL_SET": "gateway_mode"})          # a failing config set stops the script
    assert r.returncode == 1 and "FAILED: hermes -p manager config set delegation.gateway_mode" in r.stdout, r.stdout


def test_check_sh_and_ask_sh_against_the_fake():
    # round 2, finding 10: check.sh applies the worker's own rule, so "default" (the API server's default with
    # the first config) fails check.sh exactly as it fails the preflight
    for toolsets, want_rc in (("clean", 0), ("dirty", 1), ("default", 1)):
        srv, url = fake_manager.start("good", key="k", toolsets=toolsets, delay=0.0)
        try:
            d, hm = hm_copy()
            (hm / "manager.key.env").write_text(f"ARMSIM_MANAGER_URL={url}\nARMSIM_MANAGER_KEY=k\n")
            r = subprocess.run(["bash", str(hm / "check.sh")], capture_output=True, text=True, timeout=60)
            assert r.returncode == want_rc, (toolsets, r.stdout + r.stderr)
            assert f"GET {url}/health (H:L39505)" in r.stdout and "HTTP 200" in r.stdout, r.stdout
            if toolsets == "default":
                assert "NOT ALLOWED" in r.stdout and "cronjob is enabled" in r.stdout and "vision is enabled" in r.stdout, \
                    r.stdout
            if toolsets == "clean":
                assert "the worker's toolset rule" in r.stdout and "passes" in r.stdout, r.stdout
            if toolsets == "clean":
                code = [ln for ln in (hm / "ask.sh").read_text().splitlines() if not ln.lstrip().startswith("#")]
                assert not [ln for ln in code if re.search(r"\bjq\b", ln)], "ask.sh still calls jq"   # macOS < 15 has none
                r = subprocess.run(["bash", str(hm / "ask.sh"), str(hm / "samples" / "esc-us_stuck.json")],
                                   capture_output=True, text=True, timeout=60)
                assert r.returncode == 0 and "status: completed" in r.stdout and "output is one JSON object" in r.stdout, \
                    r.stdout + r.stderr
                body = json.loads(next(q for q in srv.requests if q["method"] == "POST")["body"])
                assert body["conversation_history"] == [] and body["session_id"].startswith("inc-manual-"), body
        finally:
            srv.shutdown()
    # branch (a): the /p/manager prefix's /health doesn't answer, so the listener's own /health is probed
    srv, url = fake_manager.start("good", key="k")
    try:
        d, hm = hm_copy()
        (hm / "manager.key.env").write_text(f"ARMSIM_MANAGER_URL={url}/p/manager\nARMSIM_MANAGER_KEY=k\n")
        r = subprocess.run(["bash", str(hm / "check.sh")], capture_output=True, text=True, timeout=60)
        assert f"GET {url}/health (H:L39505)" in r.stdout and "HTTP 200" in r.stdout, r.stdout
    finally:
        srv.shutdown()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            t0 = time.time()
            fn()
            print(f"ok  {name}  ({time.time() - t0:.1f} s)")
