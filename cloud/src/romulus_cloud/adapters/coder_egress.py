"""The agent run's network (B5): an internal Docker network plus a key-holding LLM proxy.

For each agent run the worker:
1. creates a Docker network with `--internal` (no default route: no internet, bench LAN or
   host; `host.docker.internal` doesn't even resolve) and no gateway IP on the bridge
   (`inhibit_ipv4`, so the Docker host's own services aren't reachable either);
2. creates the proxy (`cloud/coder/llm_proxy.py`, from the same coder image) on that network
   as `llm-proxy`, connects it to the normal `bridge` network too (its only way out), and
   starts it with the real LLM key, passed to `docker` by name only. The proxy exits on its
   own at `ttl_s`, so even an orphaned one stops holding the key;
3. hands the agent `LLM_BASE_URL=http://llm-proxy:8080/v1` and a random per-job token as its
   key, so the agent's container never holds the real key;
4. removes every container of the run (agent and proxy) and then the network afterwards,
   whatever happened.

Everything is labelled `romulus.coder=egress`, `romulus.coder.run=<id>`,
`romulus.coder.role=net|proxy|agent`, `romulus.coder.kind=worker|test`,
`romulus.coder.owner=<host>:<pid>:<process start>` and `romulus.coder.expires=<unix time of
the proxy's deadline>`. A killed worker can still leave a run behind, so
`install_signal_teardown` (SIGTERM/SIGINT tear down this process's open runs first) and
`prune` run at worker startup (after the host worker's pid-file lock). `prune` removes only
`kind=worker` runs that are past their deadline, or whose owner is on this host and no longer
alive: never a live worker's run, and never a test run (`kind=test`).
`python -m romulus_cloud.adapters.coder_egress --prune` does the same by hand.

The proxy forwards only `POST /v1/chat/completions` to the configured LLM endpoint, rebuilds
the body from an allowlist with the model forced, caps body size, tokens, requests and
connections, and logs one line per request; `summary` counts those lines.
"""

from __future__ import annotations

import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from romulus_cloud.adapters.coder import Runner

PROXY_ALIAS = "llm-proxy"
PROXY_PORT = 8080
PROXY_SCRIPT = "/opt/romulus/llm_proxy.py"
PROXY_PYTHON = "/usr/local/bin/python"
LABEL = "romulus.coder=egress"
RUN_LABEL = "romulus.coder.run"
ROLE_LABEL = "romulus.coder.role"
KIND_LABEL = "romulus.coder.kind"  # worker (pruned when orphaned) or test (never pruned)
OWNER_LABEL = "romulus.coder.owner"  # <host>:<pid>:<process start>
EXPIRES_LABEL = "romulus.coder.expires"  # unix time the proxy exits on its own
# Without it the bridge still holds the network's gateway IP, so the agent could reach
# services listening on the Docker host (on Docker Desktop: its Linux VM) at x.y.0.1.
NO_GATEWAY = "com.docker.network.bridge.inhibit_ipv4=true"
MAX_BODY = 4 * 1024 * 1024
DEFAULT_MAX_TOKENS = 2000
# Must match cloud/coder/llm_proxy.py's log prefixes.
READY, FORWARDED = "llm_proxy ready", "llm_proxy forwarded"
REFUSED, UPSTREAM_ERROR = "llm_proxy refused", "llm_proxy upstream_error"


class EgressError(Exception):
    """Docker couldn't set up the restricted network or the proxy."""


@dataclass(frozen=True)
class EgressSession:
    network: str  # the internal network the agent joins (and nothing else)
    agent_env: dict[str, str]  # LLM_BASE_URL and LLM_API_KEY (the job token) for the agent
    agent_labels: dict[str, str]  # so teardown and prune find the agent's container too
    proxy: str  # the proxy container's name


@dataclass(frozen=True)
class EgressSummary:
    forwarded: int
    refused: int  # the agent's requests the proxy refused (a probe, or a broken client)
    upstream_errors: int  # the LLM endpoint failed (not the agent's doing)
    refusals: tuple[str, ...]  # the proxy's refusal lines (no bodies, no keys)
    ready: bool

    def note(self) -> str:
        note = f"llm proxy: {self.forwarded} forwarded, {self.refused} refused"
        return note + (f", {self.upstream_errors} upstream errors" if self.upstream_errors else "")


def summarize(logs: str) -> EgressSummary:
    lines = logs.splitlines()
    refusals = tuple(line for line in lines if line.startswith(REFUSED))
    return EgressSummary(
        forwarded=sum(line.startswith(FORWARDED) for line in lines),
        refused=len(refusals),
        upstream_errors=sum(line.startswith(UPSTREAM_ERROR) for line in lines),
        refusals=refusals,
        ready=any(line.startswith(READY) for line in lines),
    )


