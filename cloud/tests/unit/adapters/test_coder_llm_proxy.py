"""The coding agent's egress proxy (B5): allowlist, body rebuild, key swap, caps, timeouts and
its self-exit deadline, against a fake upstream."""

import http.client
import importlib.util
import json
import socket
import threading
import time
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
MODEL = "configured-model"
CHAT = {"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}


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
        kw.setdefault("model", MODEL)
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
            llm_proxy.Config(upstream=bad, key=REAL_KEY, token=TOKEN, model=MODEL)
    with pytest.raises(ValueError, match="TOKEN"):
        llm_proxy.Config(upstream="https://x/v1", key=REAL_KEY, token="", model=MODEL)
    with pytest.raises(ValueError, match="MODEL"):
        llm_proxy.Config(upstream="https://x/v1", key=REAL_KEY, token=TOKEN, model="")


def test_host_side_log_prefixes_match_the_proxy():
    assert coder_egress.READY == llm_proxy.READY
    assert coder_egress.FORWARDED == llm_proxy.FORWARDED
    assert coder_egress.REFUSED == llm_proxy.REFUSED
    assert coder_egress.UPSTREAM_ERROR == llm_proxy.UPSTREAM_ERROR
    assert coder_egress.PROXY_SCRIPT.endswith(PROXY_FILE.name)
    logs = (
        f"{llm_proxy.READY} on :8080\n{llm_proxy.FORWARDED} POST ... -> 200\n"
        f"{llm_proxy.REFUSED} GET /v1/models -> 405: method GET not allowed\n"
    )
    s = coder_egress.summarize(logs)
    assert (s.ready, s.forwarded, s.refused) == (True, 1, 1)
    assert s.note() == "llm proxy: 1 forwarded, 1 refused"


# -- the body allowlist (review finding 1) ---------------------------------------------------


def test_the_review_body_arrives_with_the_model_forced_n_1_capped_tokens_and_no_extras(
    make_proxy,
):
    up = Upstream()
    port = make_proxy(up, max_tokens=2000)
    body = {
        "model": "ANY",
        "n": 64,
        "max_tokens": 100000,
        "web_search_options": {},
        "functions": [{"name": "f"}],
        "tools": [{"type": "function"}],
        "tool_choice": "auto",
        "stream": True,
        "extra_body": {"x": 1},
        "messages": [{"role": "user", "content": "hi", "name": "x", "tool_calls": []}],
    }
    assert call(port, body=body)[0] == 200
    (req,) = up.seen
    assert json.loads(req["body"]) == {
        "model": MODEL,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 2000,
        "n": 1,
    }


def _rebuilt(body: dict, **kw) -> dict:
    cfg = llm_proxy.Config(upstream="https://x/v1", key="", token=TOKEN, model=MODEL, **kw)
    return json.loads(llm_proxy.upstream_body(json.dumps(body).encode(), cfg))


def test_max_tokens_defaults_to_the_cap_and_small_asks_are_kept():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    assert _rebuilt({"messages": msgs}, max_tokens=300)["max_tokens"] == 300
    assert _rebuilt({"messages": msgs, "max_tokens": 10}, max_tokens=300)["max_tokens"] == 10
    assert _rebuilt({"messages": msgs, "max_tokens": 0}, max_tokens=300)["max_tokens"] == 1
    assert "n" not in _rebuilt({"messages": msgs})


def test_sampling_values_are_clamped_and_stop_kept_only_if_small():
    msgs = [{"role": "user", "content": "u"}]
    out = _rebuilt({"messages": msgs, "temperature": 9, "top_p": -1, "stop": ["a", "b"]})
    assert (out["temperature"], out["top_p"], out["stop"]) == (2.0, 0.0, ["a", "b"])
    assert "stop" not in _rebuilt({"messages": msgs, "stop": ["x"] * 5})
    assert "stop" not in _rebuilt({"messages": msgs, "stop": "x" * 201})


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        [],
        {"messages": []},
        {"messages": "hi"},
        {"messages": [{"role": "tool", "content": "x"}]},
        {"messages": [{"role": "user", "content": [{"type": "image_url"}]}]},
        {"messages": [{"role": "user", "content": "x"}], "max_tokens": "lots"},
        {"messages": [{"role": "user", "content": "x"}], "temperature": True},
        {"messages": [{"role": "user", "content": "x"}] * (llm_proxy.MAX_MESSAGES + 1)},
    ],
)
def test_bodies_that_are_not_plain_chat_completions_are_refused(make_proxy, body, capsys):
    up = Upstream()
    port = make_proxy(up)
    assert call(port, body=body)[0] == 400
    assert up.seen == []
    assert "llm_proxy refused POST /v1/chat/completions -> 400" in capsys.readouterr().err


# -- refusal accounting (finding 3) ----------------------------------------------------------


