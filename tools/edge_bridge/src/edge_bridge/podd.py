"""Read what Olir's pod healer already writes (`firmware/healer`, `firmware/docs/HARNESS.md`).

This is the only module that touches the healer, and it only reads: the JSONL logs under
`var/`, podd's `status` and `stats` over its Unix socket, the image registry and the
workspace's git log. It never calls a heal tool, so it can't change the pod. Paths follow
`healer/config.py`: `var/` is `$HEALER_VAR`, else `<healer>/var`.

Every record keeps podd's own shape. Times are podd's `ts`: host epoch seconds.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ROTATED_KEEP = 5
"""podd's RotatingLog keeps `name.jsonl.1` … `.5` (podd.py)."""


@dataclass(frozen=True)
class PoddPaths:
    """Where the healer keeps things, as in `healer/config.py`."""

    healer: Path
    var: Path

    @classmethod
    def at(cls, healer: str | Path, var: str | Path | None = None) -> PoddPaths:
        healer = Path(healer)
        var = var or os.environ.get("HEALER_VAR") or healer / "var"
        return cls(healer=healer, var=Path(var))

    @property
    def logs(self) -> Path:
        return self.var / "logs"

    def log(self, name: str) -> Path:
        """`telemetry`, `health`, `events`, `incidents` or `audit` → its `.jsonl`."""
        return self.logs / f"{name}.jsonl"

    @property
    def notifications(self) -> Path:
        return self.var / "notifications.jsonl"

    @property
    def deployments(self) -> Path:
        return self.var / "deployments.jsonl"

    @property
    def images(self) -> Path:
        return self.var / "images"

    @property
    def workspace(self) -> Path:
        return self.var / "workspace" / "pod"

    @property
    def socket(self) -> Path:
        return self.var / "podd.sock"

    @property
    def tools_manifest(self) -> Path:
        return self.healer / "tools.json"


# -- following one file ------------------------------------------------------------------


@dataclass(frozen=True)
class Cursor:
    """How far one file has been read: its inode and the byte offset after the last line."""

    inode: int
    offset: int

    def to_dict(self) -> dict[str, int]:
        return {"inode": self.inode, "offset": self.offset}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Cursor:
        return cls(inode=int(data["inode"]), offset=int(data["offset"]))


def read_new(path: Path, cursor: Cursor | None) -> tuple[list[dict[str, Any]], Cursor | None]:
    """The complete lines added to `path` since `cursor`, parsed, and the new cursor.

    A missing file returns nothing and keeps the cursor. A replaced file (new inode) or a
    shorter one (truncated) is read from the start. A line still being written (no newline
    yet) is left for the next read. Lines that aren't JSON objects are skipped.
    """
    try:
        st = path.stat()
    except FileNotFoundError:
        return [], cursor
    offset = cursor.offset if cursor and cursor.inode == st.st_ino else 0
    if offset > st.st_size:
        offset = 0
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(st.st_size - offset)
    end = data.rfind(b"\n") + 1
    return _parse(data[:end], path), Cursor(st.st_ino, offset + end)


def read_all(path: Path) -> list[dict[str, Any]]:
    """Every record in one file (for the small, unrotated ones)."""
    try:
        return _parse(path.read_bytes(), path)
    except FileNotFoundError:
        return []


def read_window(path: Path, lo: float, hi: float) -> list[dict[str, Any]]:
    """Records with `lo <= ts <= hi` from a rotated log (`path.5` … `path.1`, then `path`).

    Rotated files are oldest first; a file whose first and last records both fall outside
    the window is skipped without parsing it.
    """
    out: list[dict[str, Any]] = []
    for p in _rotations(path):
        span = _span(p)
        if span is None or span[1] < lo or span[0] > hi:
            continue
        out.extend(r for r in read_all(p) if lo <= _ts(r) <= hi)
    out.sort(key=_ts)
    return out


def _rotations(path: Path) -> Iterator[Path]:
    for i in range(ROTATED_KEEP, 0, -1):
        rotated = path.with_name(f"{path.name}.{i}")
        if rotated.exists():
            yield rotated
    if path.exists():
        yield path


def _span(path: Path, probe: int = 8192) -> tuple[float, float] | None:
    """`ts` of the first and last parseable records, reading only the file's two ends."""
    size = path.stat().st_size
    with open(path, "rb") as f:
        head = f.read(min(size, probe))
        f.seek(max(0, size - probe))
        tail = f.read()
    first = next((_ts(r) for r in _parse(head, path, quiet=True)), None)
    last = next((_ts(r) for r in reversed(_parse(tail, path, quiet=True))), None)
    return None if first is None or last is None else (first, last)


def _parse(data: bytes, path: Path, quiet: bool = False) -> list[dict[str, Any]]:
    out = []
    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            if not quiet:
                log.debug("skipping a line that isn't JSON in %s", path.name)
            continue
        if isinstance(rec, dict) and isinstance(rec.get("ts"), int | float):
            out.append(rec)
    return out


def _ts(rec: dict[str, Any]) -> float:
    return float(rec["ts"])


# -- the healer's other state ------------------------------------------------------------


def tool_risks(paths: PoddPaths) -> dict[str, str]:
    """Each heal tool's risk level from `tools.json` (`read`, `runtime`, `code`, `flash`,
    `notify`). Missing or unreadable: an empty map, and every tool counts as an action."""
    try:
        manifest = json.loads(paths.tools_manifest.read_text())
        return {t["name"]: t["risk"] for t in manifest}
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("can't read %s; every heal tool will count as an action", paths.tools_manifest)
        return {}


def image(paths: PoddPaths, crc: str) -> dict[str, Any] | None:
    """An image's registry entry (`images/<CRC>/meta.json`) plus its `source.patch`, which
    `fw_build` saves as the workspace's uncommitted diff at build time (workspace.py)."""
    folder = paths.images / crc
    try:
        meta = json.loads((folder / "meta.json").read_text())
    except (OSError, ValueError):
        return None
    patch = folder / "source.patch"
    if patch.exists():
        meta["source_patch"] = patch.read_text(errors="replace")
    return meta


def workspace_log(paths: PoddPaths, lo: float, hi: float) -> str | None:
    """The workspace commits made between `lo` and `hi`, with their patches (`git log -p`).

    None when there's no workspace, no git, or no commit in the window.
    """
    if not (paths.workspace / ".git").exists():
        return None
    args = [
        "git",
        "-C",
        str(paths.workspace),
        "log",
        "-p",
        "--reverse",
        "--date=iso",
        f"--since=@{int(lo)}",
        f"--until=@{int(hi) + 1}",
    ]
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None


# -- the socket --------------------------------------------------------------------------


class PoddSocket:
    """podd's request socket: one JSON object per line in, one per line out (podd.py)."""

    def __init__(self, path: Path, timeout: float = 3.0) -> None:
        self.path = path
        self.timeout = timeout

    def call(self, op: str, **fields: Any) -> dict[str, Any] | None:
        """podd's answer, or None when podd isn't running or doesn't answer."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(str(self.path))
            s.sendall((json.dumps({"op": op} | fields) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
            reply = json.loads(buf)
        except (OSError, ValueError):
            return None
        finally:
            s.close()
        return reply if isinstance(reply, dict) and reply.get("ok") else None
