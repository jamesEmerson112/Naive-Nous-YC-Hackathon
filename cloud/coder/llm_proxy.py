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
- the body is rebuilt from an allowlist (`upstream_body`): `model` is forced to
  LLM_PROXY_MODEL, `messages` keep only `role` and string `content`, `max_tokens` is capped,
  `n` is 1, `temperature`/`top_p` are clamped, `stop` is kept if small; everything else
  (`stream`, `tools`, `web_search_options`, provider extras) is dropped;
- every other path, method (`CONNECT` and unknown ones included), absolute-URI request line,
  query string, wrong token, chunked, oversized, malformed or slow body, request past the
  per-job cap, or connection past the concurrency cap is refused;
- the upstream host comes from config, never from the request; redirects are not followed;
- it exits on its own at LLM_PROXY_TTL_S, so an orphaned proxy (worker killed) can't keep
  the key alive;
- one log line per request on stderr (method, path, status, sizes, time): no bodies, no keys.
  Refusals of the agent's requests and upstream failures have separate prefixes.

litellm's OpenAI-compatible route (mini-swe-agent's text-based model, `openai/<model>`) only
calls `POST {api_base}/chat/completions` with `model`, `messages` (`role`, `content`) and
`max_tokens` (checked against mini-swe-agent 2.4.6), so that is the whole allowlist.

