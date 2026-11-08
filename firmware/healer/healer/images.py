"""Firmware image registry: every image that is built or flashed is archived by build ID.

var/images/<CRC>/  pod.elf  pod.bin  meta.json  [source.patch]
CRC is the CRC-32 of the build ID string, which is also what the firmware
reports (E ev=boot crc=..., crash records' build_crc), so a crash from any
build can be symbolized with that build's own ELF.

Status values: candidate (built, not proven), known-good, bad, deployed-history
is in var/deployments.jsonl.
"""
import json
import shutil
import subprocess
import tempfile
import time
import zlib
from pathlib import Path

from . import config


def crc_of(build_id):
    return "%08X" % (zlib.crc32(build_id.encode()) & 0xFFFFFFFF)


def elf_build_id(elf):
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "info.bin"
        subprocess.run([config.gcc_tool("objcopy"), "-O", "binary", "--only-section=.fw_info", str(elf), str(out)],
                       check=True, capture_output=True)
        blob = out.read_bytes()
    if len(blob) < 64 or int.from_bytes(blob[:4], "little") != config.FW_INFO_MAGIC:
        raise ValueError("%s has no valid .fw_info section" % elf)
    return blob[4:64].split(b"\0", 1)[0].decode()


def _meta_path(crc):
    return config.IMAGES / crc / "meta.json"


def _save(meta):
    p = _meta_path(meta["crc"])
    p.write_text(json.dumps(meta, indent=2) + "\n")


def register(elf, status="candidate", origin="unknown", note=None, source_rev=None, patch=None, extra=None, src_dir=None):
    """Archive an ELF (and its .bin, and a snapshot of src_dir/*.c|h for symbolized source context).
    Re-registering the same build keeps the existing status."""
    config.ensure_dirs()
    elf = Path(elf)
    build_id = elf_build_id(elf)
    crc = crc_of(build_id)
    d = config.IMAGES / crc
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(elf, d / "pod.elf")
    subprocess.run([config.gcc_tool("objcopy"), "-O", "binary", str(d / "pod.elf"), str(d / "pod.bin")], check=True)
    if patch is not None:
        (d / "source.patch").write_text(patch)
    if src_dir is not None:
        snap = d / "src"
        snap.mkdir(exist_ok=True)
        for f in Path(src_dir).glob("*.[ch]"):
            shutil.copy2(f, snap / f.name)
    old = load(crc)
    parts = build_id.split()
    meta = {"crc": crc, "build_id": build_id, "version": parts[1] if len(parts) > 1 else None,
            "status": old["status"] if old else status, "origin": old["origin"] if old else origin,
            "created": old["created"] if old else time.time(), "source_rev": source_rev or (old or {}).get("source_rev"),
            "notes": (old or {}).get("notes", [])}
    if note:
        meta["notes"].append({"ts": time.time(), "note": note})
    if extra:
        meta |= extra
    meta["elf"] = str(d / "pod.elf")
    meta["bin"] = str(d / "pod.bin")
    _save(meta)
    return meta


def load(crc):
    p = _meta_path(crc)
    return json.loads(p.read_text()) if p.exists() else None


def all_images():
    if not config.IMAGES.exists():
        return []
    metas = [json.loads(p.read_text()) for p in config.IMAGES.glob("*/meta.json")]
    return sorted(metas, key=lambda m: m["created"])


def resolve(ref):
    """ref: CRC (any case, optional 0x, unique prefix ok), 'known-good', 'latest', 'deployed', or an ELF path."""
    ref = str(ref).strip()
    imgs = all_images()
    if ref in ("known-good", "known_good"):
        good = [m for m in imgs if m["status"] == "known-good"]
        if not good:
            raise KeyError("no image is marked known-good")
        return good[-1]
    if ref == "latest":
        if not imgs:
            raise KeyError("registry is empty")
        return imgs[-1]
    if ref in ("deployed", "current"):
        dep = last_deployment()
        if not dep:
            raise KeyError("no deployment recorded")
        return load(dep["crc"])
    if ref.endswith(".elf") and Path(ref).exists():
        return register(ref, origin="path")
    key = ref.upper().removeprefix("0X")
    hits = [m for m in imgs if m["crc"].startswith(key)]
    if len(hits) == 1:
        return hits[0]
    raise KeyError("no unique image matches %r (%d hits)" % (ref, len(hits)))


def set_status(ref, status, note=None):
    if status not in ("candidate", "known-good", "bad"):
        raise ValueError("status must be candidate, known-good or bad")
    meta = resolve(ref)
    meta["status"] = status
    meta.setdefault("notes", []).append({"ts": time.time(), "note": "status -> %s%s" % (status, ": " + note if note else "")})
    _save(meta)
    return meta


def record_deployment(crc, result, **fields):
    config.ensure_dirs()
    rec = {"ts": time.time(), "crc": crc, "result": result} | fields
    with open(config.VAR / "deployments.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def deployments(limit=20):
    p = config.VAR / "deployments.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines()[-limit:]]


def last_deployment():
    deps = [d for d in deployments(200) if d["result"] in ("ok", "unverified")]
    return deps[-1] if deps else None


def symbols(elf):
    out = subprocess.run([config.gcc_tool("nm"), "-S", "-C", str(elf)], capture_output=True, text=True, check=True).stdout
    syms = {}
    for line in out.splitlines():
        p = line.split()
        if len(p) == 4:
            syms[p[3]] = (int(p[0], 16), int(p[1], 16))
        elif len(p) == 3:
            syms[p[2]] = (int(p[0], 16), 0)
    return syms


def public(meta):
    return {k: meta.get(k) for k in ("crc", "build_id", "version", "status", "origin", "source_rev", "created", "notes")}