def _process_start(pid: int) -> str | None:
    """The process's start time as `ps` prints it (Linux and macOS), letters and digits only;
    None if there is no such process. With the pid it tells a live owner from a reused pid."""
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    start = "".join(ch for ch in out.stdout if ch.isalnum())
    return start if out.returncode == 0 and start else None


_OWNER: str | None = None


def process_owner() -> str:
    """This process's owner label, `<host>:<pid>:<process start>` (computed once per pid)."""
    global _OWNER
    pid = os.getpid()
    if _OWNER is None or _OWNER.split(":")[1] != str(pid):
        _OWNER = f"{socket.gethostname()}:{pid}:{_process_start(pid) or 'unknown'}"
    return _OWNER


def owner_alive(owner: str, host: str | None = None) -> bool | None:
    """True if `owner`'s process still runs (same pid and start time), False if it is gone,
    None if that can't be told from here (another host, or an unreadable label)."""
    parts = owner.split(":", 2)
    if len(parts) != 3 or not parts[1].isdigit():
        return None
    if parts[0] != (host or socket.gethostname()):
        return None
    start = _process_start(int(parts[1]))
    return start is not None and parts[2] in ("unknown", start)


def _labels(run: str, role: str, kind: str, owner: str, expires: int) -> dict[str, str]:
    key, value = LABEL.split("=", 1)
    return {
        key: value,
        RUN_LABEL: run,
        ROLE_LABEL: role,
        KIND_LABEL: kind,
        OWNER_LABEL: owner,
        EXPIRES_LABEL: str(expires),
    }


def _label_args(labels: Mapping[str, str]) -> list[str]:
    return [arg for k, v in labels.items() for arg in ("--label", f"{k}={v}")]


def remove_labelled(runner: Runner, docker: str = "docker", label: str = LABEL) -> int:
    """`docker rm -f` every container with `label`, then remove every such network.
    Returns how many things it removed. Never raises on Docker errors."""
    removed = 0
    ps = runner([docker, "ps", "-aq", "--filter", f"label={label}"], timeout=60)
    ids = ps.stdout.split() if ps.returncode == 0 else []
    if ids:
        if runner([docker, "rm", "-f", *ids], timeout=60).returncode == 0:
            removed += len(ids)
    nets = runner([docker, "network", "ls", "-q", "--filter", f"label={label}"], timeout=60)
    for net in nets.stdout.split() if nets.returncode == 0 else []:
        if runner([docker, "network", "rm", net], timeout=60).returncode == 0:
            removed += 1
    return removed


# This process's open runs, so a SIGTERM/SIGINT can tear them down before exiting. Reentrant:
# the signal handler runs on the main thread, which may be holding it at that moment.
_ACTIVE: dict[str, Egress] = {}
_ACTIVE_LOCK = threading.RLock()


def teardown_active() -> int:
    """Remove every run this process has open (containers, then networks)."""
    with _ACTIVE_LOCK:
        active = list(_ACTIVE.items())
    return sum(eg.remove_run(run) for run, eg in active)


def install_signal_teardown() -> None:
    """On SIGTERM or SIGINT: tear down this process's open runs, then exit (main thread)."""

    def handler(signum, frame) -> None:
        teardown_active()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def orphaned(labels: Mapping[str, str], now: float, alive: Callable[[str], bool | None]) -> bool:
    """A worker run nobody will tear down: past its proxy deadline, or its owner on this host
    is gone. Test runs, and live (or unknowable) owners' runs before the deadline, are spared."""
    if labels.get(KIND_LABEL) != "worker":
        return False
    try:
        if float(labels.get(EXPIRES_LABEL, "")) < now:
            return True
    except ValueError:
        pass  # no readable deadline: the owner alone decides
    return alive(labels.get(OWNER_LABEL, "")) is False


def _listed(runner: Runner, argv: list[str]) -> list[tuple[str, dict[str, str]]]:
    """(id, labels) per line of `docker ps -a` / `docker network ls` with `{{.ID}} {{.Labels}}`."""
    proc = runner(argv, timeout=60)
    found = []
    for line in proc.stdout.splitlines() if proc.returncode == 0 else []:
        ident, _, raw = line.strip().partition(" ")
        if ident:
            found.append((ident, dict(p.split("=", 1) for p in raw.split(",") if "=" in p)))
    return found