Env: LLM_PROXY_UPSTREAM (the real LLM_BASE_URL, e.g. https://host/v1), LLM_PROXY_KEY,
LLM_PROXY_TOKEN, LLM_PROXY_MODEL, LLM_PROXY_MAX_TOKENS (default 2000), LLM_PROXY_MAX_REQUESTS
(default 100), LLM_PROXY_MAX_BODY (bytes, default 4 MiB), LLM_PROXY_MAX_CONNS (default 8),
LLM_PROXY_TTL_S (default 0 = none), LLM_PROXY_PORT (default 8080), LLM_PROXY_TIMEOUT_S
(upstream, default 180), LLM_PROXY_READ_TIMEOUT_S (agent socket reads, default 10).
"""

import hmac
import http.client
import json
import math
import os
import ssl
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

ALLOWED_PATH = "/v1/chat/completions"
READY = "llm_proxy ready"
REFUSED = "llm_proxy refused"
FORWARDED = "llm_proxy forwarded"
UPSTREAM_ERROR = "llm_proxy upstream_error"
DEADLINE = "llm_proxy deadline"
PASS_HEADERS = ("Content-Type", "Accept")
ROLES = {"system", "user", "assistant"}
MAX_MESSAGES = 500
MAX_STOP = 4


class BadBody(ValueError):
    """The agent's body isn't a chat completion we forward."""


@dataclass
class Config:
    upstream: str  # e.g. https://llm.example/v1
    key: str
    token: str
    model: str
    max_tokens: int = 2000
    max_requests: int = 100
    max_body: int = 4 * 1024 * 1024
    max_conns: int = 8
    timeout_s: float = 180.0
    read_timeout_s: float = 10.0
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
        if not self.model:
            raise ValueError("LLM_PROXY_MODEL is required")
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
        model=env.get("LLM_PROXY_MODEL", ""),
        max_tokens=int(env.get("LLM_PROXY_MAX_TOKENS") or 2000),
        max_requests=int(env.get("LLM_PROXY_MAX_REQUESTS") or 100),
        max_body=int(env.get("LLM_PROXY_MAX_BODY") or 4 * 1024 * 1024),
        max_conns=int(env.get("LLM_PROXY_MAX_CONNS") or 8),
        timeout_s=float(env.get("LLM_PROXY_TIMEOUT_S") or 180),
        read_timeout_s=float(env.get("LLM_PROXY_READ_TIMEOUT_S") or 10),
    )


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise BadBody(f"{name} must be a finite number")
    return float(value)


def upstream_body(raw: bytes, cfg: Config) -> bytes:
    """Rebuild the agent's body from the allowlist (see module doc); raise BadBody."""
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadBody("body is not JSON") from exc
    except RecursionError as exc:  # e.g. 100,000 nested `[`
        raise BadBody("body is nested too deeply") from exc
    if not isinstance(body, dict):
        raise BadBody("body is not a JSON object")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
        raise BadBody(f"messages must be a list of 1-{MAX_MESSAGES} messages")
    clean = []
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") not in ROLES:
            raise BadBody(f"each message needs a role in {sorted(ROLES)}")
        if not isinstance(msg.get("content"), str):
            raise BadBody("each message's content must be a string")
        clean.append({"role": msg["role"], "content": msg["content"]})
    out: dict[str, Any] = {"model": cfg.model, "messages": clean}
    max_tokens = body.get("max_tokens")
    out["max_tokens"] = cfg.max_tokens
    if max_tokens is not None:
        out["max_tokens"] = max(1, min(int(_number(max_tokens, "max_tokens")), cfg.max_tokens))
    if "n" in body:
        out["n"] = 1
    if body.get("temperature") is not None:
        out["temperature"] = min(max(_number(body["temperature"], "temperature"), 0.0), 2.0)
    if body.get("top_p") is not None:
        out["top_p"] = min(max(_number(body["top_p"], "top_p"), 0.0), 1.0)
    stop = body.get("stop")
    if isinstance(stop, str) and len(stop) <= 200:
        out["stop"] = stop
    elif isinstance(stop, list) and len(stop) <= MAX_STOP:
        if all(isinstance(s, str) and len(s) <= 200 for s in stop):
            out["stop"] = stop
    return json.dumps(out).encode()


def log(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "romulus-llm-proxy"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    config: Config  # set by make_server

    def setup(self) -> None:
        self.timeout = self.config.read_timeout_s  # every read from the agent, body included
        super().setup()

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        pass  # our own one line per request instead

    def send_error(self, code: int, message: str | None = None, explain: str | None = None):
        """The stdlib's own refusals (unknown method 501, malformed request 400, ...)."""
        log(f"{REFUSED} {self.command or '-'} {self._shown_path()} -> {code}: {message or ''}")
        super().send_error(code, message, explain)

    def _refuse(self, status: int, why: str) -> None:
        log(f"{REFUSED} {self.command} {self._shown_path()} -> {status}: {why}")
        body = json.dumps({"error": {"message": f"llm proxy: {why}", "type": "refused"}})
        self._send(status, body.encode(), "application/json")
        self.close_connection = True

    def _shown_path(self) -> str:
        path = getattr(self, "path", "") or "-"
        return path[:200].replace("\n", " ").replace("\r", " ")

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
        try:
            raw = self.rfile.read(length)
        except TimeoutError:
            return self._refuse(408, f"body not sent within {cfg.read_timeout_s:g} s")
        if len(raw) != length:
            return self._refuse(400, "body shorter than Content-Length")
        try:
            body = upstream_body(raw, cfg)
        except BadBody as exc:
            return self._refuse(400, str(exc))
        n = cfg.take()  # counted only once a whole, valid body is in hand
        if n is None:
            return self._refuse(429, f"per-job cap of {cfg.max_requests} requests reached")
        started = time.monotonic()
        try:
            status, content_type, reply = self._forward(body)
        except (OSError, http.client.HTTPException) as exc:
            log(f"{UPSTREAM_ERROR} POST {self.path} -> 502: {type(exc).__name__}")
            msg = {"error": {"message": f"llm proxy: upstream {type(exc).__name__}"}}
            return self._send(502, json.dumps(msg).encode(), "application/json")
        if cfg.key:  # an upstream error that echoes the key never reaches the agent
            reply = reply.replace(cfg.key.encode(), b"[redacted]")
        log(
            f"{FORWARDED} POST {self.path} -> {status} in={len(body)}B out={len(reply)}B "
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
        headers["Content-Type"] = "application/json"
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


class Server(ThreadingHTTPServer):
    """A thread per connection, at most `max_conns` at once; extra connections are closed."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type, max_conns: int) -> None:
        super().__init__(address, handler)
        self.max_conns = max_conns
        self.slots = threading.BoundedSemaphore(max_conns)

    def process_request(self, request, client_address) -> None:
        if not self.slots.acquire(blocking=False):
            log(f"{REFUSED} - - -> 503: over {self.max_conns} concurrent connections")
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def make_server(cfg: Config, host: str = "0.0.0.0", port: int = 8080) -> Server:
    handler = type("BoundHandler", (Handler,), {"config": cfg})
    return Server((host, port), handler, cfg.max_conns)


def start_deadline(ttl_s: float, exit_fn: Callable[[], None]) -> threading.Timer | None:
    """After `ttl_s` seconds, log and call `exit_fn` (os._exit in the container)."""
    if ttl_s <= 0:
        return None

    def fire() -> None:
        log(f"{DEADLINE} reached after {ttl_s:g} s, exiting")
        exit_fn()

    timer = threading.Timer(ttl_s, fire)
    timer.daemon = True
    timer.start()
    return timer


def main() -> int:
    cfg = config_from_env(dict(os.environ))
    for name in ("LLM_PROXY_KEY", "LLM_PROXY_TOKEN"):
        os.environ.pop(name, None)
    port = int(os.environ.get("LLM_PROXY_PORT") or 8080)
    server = make_server(cfg, port=port)
    start_deadline(float(os.environ.get("LLM_PROXY_TTL_S") or 0), lambda: os._exit(0))
    log(f"{READY} on :{port} -> {cfg.scheme}://{cfg.host}{cfg.path} model {cfg.model}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
