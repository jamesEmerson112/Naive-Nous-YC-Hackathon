"""Opt-in (B5): the real agent sandbox reaches only the LLM, through the key-holding proxy.

Skipped unless CODER_INTEGRATION=1. Needs Docker Desktop (the fake LLM listens on the host and
the proxy reaches it at host.docker.internal). Spends nothing: the "LLM" is a fake on the host
that answers mini-swe-agent's first call with a probe script, so the probes run as the agent's
own command inside the real sandbox, and the next call carries their output back to the fake.

    CODER_INTEGRATION=1 uv run pytest cloud/tests/integration/test_coder_egress.py -s

CODER_INTEGRATION_IMAGE picks the tag the coder image is built as (default romulus-coder:b5it).
It also checks the lifecycle with real containers: a SIGTERM to a process mid-run leaves
nothing, the startup prune spares a live owner's run and removes it once its owner is
SIGKILLed, and the proxy exits at its deadline. Runs these tests open themselves are
`kind=test` (a worker's prune never touches them) unless the test is about the prune.

The optional real call (`test_one_tiny_real_call_through_the_proxy`) also needs
CODER_REAL_LLM=1 and LLM_BASE_URL / LLM_MODEL / LLM_API_KEY in the environment (read at import,
since conftest unsets settings variables before each test); it asks for at most 10 tokens.
"""

import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from romulus_cloud.adapters.coder import LocalAgent, Sandbox, subprocess_runner
from romulus_cloud.adapters.coder_egress import LABEL, RUN_LABEL, Egress, prune, remove_labelled

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
import coder_fakes as fakes  # noqa: E402

