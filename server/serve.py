#!/usr/bin/env python3
"""Serve the laptop's Ollama model to the Pi over the direct Ethernet link.

Ollama keeps listening on localhost only. This script opens a second listener on the laptop's
Ethernet address (the side of the cable the Pi is on) and relays raw TCP to 127.0.0.1:11434, so the
Pi's harness can call http://<laptop-ethernet-ip>:11434/api/chat exactly as it would call a local
Ollama. Nothing listens on Wi-Fi, and only clients on the Ethernet subnet are let in.

Before it starts listening it checks that Ollama is up and has the model, then loads the model with
the harness's context size so the Pi's first request doesn't wait for a load.

    python3 server/serve.py                      # finds the Ethernet IP by routing towards the Pi
    python3 server/serve.py --bind 192.168.50.1  # or name it

Stdlib only. Stop it with Ctrl-C.
"""
import argparse
import asyncio
import ipaddress
import json
import socket
import sys
import time
import urllib.error
import urllib.request

METHODS = (b"GET ", b"POST ", b"HEAD ", b"PUT ", b"DELETE ", b"OPTIONS ")


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def local_ip_towards(host):
    """The laptop's address on the interface that routes to `host` (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 9))
        return s.getsockname()[0]
    finally:
        s.close()


def ollama(upstream, path, body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(upstream + path, data, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def preflight(upstream, model, num_ctx, keep_alive, warm):
    try:
        tags = ollama(upstream, "/api/tags")
    except (urllib.error.URLError, OSError) as e:
        sys.exit("Ollama isn't answering at %s (%s). Open the Ollama app or run `ollama serve`." % (upstream, e))
    names = [m["name"] for m in tags.get("models", [])]
    if model not in names:
        sys.exit("Ollama doesn't have %s (has: %s). Run `ollama pull %s`." % (model, ", ".join(names), model))
    if not warm:
        return
    log("loading %s with num_ctx %d (keep_alive %s)..." % (model, num_ctx, keep_alive))
    t0 = time.time()
    # An empty prompt loads the model without generating. The same num_ctx as the harness sends
    # means Ollama won't reload the model on the Pi's first request.
    ollama(upstream, "/api/generate", {"model": model, "prompt": "", "keep_alive": keep_alive,
                                       "options": {"num_ctx": num_ctx}}, timeout=600)
    log("loaded in %.1f s" % (time.time() - t0))


async def pipe(reader, writer, counter, peer, log_requests):
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            if log_requests and chunk.startswith(METHODS):
                log("%s  %s" % (peer, chunk.split(b"\r\n", 1)[0].decode(errors="replace")))
            counter[0] += len(chunk)
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


def make_handler(up_host, up_port, allowed):
    async def handle(c_reader, c_writer):
        peer_ip = c_writer.get_extra_info("peername")[0]
        if ipaddress.ip_address(peer_ip) not in allowed:
            log("refused %s (not in %s)" % (peer_ip, allowed))
            c_writer.close()
            return
        try:
            u_reader, u_writer = await asyncio.open_connection(up_host, up_port)
        except OSError as e:
            log("%s  Ollama unreachable at %s:%d: %s" % (peer_ip, up_host, up_port, e))
            c_writer.close()
            return
        t0, sent, received = time.time(), [0], [0]
        await asyncio.gather(pipe(c_reader, u_writer, sent, peer_ip, True),
                             pipe(u_reader, c_writer, received, peer_ip, False))
        log("%s  closed after %.1f s (%d B in, %d B out)" % (peer_ip, time.time() - t0, sent[0], received[0]))
    return handle


async def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pi", default="192.168.50.2", help="the Pi's address, used to find the Ethernet IP")
    p.add_argument("--bind", help="address to listen on (default: this laptop's IP on the route to --pi)")
    p.add_argument("--port", type=int, default=11434)
    p.add_argument("--allow", help="client subnet let in (default: the /24 around --bind)")
    p.add_argument("--upstream", default="http://127.0.0.1:11434", help="the local Ollama")
    p.add_argument("--model", default="qwen3.5:4b")
    p.add_argument("--num-ctx", type=int, default=65536, help="match the harness's --num-ctx (its default is 65536)")
    p.add_argument("--keep-alive", default="2h", help="how long Ollama keeps the model loaded after the warm-up")
    p.add_argument("--no-warm", action="store_true", help="don't load the model at startup")
    a = p.parse_args()

    bind = a.bind or local_ip_towards(a.pi)
    if ipaddress.ip_address(bind).is_loopback:
        sys.exit("no route to the Pi at %s; is the Ethernet cable plugged in? Or pass --bind." % a.pi)
    allowed = ipaddress.ip_network(a.allow or "%s/24" % bind, strict=False)
    upstream = a.upstream.rstrip("/")
    up = urllib.request.urlparse(upstream)

    preflight(upstream, a.model, a.num_ctx, a.keep_alive, not a.no_warm)

    try:
        server = await asyncio.start_server(make_handler(up.hostname, up.port or 80, allowed), bind, a.port)
    except OSError as e:
        sys.exit("can't listen on %s:%d: %s" % (bind, a.port, e))
    log("serving %s to %s at http://%s:%d  (relaying to %s)" % (a.model, allowed, bind, a.port, upstream))
    log("on the Pi:  python3 agent.py run --tools core --ollama-url http://%s:%d" % (bind, a.port))
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
