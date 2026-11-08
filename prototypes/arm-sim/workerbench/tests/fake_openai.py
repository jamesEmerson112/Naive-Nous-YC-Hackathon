"""A fake OpenAI-compatible chat server (standard library only) for testing workerbench/run.py.

The reply depends on the request's `model`, so one server covers every case:
  good            a valid healthy answer, with `usage`
  fault           a valid fault answer citing D01-D03
  fenced          the answer inside a ```json fence (lenient only)
  prose           text around the JSON object (lenient only)
  badjson         not JSON at all
  reasoning-only  empty content; the JSON is only in message.reasoning (must be invalid)
  length          a truncated answer with finish_reason "length" (must be invalid)
  slow            waits `slow_s` seconds, then a good answer
  native-usage    a good answer with Ollama's prompt_eval_count/eval_count instead of `usage`
  http500 / http429   that HTTP status
With "stream": true every mode streams server-sent events, ending with a usage chunk and [DONE].

    srv = FakeOpenAI(slow_s=3).start(); srv.url  -> "http://127.0.0.1:<port>/v1"; srv.stop()
    python3 -m workerbench.tests.fake_openai --port 18080     # by hand
"""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HEALTHY = {"status": "healthy", "kind": "none", "severity": "info", "evidence": [], "escalate": False, "message": ""}
FAULT = {"status": "fault", "kind": "us_frozen", "severity": "critical", "evidence": ["D01-D03"], "escalate": True,
         "message": "Ultrasonic distance is frozen with identical echo readings, so restart the sensor read loop."}


class FakeOpenAI:
    def __init__(self, host="127.0.0.1", port=0, slow_s=3.0):
        self.slow_s = slow_s
        self.requests = []                  # (path, headers dict, body dict) of every request
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                outer.requests.append((self.path, dict(self.headers), body))
                if not self.path.endswith("/chat/completions"):
                    return self._send(404, {"error": {"message": "not found"}})
                outer.reply(self, body)

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer((host, port), H)
        self.httpd.daemon_threads = True
        self.httpd.handle_error = lambda request, address: None    # a client that timed out closed the socket
        self.url = f"http://{host}:{self.httpd.server_address[1]}/v1"

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def reply(self, h, body):
        mode = body.get("model", "good")
        if mode == "http500":
            return h._send(500, {"error": {"message": "boom"}})
        if mode == "http429":
            return h._send(429, {"error": {"message": "slow down"}})
        if mode == "slow":
            time.sleep(self.slow_s)
        content, reasoning, finish = json.dumps(HEALTHY), "", "stop"
        if mode == "fault":
            content = json.dumps(FAULT)
        elif mode == "fenced":
            content = "```json\n" + json.dumps(FAULT) + "\n```"
        elif mode == "prose":
            content = "Here is my report: " + json.dumps(FAULT) + " Hope that helps."
        elif mode == "badjson":
            content = "status: healthy, kind none"
        elif mode == "reasoning-only":
            content, reasoning = "", "Thinking... " + json.dumps(FAULT)
        elif mode == "length":
            content, finish = json.dumps(FAULT)[:40], "length"
        usage = {"prompt_tokens": 1234, "completion_tokens": 56, "total_tokens": 1290}
        if body.get("stream"):
            return self._stream(h, content, reasoning, finish, usage)
        msg = {"role": "assistant", "content": content}
        if reasoning:
            msg["reasoning"] = reasoning
        out = {"id": "chatcmpl-fake", "object": "chat.completion", "model": mode,
               "choices": [{"index": 0, "message": msg, "finish_reason": finish}]}
        if mode == "native-usage":
            out.update(prompt_eval_count=777, eval_count=33)
        else:
            out["usage"] = usage
        h._send(200, out)

    def _stream(self, h, content, reasoning, finish, usage):
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream")
        h.end_headers()

        def ev(obj):
            h.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
            h.wfile.flush()
        if reasoning:
            ev({"choices": [{"index": 0, "delta": {"reasoning": reasoning}}]})
        for i in range(0, len(content), 16):
            ev({"choices": [{"index": 0, "delta": {"content": content[i:i + 16]}}]})
        ev({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
        ev({"choices": [], "usage": usage})
        h.wfile.write(b"data: [DONE]\n\n")
        h.wfile.flush()


def main():
    ap = argparse.ArgumentParser(description="Fake OpenAI-compatible server for workerbench tests.")
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--slow-s", type=float, default=3.0)
    a = ap.parse_args()
    srv = FakeOpenAI(port=a.port, slow_s=a.slow_s)
    print(f"serving {srv.url} (model = mode)", flush=True)
    srv.httpd.serve_forever()


if __name__ == "__main__":
    main()