LLM = {k: os.environ.get(k, "") for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")}
REAL_LLM = os.environ.get("CODER_REAL_LLM") == "1"
IMAGE = os.environ.get("CODER_INTEGRATION_IMAGE") or "romulus-coder:b5it"
CODER_DIR = Path(__file__).resolve().parents[2] / "coder"
FAKE_KEY = "sk-b5-integration-REAL-KEY-0123456789"

pytestmark = pytest.mark.skipif(
    os.environ.get("CODER_INTEGRATION") != "1", reason="set CODER_INTEGRATION=1 (needs Docker)"
)

PROBE = r"""
import http.client, os, socket, urllib.request
def probe(name, fn):
    try:
        fn()
        print(f"PROBE {name}: OPEN")
    except Exception as exc:
        print(f"PROBE {name}: BLOCKED {type(exc).__name__}")
probe("internet", lambda: urllib.request.urlopen("https://example.com", timeout=5))
probe("raw_ip", lambda: socket.create_connection(("1.1.1.1", 443), timeout=5))
probe("host", lambda: socket.create_connection(("host.docker.internal", HOST_PORT), timeout=5))
probe("lan", lambda: socket.create_connection(("192.168.1.1", 80), timeout=3))
probe("host_ip", lambda: socket.create_connection(("192.168.65.254", HOST_PORT), timeout=3))
# The Docker host's side of the bridge (x.y.0.1): a refusal would mean it is reachable.
me = socket.gethostbyname(socket.gethostname()).split(".")
probe("docker_host", lambda: socket.create_connection((f"{me[0]}.{me[1]}.0.1", 22), timeout=3))
# DNS: only llm-proxy resolves; nothing is forwarded (a lookup could carry data out).
probe("dns", lambda: socket.getaddrinfo("example.com", 443))
probe("dns_exfil", lambda: socket.getaddrinfo("c2VjcmV0.example.com", 443))
probe("dns_host", lambda: socket.getaddrinfo("host.docker.internal", 443))
# IPv6: global and unique-local addresses are unreachable too.
probe("ipv6", lambda: socket.create_connection(("2606:4700:4700::1111", 443), timeout=3))
probe("ipv6_ula", lambda: socket.create_connection(("fd00::1", 80), timeout=3))
def status(method, path, auth="Bearer wrong-token"):
    c = http.client.HTTPConnection("llm-proxy", 8080, timeout=5)
    body = b"{}" if method == "POST" else None
    c.request(method, path, body=body, headers={"Authorization": auth})
    return c.getresponse().status
for method, path in [("GET", "/v1/models"), ("POST", "/v1/embeddings"),
                     ("POST", "http://example.com/v1/chat/completions"),
                     ("CONNECT", "example.com:443"), ("POST", "/v1/chat/completions")]:
    print(f"PROXY {method} {path}: {status(method, path)}")
print("ENV", " ".join(f"{k}={v}" for k, v in sorted(os.environ.items())))
try:
    print("PID1", open("/proc/1/environ", "rb").read()[:80])
except OSError as exc:
    print("PID1 denied", type(exc).__name__)
"""


class FakeLlm:
    """An OpenAI-compatible endpoint on the host: probe first, then submit."""

    def __init__(self) -> None:
        self.seen: list[dict] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                fake.seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
                port = fake.server.server_address[1]
                cmd = (
                    f"python - <<'EOF'\n{PROBE.replace('HOST_PORT', str(port))}\nEOF"
                    if len(fake.seen) == 1
                    else "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
                )
                content = f"THOUGHT: probing.\n\n```mswea_bash_command\n{cmd}\n```"
                reply = json.dumps(
                    {
                        "id": f"fake-{len(fake.seen)}",
                        "object": "chat.completion",
                        "created": 0,
                        "model": "fake-model",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": content},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://host.docker.internal:{self.server.server_address[1]}/v1"


@pytest.fixture(scope="module")
def sandbox() -> Sandbox:
    toolkit = CODER_DIR.parents[1] / "firmware" / "toolkit"
    subprocess.run(
        ["docker", "build", "-q", "--build-context", f"fwtoolkit={toolkit}", "-t", IMAGE]
        + [str(CODER_DIR)],
        check=True,
    )
    return Sandbox(image=IMAGE, runner=subprocess_runner)


def _labelled(label: str = LABEL) -> set[str]:
    """Every container (running or not) and network carrying `label`."""
    found = set()
    for argv in (["ps", "-aq"], ["network", "ls", "-q"]):
        out = subprocess.run(
            ["docker", *argv, "--filter", f"label={label}"],
            capture_output=True,
            text=True,
            check=True,
        )
        found |= set(out.stdout.split())
    return found


def test_agent_reaches_only_the_llm_through_the_proxy(sandbox):
    llm = FakeLlm()
    before = _labelled()
    agent = LocalAgent(
        base_url=llm.url,
        model="fake-model",
        api_key=FAKE_KEY,
        sandbox=sandbox,
        step_limit=3,
        time_limit_s=120,
        test_timeout_s=60,
    )
    draft = agent.draft(fakes.TOY_PATCH, {"id": "inc-b5"}, "toy-c0de", fakes.toy_files())
    print("\n--- notes\n" + draft.notes)
    assert len(llm.seen) >= 2, draft.notes  # the probe ran and its output came back

    # (d) every call reached the fake upstream with the real key put in by the proxy
    for req in llm.seen:
        assert req["path"] == "/v1/chat/completions"
        assert req["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
        body = json.loads(req["body"])  # rebuilt by the proxy from its allowlist
        assert body["model"] == "fake-model" and body["max_tokens"] <= 2000
        assert set(body) <= {"model", "messages", "max_tokens", "n", "temperature", "top_p"}
    observation = llm.seen[1]["body"].decode()
    out = json.loads(observation)["messages"][-1]["content"]
    print("--- probe output\n" + out)

    # (a) no internet, no raw IPs, no host (by name or IP), no LAN, no Docker host
    for name in ("internet", "raw_ip", "host", "lan", "host_ip", "docker_host", "ipv6", "ipv6_ula"):
        assert f"PROBE {name}: BLOCKED" in out, name
    # (a2) no DNS forwarding
    for name in ("dns", "dns_exfil", "dns_host"):
        assert f"PROBE {name}: BLOCKED gaierror" in out, name
    assert "ConnectionRefusedError" not in out  # nothing even answered with a refusal
    # (b) the proxy refuses everything but chat completions with the job token
    assert "PROXY GET /v1/models: 405" in out
    assert "PROXY POST /v1/embeddings: 403" in out
    assert "PROXY POST http://example.com/v1/chat/completions: 403" in out
    assert "PROXY CONNECT example.com:443: 405" in out
    assert "PROXY POST /v1/chat/completions: 401" in out
    # (c) the real key is nowhere in the agent's container
    assert "ENV " in out and FAKE_KEY not in out and "PID1 denied" in out
    assert FAKE_KEY not in draft.notes + draft.test_output + draft.diff
    assert "llm proxy: " in draft.notes and "5 refused" in draft.notes
    assert any(f.startswith("egress_refused") for f in draft.flags)
    assert _labelled() == before  # network, proxy and agent torn down


@pytest.mark.skipif(not (REAL_LLM and LLM["LLM_BASE_URL"]), reason="CODER_REAL_LLM=1 + LLM_*")
def test_one_tiny_real_call_through_the_proxy(sandbox):
    """End to end against the real endpoint (e.g. the Naïve proxy), max_tokens 10."""
    egress = Egress(image=IMAGE, runner=subprocess_runner, kind="test")
    script = (
        "import http.client, json, os\n"
        "c = http.client.HTTPConnection('llm-proxy', 8080, timeout=120)\n"
        "body = json.dumps({'model': os.environ['LLM_MODEL'], 'max_tokens': 10,\n"
        "    'messages': [{'role': 'user', 'content': 'Reply with the word ok.'}]})\n"
        "c.request('POST', '/v1/chat/completions', body=body, headers={\n"
        "    'Authorization': 'Bearer ' + os.environ['LLM_API_KEY'],\n"
        "    'Content-Type': 'application/json'})\n"
        "r = c.getresponse(); data = r.read()\n"
        "print('STATUS', r.status)\n"
        "msg = json.loads(data)['choices'][0]['message'] if r.status == 200 else {}\n"
        "print('REPLY', repr(msg.get('content'))[:40]"
        " if r.status == 200 else data[:200])\n"
    )
    with egress.open(
        LLM["LLM_BASE_URL"],
        LLM["LLM_API_KEY"],
        model=LLM["LLM_MODEL"],
        max_requests=2,
        ttl_s=300,
        base_env=os.environ,
    ) as net:
        env = {**os.environ, **net.agent_env, "LLM_MODEL": LLM["LLM_MODEL"]}
        proc = subprocess.run(
            ["docker", "run", "--rm", "--network", net.network, "-e", "LLM_API_KEY"]
            + ["-e", "LLM_MODEL", "--entrypoint", "/usr/local/bin/python", IMAGE, "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        summary = egress.summary(net)
    out = proc.stdout + proc.stderr
    if LLM["LLM_API_KEY"]:
        assert LLM["LLM_API_KEY"] not in out
    print(out, summary.note())
    if "capacity" in out or "STATUS 429" in out or "STATUS 503" in out:
        pytest.skip("the LLM endpoint is at capacity")
    assert "STATUS 200" in out and summary.forwarded == 1


HOLDER = """
import os, sys, time
from romulus_cloud.adapters.coder import subprocess_runner
from romulus_cloud.adapters import coder_egress
coder_egress.install_signal_teardown()
egress = coder_egress.Egress(image=sys.argv[1], runner=subprocess_runner, kind=sys.argv[2])
with egress.open("http://example.invalid/v1", "sk-held-REAL-KEY", model="m",
                 max_requests=1, ttl_s=120, base_env=os.environ) as net:
    print("RUN", net.agent_labels["romulus.coder.run"], net.proxy, flush=True)
    time.sleep(120)
"""


def _hold_a_run(kind: str = "test") -> tuple[subprocess.Popen, str, str]:
    """A separate process with one egress run open (the proxy holds a key), sleeping."""
    proc = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(HOLDER), IMAGE, kind],
        stdout=subprocess.PIPE,
        text=True,
    )
    line = proc.stdout.readline().split()
    assert line[:1] == ["RUN"], line
    return proc, line[1], line[2]


def test_sigterm_mid_run_leaves_nothing(sandbox):
    proc, run, proxy = _hold_a_run()
    try:
        assert len(_labelled(f"{RUN_LABEL}={run}")) == 2  # the proxy and the network
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=60) == 128 + signal.SIGTERM
        assert _labelled(f"{RUN_LABEL}={run}") == set()
    finally:
        proc.kill()
        remove_labelled(subprocess_runner, label=f"{RUN_LABEL}={run}")


def test_the_startup_prune_spares_a_live_worker_and_cleans_up_after_a_sigkill(sandbox):
    """The real `prune` (as at worker startup): a live worker's run stays; once that worker is
    SIGKILLed (no handler, no `finally`), its proxy, still holding the key, and its network go.
    It only ever removes `kind=worker` runs of dead owners on this host, or past deadline."""
    proc, run, proxy = _hold_a_run(kind="worker")
    try:
        assert len(_labelled(f"{RUN_LABEL}={run}")) == 2
        prune(subprocess_runner)
        assert len(_labelled(f"{RUN_LABEL}={run}")) == 2  # its owner is alive: spared
        proc.kill()
        proc.wait(timeout=30)
        assert len(_labelled(f"{RUN_LABEL}={run}")) == 2  # the orphan
        assert prune(subprocess_runner) >= 2
        assert _labelled(f"{RUN_LABEL}={run}") == set()
    finally:
        proc.kill()
        remove_labelled(subprocess_runner, label=f"{RUN_LABEL}={run}")


def test_the_proxy_exits_at_its_deadline(sandbox):
    egress = Egress(image=IMAGE, runner=subprocess_runner, kind="test")
    with egress.open(
        "http://example.invalid/v1",
        "sk-ttl",
        model="m",
        max_requests=1,
        ttl_s=3,
        base_env=os.environ,
    ) as net:
        running = ["docker", "ps", "-q", "--filter", f"name={net.proxy}"]
        assert subprocess.run(running, capture_output=True, text=True).stdout.strip()
        deadline = time.monotonic() + 30
        while subprocess.run(running, capture_output=True, text=True).stdout.strip():
            assert time.monotonic() < deadline, "the proxy outlived its deadline"
            time.sleep(0.5)
    assert _labelled(f"{RUN_LABEL}={net.agent_labels[RUN_LABEL]}") == set()
