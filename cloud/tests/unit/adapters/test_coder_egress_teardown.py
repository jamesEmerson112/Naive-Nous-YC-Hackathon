"""B5 review finding 2: no orphaned LLM proxy keeps the real key. Every egress container and
network is labelled per run; SIGTERM/SIGINT tear down open runs; worker startup prunes what a
killed worker left behind (only runs whose owner is dead or past their deadline, never a
live worker's or a test's); the proxy is told its own deadline. Docker is `FakeDocker`."""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from romulus_cloud import worker
from romulus_cloud.adapters import coder_egress
from romulus_cloud.adapters.coder import LocalAgent, Sandbox, build_coder
from romulus_cloud.adapters.coder_egress import (
    EXPIRES_LABEL,
    KIND_LABEL,
    LABEL,
    OWNER_LABEL,
    ROLE_LABEL,
    RUN_LABEL,
    Egress,
)
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
    started = time.time()
    with open_run(egress, model="pinned-model", max_tokens=321, ttl_s=777) as net:
        run = net.agent_labels[RUN_LABEL]
        common = {
            "romulus.coder": "egress",
            RUN_LABEL: run,
            KIND_LABEL: "worker",
            OWNER_LABEL: coder_egress.process_owner(),
        }
        for labels, role in (
            (docker.networks[net.network], "net"),
            (docker.containers[net.proxy], "proxy"),
            (net.agent_labels, "agent"),
        ):
            assert labels.items() >= {**common, ROLE_LABEL: role}.items()
            assert started + 777 - 1 <= int(labels[EXPIRES_LABEL]) <= time.time() + 777
        assert coder_egress.process_owner().startswith(f"{socket.gethostname()}:{os.getpid()}:")
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


def _dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def test_owner_alive_tells_live_dead_and_reused_pids_apart():
    me = coder_egress.process_owner()
    host, pid, start = me.split(":", 2)
    assert coder_egress.owner_alive(me) is True
    assert coder_egress.owner_alive(f"{host}:{pid}:SomeOtherStart") is False  # pid reused
    assert coder_egress.owner_alive(f"{host}:{_dead_pid()}:{start}") is False
    assert coder_egress.owner_alive(f"other-host:{pid}:{start}") is None  # can't tell
    assert coder_egress.owner_alive("garbled") is None


def test_a_killed_worker_leaves_nothing_after_the_startup_prune(
    make_settings, restore_signals, monkeypatch
):
    """SIGKILL: no handler, no `finally`. The next worker's startup prune removes that run
    (the proxy still holding the key, the agent, the network) and spares everything else."""
    docker = fakes.FakeDocker()
    host = socket.gethostname()
    me = coder_egress.process_owner()

    def run_as(owner: str, kind: str = "worker", ttl_s: int = 900) -> None:
        monkeypatch.setattr(coder_egress, "process_owner", lambda: owner)
        egress = Egress(image="romulus-coder:test", runner=docker, kind=kind)
        with open_run(egress, ttl_s=ttl_s) as net:  # stays behind: SIGKILL skips teardown
            docker.containers[f"agent-{net.agent_labels[RUN_LABEL]}"] = dict(net.agent_labels)
            coder_egress._ACTIVE.clear()
            egress.runner = lambda argv, **kw: fakes.ProcResult(0, "", "")  # no teardown

    dead = f"{host}:{_dead_pid()}:x"
    run_as(dead)  # a killed worker's run: removed
    run_as(f"{host}:{os.getpid()}:NotMyStart")  # same pid, another process: removed
    run_as(me)  # a live worker's run (this process): spared
    run_as(dead, kind="test")  # an integration test's run: spared
    run_as("other-host:1:x")  # another host's live-or-not run before its deadline: spared
    run_as("other-host:1:x", ttl_s=-10)  # past its deadline (the proxy is gone): removed
    monkeypatch.undo()
    docker.containers["unrelated"] = {"other": "x"}
    before = docker.left_behind()
    assert len(before) == 6 * 3 + 1

    settings = make_settings().model_copy(update={"coder_provider": "local_agent"})
    worker.egress_startup(settings, runner=docker)
    owners = [docker.containers.get(n) or docker.networks.get(n) for n in docker.left_behind()]
    kept = sorted((o.get(OWNER_LABEL, "-"), o.get(KIND_LABEL, "-")) for o in owners)
    assert kept == sorted(
        [(me, "worker")] * 3 + [(dead, "test")] * 3 + [("other-host:1:x", "worker")] * 3
        + [("-", "-")]
    )  # fmt: skip
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


