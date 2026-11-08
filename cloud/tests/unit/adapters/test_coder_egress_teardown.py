"""B5 review finding 2: no orphaned LLM proxy keeps the real key. Every egress container and
network is labelled per run; SIGTERM/SIGINT tear down open runs; worker startup prunes what a
killed worker left behind; the proxy is told its own deadline. Docker is `FakeDocker`."""

import os
import signal
import sys
from pathlib import Path

import pytest

from romulus_cloud import worker
from romulus_cloud.adapters import coder_egress
from romulus_cloud.adapters.coder import LocalAgent, Sandbox, build_coder
from romulus_cloud.adapters.coder_egress import LABEL, ROLE_LABEL, RUN_LABEL, Egress
from romulus_cloud.services import batch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fixtures"))
import coder_fakes as fakes  # noqa: E402

KEY = "sk-test-SECRET-123"


def open_run(egress: Egress, **kw):
    args = {"model": "m", "max_requests": 5, "ttl_s": 900, "base_env": {}} | kw
    return egress.open("https://llm.example/v1", KEY, **args)


@pytest.fixture
def restore_signals():
    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)
    coder_egress._ACTIVE.clear()


def test_every_container_and_network_of_a_run_is_labelled_and_removed():
    docker = fakes.FakeDocker()
    egress = Egress(image="romulus-coder:test", runner=docker)
    with open_run(egress, model="pinned-model", max_tokens=321, ttl_s=777) as net:
        run = net.agent_labels[RUN_LABEL]
        assert docker.networks[net.network] == {
            "romulus.coder": "egress",
            RUN_LABEL: run,
            ROLE_LABEL: "net",
        }
        assert docker.containers[net.proxy][ROLE_LABEL] == "proxy"
        assert docker.containers[net.proxy][RUN_LABEL] == run
        assert net.agent_labels == {"romulus.coder": "egress", RUN_LABEL: run, ROLE_LABEL: "agent"}
        env = docker.call("create")["env"]
        assert env["LLM_PROXY_MODEL"] == "pinned-model"
        assert env["LLM_PROXY_MAX_TOKENS"] == "321"
        assert env["LLM_PROXY_TTL_S"] == "777"
        assert run in coder_egress._ACTIVE
    assert docker.left_behind() == []
    assert run not in coder_egress._ACTIVE


def test_local_agent_pins_the_model_caps_tokens_and_sets_the_proxy_deadline():
    docker = fakes.FakeDocker(agent=fakes.fix_like_a_careful_engineer)
    agent = LocalAgent(
        base_url="https://llm.example/v1",
        model="pinned-model",
        api_key=KEY,
        sandbox=Sandbox(image="romulus-coder:test", runner=docker),
        time_limit_s=100,
        test_timeout_s=50,
        max_tokens=777,
    )
    agent.draft(fakes.TOY_PATCH, {"id": "inc-1"}, "c0de001", fakes.toy_files())
    proxy_env = docker.call("create")["env"]
    assert proxy_env["LLM_PROXY_MODEL"] == "pinned-model"
    assert proxy_env["LLM_PROXY_MAX_TOKENS"] == "777"
    assert proxy_env["LLM_PROXY_TTL_S"] == str(100 + 50 + 60 + 120)  # job timeout + margin
    run = docker.run_call
    assert run["env"]["CODER_MAX_TOKENS"] == "777"
    labels = fakes._labels(run["argv"])
    assert labels[ROLE_LABEL] == "agent" and labels["romulus.coder"] == "egress"
    assert docker.left_behind() == []


def test_the_token_cap_setting_reaches_the_agent(make_settings):
    settings = make_settings().model_copy(
        update={
            "coder_provider": "local_agent",
            "llm_base_url": "https://llm.example/v1",
            "llm_model": "m",
            "coder_max_tokens": 123,
        }
    )
    assert batch._build_coder(settings).max_tokens == 123
    assert build_coder("local_agent", base_url="https://x/v1", model="m").max_tokens == 2000


def test_a_timed_out_agent_that_outlives_its_cli_is_removed_with_its_run():
    docker = fakes.FakeDocker()
    egress = Egress(image="romulus-coder:test", runner=docker)
    with open_run(egress) as net:
        docker.containers["romulus-coder-stuck"] = dict(net.agent_labels)
    assert docker.left_behind() == []
    assert ["network", "rm", net.network] == docker.calls[-1]["argv"][1:]  # after the agent


def test_sigterm_mid_run_tears_the_run_down_before_exiting(restore_signals):
    docker = fakes.FakeDocker()
    egress = Egress(image="romulus-coder:test", runner=docker)
    coder_egress.install_signal_teardown()
    with pytest.raises(SystemExit) as exc:
        with open_run(egress) as net:
            assert docker.left_behind() == sorted([net.proxy]) + [net.network]
            os.kill(os.getpid(), signal.SIGTERM)
    assert exc.value.code == 128 + signal.SIGTERM
    assert docker.left_behind() == []


def test_sigint_also_tears_down(restore_signals):
    docker = fakes.FakeDocker()
    egress = Egress(image="romulus-coder:test", runner=docker)
    coder_egress.install_signal_teardown()
    ctx = open_run(egress)
    ctx.__enter__()  # a run in flight whose `finally` hasn't run
    with pytest.raises(SystemExit):
        os.kill(os.getpid(), signal.SIGINT)
    assert docker.left_behind() == []


def test_a_killed_worker_leaves_nothing_after_the_startup_prune(make_settings, restore_signals):
    """SIGKILL: no handler, no `finally`. The next worker's startup prune removes the proxy
    (still holding the key), the agent and the network."""
    docker = fakes.FakeDocker()
    egress = Egress(image="romulus-coder:test", runner=docker)
    open_run(egress).__enter__()
    with open_run(egress) as second:
        docker.containers["romulus-coder-agent"] = dict(second.agent_labels)
        coder_egress._ACTIVE.clear()  # the process is gone; its memory with it
        docker.containers["unrelated"] = {"other": "x"}  # not ours: untouched
        docker.networks["unrelated-net"] = {}
        settings = make_settings().model_copy(update={"coder_provider": "local_agent"})
        worker.egress_startup(settings, runner=docker)
        assert docker.left_behind() == ["unrelated", "unrelated-net"]
        assert signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL


def test_startup_does_nothing_without_the_real_coder(make_settings):
    docker = fakes.FakeDocker()
    docker.networks["romulus-coder-net-x"] = {"romulus.coder": "egress"}
    worker.egress_startup(make_settings(), runner=docker)
    assert docker.calls == [] and docker.left_behind() == ["romulus-coder-net-x"]


def test_prune_without_docker_is_a_no_op():
    def no_docker(argv, **kw):
        raise FileNotFoundError("docker")

    assert coder_egress.prune(no_docker) == 0


def test_prune_counts_what_it_removed():
    docker = fakes.FakeDocker()
    docker.containers["p"] = {"romulus.coder": "egress"}
    docker.networks["n"] = {"romulus.coder": "egress", RUN_LABEL: "r"}
    assert coder_egress.prune(docker) == 2
    assert coder_egress.prune(docker, label=f"{RUN_LABEL}=r") == 0
    assert LABEL == "romulus.coder=egress"