def _raw(port: int, data: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(data)
        return s.recv(200)


@pytest.mark.parametrize(
    "request_line,status",
    [
        (b"FOO /v1/chat/completions HTTP/1.1", b"HTTP/1.1 501"),
        (b"post /v1/chat/completions HTTP/1.1", b"HTTP/1.1 501"),
        (b"POST /v1/chat/completions HTTP/9.9", b""),  # 505, as a bare body
        (b"garbage", b""),  # HTTP/0.9: a bare error body, no status line
    ],
)
def test_the_stdlibs_own_refusals_count_as_refusals(make_proxy, capsys, request_line, status):
    up = Upstream()
    port = make_proxy(up)
    assert _raw(port, request_line + b"\r\nHost: x\r\n\r\n").startswith(status)
    assert up.seen == []
    summary = coder_egress.summarize(capsys.readouterr().err)
    assert summary.refused == 1 and summary.upstream_errors == 0


def test_an_upstream_outage_is_counted_apart_from_refusals(make_proxy, capsys):
    up = Upstream()
    up.server.shutdown()
    up.server.server_close()
    port = make_proxy(up)
    assert call(port)[0] == 502
    summary = coder_egress.summarize(capsys.readouterr().err)
    assert (summary.refused, summary.upstream_errors) == (0, 1)
    assert summary.note() == "llm proxy: 0 forwarded, 0 refused, 1 upstream errors"


# -- read timeout and connection cap (finding 4) ---------------------------------------------


def test_a_body_shorter_than_its_content_length_times_out(make_proxy, capsys):
    up = Upstream()
    port = make_proxy(up, read_timeout_s=0.3)
    started = time.monotonic()
    reply = _raw(
        port,
        b"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\n"
        + f"Authorization: Bearer {TOKEN}\r\n".encode()
        + b"Content-Length: 50\r\n\r\n{}",
    )
    assert reply.startswith(b"HTTP/1.1 408") and time.monotonic() - started < 3
    assert up.seen == []
    assert coder_egress.summarize(capsys.readouterr().err).refused == 1


def test_an_idle_connection_is_dropped_after_the_read_timeout(make_proxy):
    port = make_proxy(Upstream(), read_timeout_s=0.3)
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        assert s.recv(10) == b""  # closed by the proxy, not by our 5 s timeout


def test_connections_past_the_cap_are_closed(make_proxy, capsys):
    up = Upstream()
    port = make_proxy(up, max_conns=1, read_timeout_s=2)
    with socket.create_connection(("127.0.0.1", port), timeout=5) as held:
        held.sendall(b"POST")  # holds the only slot
        time.sleep(0.2)
        with socket.create_connection(("127.0.0.1", port), timeout=5) as extra:
            assert extra.recv(10) == b""
    assert "over 1 concurrent connections" in capsys.readouterr().err
    time.sleep(0.2)  # the held slot is released once its handler gives up
    deadline = time.monotonic() + 5
    while call(port)[0] != 200:
        assert time.monotonic() < deadline
        time.sleep(0.2)
    assert len(up.seen) == 1


# -- the self-exit deadline (finding 2) ------------------------------------------------------


def test_the_proxy_exits_on_its_own_at_its_deadline(capsys):
    fired = threading.Event()
    assert llm_proxy.start_deadline(0, fired.set) is None  # 0 = no deadline
    started = time.monotonic()
    timer = llm_proxy.start_deadline(0.2, fired.set)
    assert timer is not None and timer.daemon
    assert fired.wait(5) and time.monotonic() - started >= 0.2
    assert "llm_proxy deadline reached after 0.2 s, exiting" in capsys.readouterr().err


def test_config_from_env_reads_the_new_caps():
    cfg = llm_proxy.config_from_env(
        {
            "LLM_PROXY_UPSTREAM": "https://x/v1",
            "LLM_PROXY_TOKEN": TOKEN,
            "LLM_PROXY_MODEL": MODEL,
            "LLM_PROXY_MAX_TOKENS": "321",
            "LLM_PROXY_MAX_CONNS": "3",
            "LLM_PROXY_READ_TIMEOUT_S": "4",
        }
    )
    assert (cfg.model, cfg.max_tokens, cfg.max_conns, cfg.read_timeout_s) == (MODEL, 321, 3, 4.0)


def test_deeply_nested_json_is_a_counted_400_not_a_dropped_connection(make_proxy, capsys):
    up = Upstream()
    port = make_proxy(up)
    raw = b"[" * 100_000
    reply = _raw(
        port,
        b"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\n"
        + f"Authorization: Bearer {TOKEN}\r\nContent-Length: {len(raw)}\r\n\r\n".encode()
        + raw,
    )
    assert reply.startswith(b"HTTP/1.1 400")
    assert up.seen == []
    log = capsys.readouterr().err
    assert "nested too deeply" in log and "Traceback" not in log
    assert coder_egress.summarize(log).refused == 1
