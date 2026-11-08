"""Talk to podd over its Unix socket."""
import json
import socket

from . import config


class PoddUnavailable(ConnectionError):
    pass


def call(op, timeout=15.0, **kw):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(config.SOCK))
    except (FileNotFoundError, ConnectionRefusedError) as e:
        s.close()
        raise PoddUnavailable("podd is not running (start it: firmware/healer/bin/podd)") from e
    try:
        s.sendall((json.dumps({"op": op} | kw) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    return json.loads(buf)