def prune(
    runner: Runner | None = None,
    docker: str = "docker",
    *,
    now: float | None = None,
    alive: Callable[[str], bool | None] = owner_alive,
) -> int:
    """Remove orphaned worker runs (`orphaned`), containers before networks. Returns how
    many things it removed; 0 without Docker."""
    if runner is None:
        from romulus_cloud.adapters.coder import subprocess_runner

        runner = subprocess_runner
    now = time.time() if now is None else now
    fmt = ["--filter", f"label={LABEL}", "--format", "{{.ID}} {{.Labels}}"]
    removed = 0
    try:
        for kind in ("ps", "network"):
            listing = [docker, "ps", "-a"] if kind == "ps" else [docker, "network", "ls"]
            remove = [docker, "rm", "-f"] if kind == "ps" else [docker, "network", "rm"]
            for ident, labels in _listed(runner, [*listing, *fmt]):
                if orphaned(labels, now, alive):
                    removed += runner([*remove, ident], timeout=60).returncode == 0
    except OSError:  # no docker CLI: nothing of ours can be running either
        return 0
    return removed


@dataclass
class Egress:
    """Sets up and tears down one agent run's network and proxy (see module doc)."""

    image: str
    runner: Runner
    docker: str = "docker"
    outside: str = "bridge"  # the proxy's way out; the agent never joins it
    kind: str = "worker"  # "test": a startup prune never touches the run

    def _docker(self, *args: str, env: Mapping[str, str] | None = None, check: bool = True):
        proc = self.runner([self.docker, *args], env=env, timeout=60)
        if check and proc.returncode != 0:
            what = " ".join(args[:2])
            raise EgressError(f"docker {what} failed: {proc.stderr.strip()[:300]}")
        return proc

    def remove_run(self, run: str) -> int:
        return remove_labelled(self.runner, self.docker, f"{RUN_LABEL}={run}")

    @contextmanager
    def open(
        self,
        upstream: str,
        api_key: str,
        *,
        model: str,
        max_requests: int,
        ttl_s: int,
        base_env: Mapping[str, str],
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Iterator[EgressSession]:
        """`base_env` is the environment the docker CLI runs with (the key is added to it
        for the proxy's `docker create` only). `ttl_s`: the proxy exits on its own then."""
        run = uuid.uuid4().hex[:12]
        network, proxy = f"romulus-coder-net-{run}", f"romulus-llm-proxy-{run}"
        token = f"romulus-job-{secrets.token_hex(16)}"
        proxy_env = {
            "LLM_PROXY_UPSTREAM": upstream,
            "LLM_PROXY_KEY": api_key,
            "LLM_PROXY_TOKEN": token,
            "LLM_PROXY_MODEL": model,
            "LLM_PROXY_MAX_TOKENS": str(max_tokens),
            "LLM_PROXY_MAX_REQUESTS": str(max_requests),
            "LLM_PROXY_MAX_BODY": str(MAX_BODY),
            "LLM_PROXY_TTL_S": str(ttl_s),
            "LLM_PROXY_PORT": str(PROXY_PORT),
        }
        owner, expires = process_owner(), int(time.time()) + ttl_s
        labels = {r: _labels(run, r, self.kind, owner, expires) for r in ("net", "proxy", "agent")}
        with _ACTIVE_LOCK:
            _ACTIVE[run] = self
        try:
            self._docker(
                "network", "create", "--internal", "-o", NO_GATEWAY,
                *_label_args(labels["net"]), network,
            )  # fmt: skip
            argv = [
                "create", "--rm", "--name", proxy, *_label_args(labels["proxy"]),
                "--network", network, "--network-alias", PROXY_ALIAS,
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only",
                "--user", "65534:65534", "--memory", "256m", "--pids-limit", "64",
            ]  # fmt: skip
            for key in sorted(proxy_env):  # names only: the key's value never enters argv
                argv += ["-e", key]
            argv += ["--entrypoint", PROXY_PYTHON, self.image, PROXY_SCRIPT]
            self._docker(*argv, env={**base_env, **proxy_env})
            self._docker("network", "connect", self.outside, proxy)
            self._docker("start", proxy)
            yield EgressSession(
                network=network,
                agent_env={
                    "LLM_BASE_URL": f"http://{PROXY_ALIAS}:{PROXY_PORT}/v1",
                    "LLM_API_KEY": token,
                },
                agent_labels=labels["agent"],
                proxy=proxy,
            )
        finally:
            self.remove_run(run)  # the agent too, if it outlived its CLI
            with _ACTIVE_LOCK:
                _ACTIVE.pop(run, None)

    def summary(self, session: EgressSession) -> EgressSummary:
        proc = self._docker("logs", session.proxy, check=False)
        return summarize(proc.stdout + proc.stderr)


if __name__ == "__main__":
    if sys.argv[1:] == ["--prune"]:
        print(f"coder egress: removed {prune()} leftover container(s)/network(s)")
    else:
        sys.exit("usage: python -m romulus_cloud.adapters.coder_egress --prune")
