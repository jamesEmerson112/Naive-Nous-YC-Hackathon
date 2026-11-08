"""The coding agent's egress proxy (B5): allowlist, key swap, caps, against a fake upstream."""

import http.client
import importlib.util
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from romulus_cloud.adapters import coder_egress

PROXY_FILE = Path(__file__).resolve().parents[3] / "coder" / "llm_proxy.py"
_spec = importlib.util.spec_from_file_location("coder_llm_proxy", PROXY_FILE)
llm_proxy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(llm_proxy)

REAL_KEY = "sk-real-UPSTREAM-KEY-42"
TOKEN = "romulus-job-abc123"
CHAT = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}


class Upstream:
    """A fake LLM endpoint that records what reached it."""

    def __init__(self, status: int = 200, reply: bytes | None = None) -> None:
        self.seen: list[dict] = []
        upstream = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _any(self):
                n = int(self.headers.get("Content-Length") or 0)
                upstream.seen.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": self.rfile.read(n),
                    }
                )
                body = reply if reply is not None else b'{"choices": [], "ok": true}'
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_POST = do_GET = _any

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/v1"


@pytest.fixture
def make_proxy():
    servers = []

    def make(upstream: Upstream, **kw):
        cfg = llm_proxy.Config(upstream=upstream.url, key=REAL_KEY, token=TOKEN, **kw)
        server = llm_proxy.make_server(cfg, host="127.0.0.1", port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server.server_address[1]

    yield make
    for s in servers:
        s.shutdown()


def call(port, method="POST", path="/v1/chat/completions", body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    data = json.dumps(CHAT if body is None else body).encode() if method == "POST" else None
    hdrs = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    hdrs.update(headers or {})
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    out = resp.status, resp.read()
    conn.close()
    return out


def test_chat_completions_is_forwarded_with_the_real_key_swapped_in(make_proxy, capsys):
    up = Upstream()
    port = make_proxy(up)
    status, body = call(port, headers={"X-Evil": "1", "Authorization": f"Bearer {TOKEN}"})
    assert status == 200 and json.loads(body)["ok"] is True
    (req,) = up.seen
    assert req["method"] == "POST" and req["path"] == "/api/v1/chat/completions"
    assert req["headers"]["Authorization"] == f"Bearer {REAL_KEY}"
    assert TOKEN not in json.dumps(req["headers"])
    assert "X-Evil" not in req["headers"]  # only Content-Type and Accept pass through
    assert json.loads(req["body"]) == CHAT
    log = capsys.readouterr().err
    assert "llm_proxy forwarded POST /v1/chat/completions -> 200" in log and "req 1/100" in log
    assert REAL_KEY not in log and TOKEN not in log and "hi" not in log  # no bodies, no keys


@pytest.mark.parametrize(
    "method,path,status",
    [
        ("GET", "/v1/models", 405),
        ("GET", "/v1/chat/completions", 405),
        ("DELETE", "/v1/chat/completions", 405),
        ("POST", "/v1/models", 403),
        ("POST", "/v1/embeddings", 403),
        ("POST", "/v1/chat/completions?x=1", 403),
        ("POST", "/v1/chat/completions/../models", 403),
        ("POST", "http://example.com/v1/chat/completions", 403),  # absolute-URI proxying
        ("POST", "http://127.0.0.1:1/v1/chat/completions", 403),
    ],
)
def test_other_paths_methods_and_hosts_are_refused(make_proxy, method, path, status, capsys):
    up = Upstream()
    port = make_proxy(up)
    assert call(port, method, path)[0] == status
    assert up.seen == []
    assert "llm_proxy refused" in capsys.readouterr().err


def test_connect_is_refused(make_proxy):
    up = Upstream()
    port = make_proxy(up)
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
        assert s.recv(200).startswith(b"HTTP/1.1 405")
    assert up.seen == []


def test_wrong_or_missing_token_is_refused(make_proxy):
    up = Upstream()
    port = make_proxy(up)
    assert call(port, headers={"Authorization": f"Bearer {REAL_KEY}"})[0] == 401
    assert call(port, headers={"Authorization": ""})[0] == 401
    assert up.seen == []


def test_body_cap(make_proxy):
    up = Upstream()
    port = make_proxy(up, max_body=200)
    big = {**CHAT, "messages": [{"role": "user", "content": "x" * 500}]}
    assert call(port, body=big)[0] == 413
    assert call(port)[0] == 200
    assert len(up.seen) == 1


def test_chunked_body_is_refused(make_proxy):
    up = Upstream()
    port = make_proxy(up)
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(
            b"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\n"
            + f"Authorization: Bearer {TOKEN}\r\n".encode()
            + b"Transfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n"
        )
        assert s.recv(200).startswith(b"HTTP/1.1 411")
    assert up.seen == []


def test_per_job_request_cap(make_proxy, capsys):
    up = Upstream()
    port = make_proxy(up, max_requests=2)
    assert [call(port)[0] for _ in range(3)] == [200, 200, 429]
    assert len(up.seen) == 2
    assert "per-job cap of 2 requests reached" in capsys.readouterr().err


def test_upstream_errors_pass_through_with_the_key_redacted(make_proxy):
    up = Upstream(status=401, reply=f'{{"error": "bad key {REAL_KEY}"}}'.encode())
    port = make_proxy(up)
    status, body = call(port)
    assert status == 401 and REAL_KEY.encode() not in body and b"[redacted]" in body


def test_unreachable_upstream_is_a_502(make_proxy):
    up = Upstream()
    up.server.shutdown()
    up.server.server_close()
    port = make_proxy(up)
    assert call(port)[0] == 502


def test_config_refuses_bad_upstreams():
    for bad in ("", "ftp://x/v1", "http://u:p@x/v1", "http://x/v1?a=1"):
        with pytest.raises(ValueError):
            llm_proxy.Config(upstream=bad, key=REAL_KEY, token=TOKEN)
    with pytest.raises(ValueError, match="TOKEN"):
        llm_proxy.Config(upstream="https://x/v1", key=REAL_KEY, token="")


def test_host_side_log_prefixes_match_the_proxy():
    assert coder_egress.READY == llm_proxy.READY
    assert coder_egress.FORWARDED == llm_proxy.FORWARDED
    assert coder_egress.REFUSED == llm_proxy.REFUSED
    assert coder_egress.PROXY_SCRIPT.endswith(PROXY_FILE.name)
    logs = (
        f"{llm_proxy.READY} on :8080\n{llm_proxy.FORWARDED} POST ... -> 200\n"
        f"{llm_proxy.REFUSED} GET /v1/models -> 405: method GET not allowed\n"
    )
    s = coder_egress.summarize(logs)
    assert (s.ready, s.forwarded, s.refused) == (True, 1, 1)
    assert s.note() == "llm proxy: 1 forwarded, 1 refused"
