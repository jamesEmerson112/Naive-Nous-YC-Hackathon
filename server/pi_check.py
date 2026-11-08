#!/usr/bin/env python3
"""Run on the Pi: check that the laptop's model answers the way the harness will call it.

    python3 pi_check.py                                  # laptop at 192.168.50.1
    python3 pi_check.py --url http://192.168.50.1:11434

Three steps: the model is listed, a plain chat answers, and a chat with thinking on and one tool
returns a tool call (what firmware/harness/agent.py's ollama driver needs). Stdlib only.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def call(url, path, body=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url + path, data, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://192.168.50.1:11434")
    p.add_argument("--model", default="qwen3.5:4b")
    p.add_argument("--num-ctx", type=int, default=32768)
    a = p.parse_args()
    url = a.url.rstrip("/")
    opts = {"num_ctx": a.num_ctx}

    try:
        names = [m["name"] for m in call(url, "/api/tags", timeout=5)["models"]]
    except (urllib.error.URLError, OSError) as e:
        sys.exit("FAIL  can't reach %s: %s\n      Is server/start.sh running on the laptop?" % (url, e))
    if a.model not in names:
        sys.exit("FAIL  %s isn't on the laptop (has: %s)" % (a.model, ", ".join(names)))
    print("ok    %s is listed at %s" % (a.model, url))

    t0 = time.time()
    r = call(url, "/api/chat", {"model": a.model, "stream": False, "think": False, "options": opts,
                                "messages": [{"role": "user", "content": "Reply with the single word: ready"}]})
    print("ok    plain chat in %.1f s: %r" % (time.time() - t0, r["message"]["content"].strip()[:60]))

    tool = {"type": "function", "function": {
        "name": "chip_status", "description": "Read the STM32 pod's status registers.",
        "parameters": {"type": "object", "properties": {}, "required": []}}}
    t0 = time.time()
    r = call(url, "/api/chat", {"model": a.model, "stream": False, "think": True, "options": opts,
                                "tools": [tool], "keep_alive": "30m",
                                "messages": [{"role": "user", "content": "The pod stopped reporting. "
                                              "Check its status with the tool."}]})
    secs, msg = time.time() - t0, r["message"]
    calls = [c["function"]["name"] for c in msg.get("tool_calls") or []]
    tok_s = r.get("eval_count", 0) / (r.get("eval_duration", 0) / 1e9 or 1)
    if not calls:
        sys.exit("FAIL  thinking + tools: no tool call in %.1f s; content was %r" % (secs, msg.get("content", "")[:200]))
    print("ok    thinking + tools in %.1f s: called %s, %d thinking chars, %.0f tokens/s"
          % (secs, ", ".join(calls), len(msg.get("thinking") or ""), tok_s))
    print("\nReady. From firmware/harness on the Pi:\n  python3 agent.py run --tools core --ollama-url %s --max-turns 6" % url)


if __name__ == "__main__":
    main()
