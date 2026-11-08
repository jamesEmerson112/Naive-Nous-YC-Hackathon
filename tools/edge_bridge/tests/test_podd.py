"""Reading the healer's files and socket (podd.py)."""

import json
import os
import socketserver
import subprocess
import threading
from pathlib import Path

from edge_bridge import podd
from edge_bridge.fake_healer import T0
from edge_bridge.podd import Cursor, PoddPaths, PoddSocket


def _write(path: Path, *recs, partial: str = "") -> None:
    with open(path, "a") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
        f.write(partial)


def test_read_new_follows_complete_lines(tmp_path):
    path = tmp_path / "incidents.jsonl"
    assert podd.read_new(path, None) == ([], None)
    _write(path, {"ts": 1.0, "id": "a"}, partial='{"ts": 2.0, "id"')
    recs, cursor = podd.read_new(path, None)
    assert [r["id"] for r in recs] == ["a"]
    _write(path, {}, partial="")  # finishes nothing useful: '"id"{}' isn't JSON
    with open(path, "a") as f:
        f.write('{"ts": 3.0, "id": "c"}\n')
    recs, cursor = podd.read_new(path, cursor)
    assert [r["id"] for r in recs] == ["c"]
    assert podd.read_new(path, cursor) == ([], cursor)


def test_read_new_starts_over_on_a_replaced_or_truncated_file(tmp_path):
    path = tmp_path / "audit.jsonl"
    _write(path, {"ts": 1.0, "n": 1}, {"ts": 2.0, "n": 2})
    _, cursor = podd.read_new(path, None)
    path.unlink()
    _write(path, {"ts": 3.0, "n": 3})
    recs, cursor2 = podd.read_new(path, cursor)
    assert [r["n"] for r in recs] == [3]
    path.write_text("")
    _write(path, {"ts": 4.0, "n": 4})
    stale = Cursor(cursor2.inode, 10_000)
    assert [r["n"] for r in podd.read_new(path, stale)[0]] == [4]


def test_read_window_spans_rotated_files_oldest_first(tmp_path):
    base = tmp_path / "telemetry.jsonl"
    _write(tmp_path / "telemetry.jsonl.2", *({"ts": float(t)} for t in range(0, 10)))
    _write(tmp_path / "telemetry.jsonl.1", *({"ts": float(t)} for t in range(10, 20)))
    _write(base, *({"ts": float(t)} for t in range(20, 30)))
    assert [r["ts"] for r in podd.read_window(base, 8.0, 22.0)] == [float(t) for t in range(8, 23)]
    assert podd.read_window(base, 100.0, 200.0) == []


def test_tool_risks_come_from_tools_json(tmp_path):
    paths = PoddPaths.at(tmp_path)
    assert podd.tool_risks(paths) == {}
    (tmp_path / "tools.json").write_text(json.dumps([{"name": "fw_flash", "risk": "flash"}]))
    assert podd.tool_risks(paths) == {"fw_flash": "flash"}


def test_var_follows_healer_var_like_the_healer(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALER_VAR", str(tmp_path / "elsewhere"))
    assert PoddPaths.at(tmp_path).var == tmp_path / "elsewhere"
    monkeypatch.delenv("HEALER_VAR")
    assert PoddPaths.at(tmp_path).socket == tmp_path / "var" / "podd.sock"


def test_image_includes_its_source_patch(tmp_path):
    paths = PoddPaths.at(tmp_path)
    folder = paths.images / "5E6F7081"
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(json.dumps({"crc": "5E6F7081", "origin": "workspace"}))
    (folder / "source.patch").write_text("--- a/src/frame.c\n")
    assert podd.image(paths, "5E6F7081")["source_patch"] == "--- a/src/frame.c\n"
    assert podd.image(paths, "00000000") is None


def test_workspace_log_shows_commits_in_the_window(tmp_path):
    paths = PoddPaths.at(tmp_path)
    ws = paths.workspace
    ws.mkdir(parents=True)

    def git(*args, at=T0):
        env = os.environ | {"GIT_AUTHOR_DATE": f"@{int(at)} +0000",
                            "GIT_COMMITTER_DATE": f"@{int(at)} +0000"}  # fmt: skip
        cmd = ["git", "-c", "user.name=healer-agent", "-c", "user.email=h@pod", *args]
        subprocess.run(cmd, cwd=ws, env=env, check=True, capture_output=True)

    git("init", "-q")
    (ws / "frame.c").write_text("old\n")
    git("add", "-A")
    git("commit", "-qm", "v1.0", at=T0 - 3600)
    (ws / "frame.c").write_text("new\n")
    git("commit", "-qam", "frame: byte-wise packing", at=T0 + 30)
    log = podd.workspace_log(paths, T0, T0 + 60)
    assert "frame: byte-wise packing" in log and "+new" in log and "v1.0" not in log
    assert podd.workspace_log(paths, T0 + 100, T0 + 200) is None


def test_socket_speaks_podds_protocol(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # a short relative path: AF_UNIX paths are capped at ~104 bytes
    seen = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            req = json.loads(self.rfile.readline())
            seen.append(req)
            reply = {"ok": True, "uptime_s": 5} if req["op"] == "status" else {"ok": False}
            self.wfile.write((json.dumps(reply) + "\n").encode())

    server = socketserver.UnixStreamServer("p.sock", Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        sock = PoddSocket(Path("p.sock"))
        assert sock.call("status") == {"ok": True, "uptime_s": 5}
        assert sock.call("stats", seconds=10) is None  # podd answered ok: false
        assert seen == [{"op": "status"}, {"op": "stats", "seconds": 10}]
    finally:
        server.shutdown()
        server.server_close()
    assert PoddSocket(Path("missing.sock")).call("status") is None