def test_prune_counts_what_it_removed_and_decides_by_deadline_then_owner():
    docker = fakes.FakeDocker()
    old = {"romulus.coder": "egress", KIND_LABEL: "worker", EXPIRES_LABEL: "100"}
    docker.containers["p"] = {**old, OWNER_LABEL: "h:1:x"}
    docker.networks["n"] = {**old, OWNER_LABEL: "h:1:x"}
    docker.networks["fresh"] = {**old, EXPIRES_LABEL: "300", OWNER_LABEL: "h:1:x"}
    docker.networks["no-deadline"] = {**old, EXPIRES_LABEL: "", OWNER_LABEL: "h:2:x"}
    docker.networks["legacy"] = {"romulus.coder": "egress"}  # no kind: never ours to prune
    alive = {"h:1:x": True, "h:2:x": False}.get
    assert coder_egress.prune(docker, now=200, alive=alive) == 3
    assert docker.left_behind() == ["fresh", "legacy"]
    assert LABEL == "romulus.coder=egress"


def _fake_worker_main(monkeypatch, tmp_path):
    """worker.main up to the pid-file lock, with no DB."""

    class _Session:
        def __enter__(self):
            return None

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(worker.db, "make_engine", lambda url: None)
    monkeypatch.setattr(worker.db, "create_all", lambda engine: None)
    monkeypatch.setattr(worker.db, "make_sessionmaker", lambda engine: _Session)
    monkeypatch.setattr(worker, "host_refusal", lambda *a: None)
    monkeypatch.setattr(worker, "egress_startup", lambda settings: (_ for _ in ()).throw(
        RuntimeError("started")
    ))  # fmt: skip


def test_a_second_host_worker_is_refused(monkeypatch, tmp_path):
    pidfile = tmp_path / "run" / "worker-host.pid"
    first, _ = worker.lock_pidfile(pidfile)
    assert first is not None and pidfile.read_text() == str(os.getpid())
    # Another process can't take it while the first holds it ...
    probe = (
        "import sys; from pathlib import Path; from romulus_cloud.worker import lock_pidfile; "
        "h, other = lock_pidfile(Path(sys.argv[1])); print('LOCKED' if h else 'REFUSED', other)"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe, str(pidfile)], capture_output=True, text=True
    )
    assert out.stdout.split() == ["REFUSED", str(os.getpid())], out.stderr
    # ... and main says so and stops before the egress prune.
    _fake_worker_main(monkeypatch, tmp_path)
    with pytest.raises(SystemExit) as exc:
        worker.main(["--host", "--pidfile", str(pidfile)])
    assert "another host worker is running" in str(exc.value) and str(os.getpid()) in str(exc.value)
    assert pidfile.read_text() == str(os.getpid())  # the refused launch changed nothing
    # The lock goes with the process (here: the file closing); then a new worker gets it.
    first.close()
    out = subprocess.run(
        [sys.executable, "-c", probe, str(pidfile)], capture_output=True, text=True
    )
    assert out.stdout.split()[0] == "LOCKED", out.stderr


def test_the_first_host_worker_takes_the_lock_then_prunes(monkeypatch, tmp_path):
    pidfile = tmp_path / "worker-host.pid"
    _fake_worker_main(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="started"):  # reached egress_startup, lock held
        worker.main(["--host", "--pidfile", str(pidfile)])
    assert worker.lock_pidfile(pidfile)[0] is None
    worker._PIDFILE_LOCK.close()
    worker._PIDFILE_LOCK = None
