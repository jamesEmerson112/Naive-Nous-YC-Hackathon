"""Talk to the arm host, the way `firmware/healer/healer/client.py` talks to podd.

    from armsim import client
    client.call("skill", name="wave")
"""
import json
import os
import socket
import tempfile


def socket_path():
    """$ARMSIM_SOCK, else <temp dir>/armsim.sock. macOS caps Unix socket paths at 104 bytes,
    and the repo path is longer than that, so the socket can't live in the repo."""
    return os.environ.get("ARMSIM_SOCK") or os.path.join(tempfile.gettempdir(), "armsim.sock")


def call(op, *, sock=None, timeout=60.0, **fields):
    path = str(sock or socket_path())
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(path)
        s.sendall((json.dumps({"op": op, **fields}) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf or b'{"ok": false, "error": "no reply"}')
