"""The agent run's network (B5): an internal Docker network plus a key-holding LLM proxy.

For each agent run the worker:
1. creates a Docker network with `--internal` (no default route: no internet, bench LAN or
   host; `host.docker.internal` doesn't even resolve) and no gateway IP on the bridge
   (`inhibit_ipv4`, so the Docker host's own services aren't reachable either), labelled
   `romulus.coder=egress`;
2. creates the proxy (`cloud/coder/llm_proxy.py`, from the same coder image) on that network
   as `llm-proxy`, connects it to the normal `bridge` network too (its only way out), and
   starts it with the real LLM key, passed to `docker` by name only;
3. hands the agent `LLM_BASE_URL=http://llm-proxy:8080/v1` and a random per-job token as its
   key, so the agent's container never holds the real key;
4. removes the proxy and the network afterwards, whatever happened.

The proxy forwards only `POST /v1/chat/completions` to the configured LLM endpoint, caps body
size and requests per job, and logs one line per request; `summary` counts those lines.
Leftovers after a crash: `docker network prune --filter label=romulus.coder=egress`.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Iterator, Mapping
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
# Without it the bridge still holds the network's gateway IP, so the agent could reach
# services listening on the Docker host (on Docker Desktop: its Linux VM) at x.y.0.1.
NO_GATEWAY = "com.docker.network.bridge.inhibit_ipv4=true"
MAX_BODY = 4 * 1024 * 1024
# Must match cloud/coder/llm_proxy.py's log prefixes.
READY, FORWARDED, REFUSED = "llm_proxy ready", "llm_proxy forwarded", "llm_proxy refused"


class EgressError(Exception):
    """Docker couldn't set up the restricted network or the proxy."""


@dataclass(frozen=True)
class EgressSession:
    network: str  # the internal network the agent joins (and nothing else)
    agent_env: dict[str, str]  # LLM_BASE_URL and LLM_API_KEY (the job token) for the agent
    proxy: str  # the proxy container's name


@dataclass(frozen=True)
class EgressSummary:
    forwarded: int
    refused: int
    refusals: tuple[str, ...]  # the proxy's refusal lines (no bodies, no keys)
    ready: bool

    def note(self) -> str:
        return f"llm proxy: {self.forwarded} forwarded, {self.refused} refused"


def summarize(logs: str) -> EgressSummary:
    lines = logs.splitlines()
    refusals = tuple(line for line in lines if line.startswith(REFUSED))
    return EgressSummary(
        forwarded=sum(line.startswith(FORWARDED) for line in lines),
        refused=len(refusals),
        refusals=refusals,
        ready=any(line.startswith(READY) for line in lines),
    )


@dataclass
class Egress:
    """Sets up and tears down one agent run's network and proxy (see module doc)."""

    image: str
    runner: Runner
    docker: str = "docker"
    outside: str = "bridge"  # the proxy's way out; the agent never joins it

    def _docker(self, *args: str, env: Mapping[str, str] | None = None, check: bool = True):
        proc = self.runner([self.docker, *args], env=env, timeout=60)
        if check and proc.returncode != 0:
            what = " ".join(args[:2])
            raise EgressError(f"docker {what} failed: {proc.stderr.strip()[:300]}")
        return proc

    @contextmanager
    def open(
        self, upstream: str, api_key: str, *, max_requests: int, base_env: Mapping[str, str]
    ) -> Iterator[EgressSession]:
        """`base_env` is the environment the docker CLI runs with (the key is added to it
        for the proxy's `docker create` only)."""
        tag = uuid.uuid4().hex[:12]
        network, proxy = f"romulus-coder-net-{tag}", f"romulus-llm-proxy-{tag}"
        token = f"romulus-job-{secrets.token_hex(16)}"
        proxy_env = {
            "LLM_PROXY_UPSTREAM": upstream,
            "LLM_PROXY_KEY": api_key,
            "LLM_PROXY_TOKEN": token,
            "LLM_PROXY_MAX_REQUESTS": str(max_requests),
            "LLM_PROXY_MAX_BODY": str(MAX_BODY),
            "LLM_PROXY_PORT": str(PROXY_PORT),
        }
        self._docker("network", "create", "--internal", "-o", NO_GATEWAY, "--label", LABEL, network)
        try:
            argv = [
                "create", "--rm", "--name", proxy,
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
                proxy=proxy,
            )
        finally:
            self._docker("rm", "-f", proxy, check=False)
            self._docker("network", "rm", network, check=False)

    def summary(self, session: EgressSession) -> EgressSummary:
        proc = self._docker("logs", session.proxy, check=False)
        return summarize(proc.stdout + proc.stderr)
