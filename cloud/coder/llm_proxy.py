"""The coding agent's only way out (B5): a key-holding proxy to one LLM endpoint.

The worker starts it as a sidecar container next to each agent run
(`romulus_cloud.adapters.coder_egress`). The agent's container sits on an internal Docker
network with no route anywhere; this proxy is on that network and on a normal one. The
agent gets `LLM_BASE_URL=http://llm-proxy:8080/v1` and a per-job token as its "API key";
the real key lives only here.

What it does, and nothing else:
- `POST /v1/chat/completions` with `Authorization: Bearer <job token>` is forwarded to
  `<LLM_PROXY_UPSTREAM>/chat/completions`, with the real key put in `Authorization` (the
  agent's header is dropped) and only `Content-Type` and `Accept` passed through;
- every other path, method (`CONNECT` included), absolute-URI request line, query string,
  wrong token, chunked or oversized body, or request past the per-job cap is refused;
- the upstream host comes from config, never from the request; redirects are not followed;
- one log line per request on stderr (method, path, status, sizes, time): no bodies, no keys.

litellm's OpenAI-compatible route (mini-swe-agent, `openai/<model>`) only ever calls
`POST {api_base}/chat/completions`, so that is the whole allowlist.

Env: LLM_PROXY_UPSTREAM (the real LLM_BASE_URL, e.g. https://host/v1), LLM_PROXY_KEY,
LLM_PROXY_TOKEN, LLM_PROXY_MAX_REQUESTS (default 100), LLM_PROXY_MAX_BODY (bytes, default
4 MiB), LLM_PROXY_PORT (default 8080), LLM_PROXY_TIMEOUT_S (default 180).
"""

import hmac
import http.client
import json
import os
import ssl
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ALLOWED_PATH = "/v1/chat/completions"
READY = "llm_proxy ready"
REFUSED = "llm_proxy refused"
FORWARDED = "llm_proxy forwarded"
PASS_HEADERS = ("Content-Type", "Accept")


@dataclass
class Config:
    upstream: str  # e.g. https://llm.example/v1
    key: str
    token: str
    max_requests: int = 100
    max_body: int = 4 * 1024 * 1024
    timeout_s: float = 180.0
    count: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        parts = urlsplit(self.upstream)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("LLM_PROXY_UPSTREAM must be an http(s) URL")
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError("LLM_PROXY_UPSTREAM must be a plain base URL")
        if not self.token:
            raise ValueError("LLM_PROXY_TOKEN is required")
        self.scheme, self.host, self.port = parts.scheme, parts.hostname, parts.port
        self.path = parts.path.rstrip("/") + "/chat/completions"

    def take(self) -> int | None:
        """Count one request against the per-job cap; None once it is used up."""
        with self.lock:
            if self.count >= self.max_requests:
                return None
            self.count += 1
            return self.count


def config_from_env(env: dict[str, str]) -> Config:
    return Config(
        upstream=env.get("LLM_PROXY_UPSTREAM", ""),
        key=env.get("LLM_PROXY_KEY", ""),
        token=env.get("LLM_PROXY_TOKEN", ""),
        max_requests=int(env.get("LLM_PROXY_MAX_REQUESTS") or 100),
        max_body=int(env.get("LLM_PROXY_MAX_BODY") or 4 * 1024 * 1024),
        timeout_s=float(env.get("LLM_PROXY_TIMEOUT_S") or 180),
    )


def log(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "romulus-llm-proxy"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    config: Config  # set by make_server

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        pass  # our own one line per request instead

    def _refuse(self, status: int, why: str) -> None:
        log(f"{REFUSED} {self.command} {self._shown_path()} -> {status}: {why}")
        body = json.dumps({"error": {"message": f"llm proxy: {why}", "type": "refused"}})
        self._send(status, body.encode(), "application/json")
        self.close_connection = True

    def _shown_path(self) -> str:
        return (self.path or "")[:200].replace("\n", " ").replace("\r", " ")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        cfg = self.config
        if self.path != ALLOWED_PATH:  # absolute URIs and query strings never match
            return self._refuse(403, "only POST /v1/chat/completions is allowed")
        token = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(token.encode(), cfg.token.encode()):
            return self._refuse(401, "wrong job token")
        if self.headers.get("Transfer-Encoding"):
            return self._refuse(411, "chunked bodies are not accepted")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._refuse(411, "Content-Length required")
        if length < 0 or length > cfg.max_body:
            return self._refuse(413, f"body over {cfg.max_body} bytes")
        n = cfg.take()
        if n is None:
            return self._refuse(429, f"per-job cap of {cfg.max_requests} requests reached")
        body = self.rfile.read(length)
        started = time.monotonic()
        try:
            status, content_type, reply = self._forward(body)
        except (OSError, http.client.HTTPException) as exc:
            log(f"{REFUSED} {self.command} {self.path} -> 502: upstream {type(exc).__name__}")
            msg = {"error": {"message": f"llm proxy: upstream {type(exc).__name__}"}}
            return self._send(502, json.dumps(msg).encode(), "application/json")
        if cfg.key:  # an upstream error that echoes the key never reaches the agent
            reply = reply.replace(cfg.key.encode(), b"[redacted]")
        log(
            f"{FORWARDED} POST {self.path} -> {status} in={length}B out={len(reply)}B "
            f"{time.monotonic() - started:.1f}s req {n}/{cfg.max_requests}"
        )
        self._send(status, reply, content_type)

    def _forward(self, body: bytes) -> tuple[int, str, bytes]:
        cfg = self.config
        if cfg.scheme == "https":
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                cfg.host, cfg.port, timeout=cfg.timeout_s, context=ssl.create_default_context()
            )
        else:
            conn = http.client.HTTPConnection(cfg.host, cfg.port, timeout=cfg.timeout_s)
        headers = {h: self.headers[h] for h in PASS_HEADERS if self.headers.get(h)}
        headers["User-Agent"] = "romulus-llm-proxy"
        if cfg.key:
            headers["Authorization"] = f"Bearer {cfg.key}"
        try:
            conn.request("POST", cfg.path, body=body, headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.getheader("Content-Type", "application/json"), resp.read()
        finally:
            conn.close()

    def _not_allowed(self) -> None:
        self._refuse(405, f"method {self.command} not allowed")

    do_GET = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = do_CONNECT = _not_allowed
    do_TRACE = _not_allowed


def make_server(cfg: Config, host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"config": cfg})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main() -> int:
    cfg = config_from_env(dict(os.environ))
    for name in ("LLM_PROXY_KEY", "LLM_PROXY_TOKEN"):
        os.environ.pop(name, None)
    port = int(os.environ.get("LLM_PROXY_PORT") or 8080)
    server = make_server(cfg, port=port)
    log(f"{READY} on :{port} -> {cfg.scheme}://{cfg.host}{cfg.path}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
