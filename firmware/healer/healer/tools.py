"""The tools an LLM harness can call. Each is a Python function plus a JSON schema.

Risk levels (a harness can gate on them):
  read     observes only (telemetry, logs, SWD reads, source reads)
  runtime  changes running state, reversible, no new code (pod commands, reset, baud)
  code     edits or builds firmware source in the workspace (no effect on the device by itself)
  flash    writes firmware to the device (backed up, verified, auto-rollback by default)
  notify   tells a human

Results are JSON-serialisable dicts. Every call is appended to var/logs/audit.jsonl.
"""
import json
import math
import re
import subprocess
import time
from pathlib import Path

from . import client, config, images, swd, workspace
from .client import PoddUnavailable

TOOLS = {}
MAX_TEXT = 24000


def tool(name, description, risk="read", params=None, required=()):
    def deco(fn):
        TOOLS[name] = {"fn": fn, "name": name, "description": description, "risk": risk,
                       "parameters": {"type": "object", "properties": params or {}, "required": list(required)}}
        return fn
    return deco


def _clip(text, n=MAX_TEXT):
    if text is None or len(text) <= n:
        return text
    return text[:n] + "\n... [truncated %d chars]" % (len(text) - n)


def _podd(op, **kw):
    r = client.call(op, **kw)
    if not r.get("ok", False) and op not in ("cmd",):
        raise RuntimeError(r.get("error", "podd error"))
    return r


def _note(ev, **fields):
    try:
        client.call("note", ev=ev, fields=fields, timeout=3)
    except Exception:
        pass


def _hex(v):
    return int(v, 16) if isinstance(v, str) else int(v)


def _tail_jsonl(path, max_bytes=4_000_000):
    p = Path(path)
    if not p.exists():
        return []
    size = p.stat().st_size
    with open(p, "rb") as f:
        f.seek(max(0, size - max_bytes))
        data = f.read().decode(errors="replace").splitlines()
    if size > max_bytes:
        data = data[1:]
    rows = []
    for line in data:
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


# =========================================================================== orientation

@tool("system_info", "Static description of the system: hardware, wiring, telemetry line format, firmware "
      "commands, self-diagnostics, how builds/flashing/rollback work, and the rules for changing code. "
      "Read this first.")
def system_info():
    return {"text": (config.HEALER / "SYSTEM.md").read_text()}


@tool("status", "One-call overview: serial link state, last boot event, latest health record, open incidents, "
      "what is deployed, and the source workspace state.")
def status():
    out = {}
    try:
        st = _podd("status")
        out |= {k: st[k] for k in ("link", "last_boot", "last_health", "health_age_s", "open_incidents")}
    except PoddUnavailable as e:
        out["podd"] = str(e)
    dep = images.last_deployment()
    out["last_deployment"] = dep
    if dep and images.load(dep["crc"]):
        out["deployed_image"] = images.public(images.load(dep["crc"]))
    try:
        out["workspace"] = workspace.status()
    except workspace.WorkspaceError as e:
        out["workspace"] = str(e)
    return out


@tool("triage", "Run all deterministic triage rules on the last few seconds of telemetry now. Returns findings "
      "with severity, evidence, hypotheses and suggested next tool calls. Start every investigation here.")
def triage():
    try:
        r = _podd("triage")
        return {"findings": r["findings"], "link": r["link"]}
    except PoddUnavailable as e:
        return {"findings": [], "podd": str(e), "hint": "podd is down; use chip_status for an SWD-only view"}


@tool("incidents", "Incidents recorded by podd's background triage (opened when a rule fires, resolved after it "
      "stays quiet for 10 s).",
      params={"include_resolved": {"type": "boolean", "default": False},
              "minutes": {"type": "number", "default": 60, "description": "how far back for resolved ones"}})
def incidents(include_resolved=False, minutes=60):
    out = {}
    try:
        out["open"] = _podd("incidents")["open"]
    except PoddUnavailable as e:
        out["podd"] = str(e)
    if include_resolved:
        since = time.time() - float(minutes) * 60
        out["history"] = [r for r in _tail_jsonl(config.LOGS / "incidents.jsonl") if r["ts"] >= since][-100:]
    return out


# =========================================================================== telemetry & logs

@tool("telemetry_stats", "Summary statistics of recent telemetry: accelerometer (rate, per-axis mean/std/min/max, "
      "|a| magnitude, stale count, MCU time gaps), distance (rate, status counts, mm stats), latest health record, "
      "latest decoded radio frame, recent events, link byte rate.",
      params={"seconds": {"type": "number", "default": 10}})
def telemetry_stats(seconds=10):
    r = _podd("stats", seconds=seconds)
    r.pop("ok", None)
    return r


@tool("telemetry_recent", "Recent records of one line type from podd's memory. kind: A (accel), D (distance), "
      "H (health), E (events, incl. host events), F (radio frames), R (command responses), '#' (comments), "
      "raw (every raw line as received, including garbage).",
      params={"kind": {"type": "string", "enum": ["A", "D", "H", "E", "F", "R", "#", "raw"]},
              "seconds": {"type": "number", "default": 10}, "limit": {"type": "integer", "default": 40}},
      required=("kind",))
def telemetry_recent(kind, seconds=10, limit=40):
    return {"rows": _podd("recent", kind=kind, seconds=seconds, limit=limit)["rows"]}


@tool("events", "Event history from the durable log (survives podd restarts): firmware E lines (boot, crash, "
      "watchdog_reset, safe_mode, acc_err, acc_cfg_lost, acc_init), host events (flash_start, baud_changed, "
      "incident_open, agent_note, ...). Filter by event names.",
      params={"minutes": {"type": "number", "default": 30},
              "kinds": {"type": "string", "description": "comma-separated ev names, e.g. 'boot,crash'"},
              "limit": {"type": "integer", "default": 60}})
def events(minutes=30, kinds=None, limit=60):
    since = time.time() - float(minutes) * 60
    want = {k.strip() for k in kinds.split(",")} if kinds else None
    rows = [r for r in _tail_jsonl(config.LOGS / "events.jsonl")
            if r.get("ts", 0) >= since and r.get("type") in ("E", "host") and (want is None or r.get("ev") in want)]
    return {"rows": rows[-int(limit):], "matched": len(rows)}


@tool("log_search", "Regex search over a log file, newest last. logs: events, health, telemetry, incidents, "
      "serial (raw lines as received, with host timestamps), audit (tool calls).",
      params={"pattern": {"type": "string"}, "log": {"type": "string", "default": "events",
              "enum": ["events", "health", "telemetry", "incidents", "serial", "audit"]},
              "minutes": {"type": "number", "default": 60}, "limit": {"type": "integer", "default": 50}},
      required=("pattern",))
def log_search(pattern, log="events", minutes=60, limit=50):
    rx = re.compile(pattern)
    path = config.LOGS / ("serial.log" if log == "serial" else log + ".jsonl")
    if not path.exists():
        return {"rows": [], "note": "no log %s yet" % path.name}
    size = path.stat().st_size
    with open(path, "rb") as f:
        f.seek(max(0, size - 8_000_000))
        lines = f.read().decode(errors="replace").splitlines()
    since = time.time() - float(minutes) * 60
    hits = []
    for line in lines:
        if not rx.search(line):
            continue
        if log != "serial":
            try:
                if json.loads(line).get("ts", 0) < since:
                    continue
            except ValueError:
                continue
        hits.append(line)
    return {"rows": hits[-int(limit):], "matched": len(hits)}


# =========================================================================== serial link

@tool("link_baud_scan", "Listen to the serial port at several baud rates (~1.2 s each) and report which one "
      "yields valid telemetry. Use when the link is garbled: a clean result at another baud means the MCU's "
      "clock assumption is off by that ratio.")
def link_baud_scan():
    return _podd("baud_scan", timeout=30)


@tool("link_raw_capture", "Capture raw bytes from the serial port for a few seconds, optionally at another baud.",
      params={"seconds": {"type": "number", "default": 2}, "baud": {"type": "integer"}})
def link_raw_capture(seconds=2, baud=None):
    return _podd("raw", seconds=seconds, baud=baud, timeout=20)


@tool("link_set_baud", "Make podd listen at a different baud rate (e.g. to read a firmware whose UART runs at "
      "the wrong speed). Reset to 115200 after. fw_flash resets it automatically.", risk="runtime",
      params={"baud": {"type": "integer"}}, required=("baud",))
def link_set_baud(baud):
    return _podd("set_baud", baud=int(baud))


# =========================================================================== firmware commands

@tool("pod_cmd", "Send one command line to the pod firmware and return its R response lines. Read-only "
      "commands: help, info, health, stats, crash, acc regs, i2c scan, i2c r <addr> <reg> [n], i2c lines, "
      "us check. State-changing (reversible): acc init, acc on|off, i2c recover, i2c w <addr> <reg> <val>, "
      "stream a|d|f on|off, rate acc|us <hz>, safe on|off, crash clear, reset. Settings made here are lost on "
      "reset. Fault injection ('inject ...') is reserved for the operator.", risk="runtime",
      params={"command": {"type": "string"}, "timeout": {"type": "number", "default": 2.0}}, required=("command",))
def pod_cmd(command, timeout=2.0):
    if command.strip().split()[:1] == ["inject"]:
        return {"ok": False, "error": "fault injection is operator-only (demo/scenario)"}
    return client.call("cmd", line=command, timeout=float(timeout) + 5)


# =========================================================================== SWD (works even if firmware is hung)

def _chip_image():
    bid = swd.chip_build_id()
    if bid is None:
        return None, None
    crc = images.crc_of(bid)
    return bid, images.load(crc)


@tool("chip_status", "Over SWD (independent of the firmware and its UART): which build is in flash, whether it is "
      "in the image registry, whether the CPU is running (uptime counter advancing) and how fast the firmware's "
      "millisecond counter runs relative to real time.")
def chip_status():
    bid, meta = _chip_image()
    out = {"build_id": bid, "crc": images.crc_of(bid) if bid else None, "in_registry": bool(meta)}
    if bid is None:
        return out | {"note": "no valid build ID in flash (erased, or firmware without fw_info)"}
    if meta:
        out["image"] = images.public(meta)
        syms = images.symbols(meta["elf"])
        if "g_uptime_ms" in syms:
            t0, t1, dt = swd.uptime_rate(syms["g_uptime_ms"][0], 1.0)
            out |= {"uptime_ms": t1, "running": t1 > t0, "uptime_advance_ms_per_s": t1 - t0,
                    "uptime_ratio_to_real_time": round((t1 - t0) / 1000.0 / dt, 3)}
            if t1 < t0:
                out["note"] = "uptime went backwards: the chip reset during the measurement"
    dep = images.last_deployment()
    out["matches_last_deployment"] = bool(dep and bid and dep["crc"] == images.crc_of(bid))
    return out


@tool("chip_clock", "Over SWD: decode the real clock tree from the RCC registers and compare it with what the "
      "firmware believes (SystemCoreClock). Derives the actual UART baud, SysTick period and TIM3 tick.")
def chip_clock():
    bid, meta = _chip_image()
    spec = {"CR": (0x40021000, 1), "CFGR": (0x40021004, 1), "CFGR2": (0x4002102C, 1), "CFGR3": (0x40021030, 1),
            "BRR": (0x4000440C, 1), "SYST_LOAD": (0xE000E014, 1), "TIM3_PSC": (0x40000428, 1)}
    syms = images.symbols(meta["elf"]) if meta else {}
    if "SystemCoreClock" in syms:
        spec["SystemCoreClock"] = (syms["SystemCoreClock"][0], 1)
    r = {k: v[0] for k, v in swd.read_words(spec).items()}
    real = swd.decode_clock(r)
    out = {"registers": {k: "0x%08X" % v for k, v in r.items()}, "real_clock": real}
    fw = r.get("SystemCoreClock")
    pclk, hclk = real["pclk_hz"], real["hclk_hz"]
    if fw:
        out["firmware_SystemCoreClock_hz"] = fw
        out["firmware_vs_real_ratio"] = round(fw / real["sysclk_hz"], 3) if real["sysclk_hz"] else None
    if pclk and r["BRR"]:
        out["uart_actual_baud"] = round(pclk / r["BRR"])
        out["uart_intended_baud"] = config.BAUD
        if fw:
            out["uart_baud_firmware_thinks"] = round(fw / r["BRR"])
    if hclk:
        out["systick_period_ms_actual"] = round((r["SYST_LOAD"] + 1) / hclk * 1000, 3)
    if pclk:
        out["tim3_tick_us_actual"] = round((r["TIM3_PSC"] + 1) / pclk * 1e6, 3)
    if fw and real["sysclk_hz"] and fw != real["sysclk_hz"]:
        out["finding"] = ("MISMATCH: firmware believes %.0f MHz but the RCC is configured for %.0f MHz (%s x%d). "
                          "Every rate derived from SystemCoreClock is off by %.2fx." %
                          (fw / 1e6, real["sysclk_hz"] / 1e6, real["pll_source"], real["pll_mul"], real["sysclk_hz"] / fw))
    return out


@tool("chip_halt_sample", "Over SWD: briefly halt the CPU several times and record where it is (pc/lr/sp), "
      "symbolized. Shows where a hung or slow firmware spends its time. The CPU resumes after each sample.",
      params={"samples": {"type": "integer", "default": 5}})
def chip_halt_sample(samples=5):
    bid, meta = _chip_image()
    raw = swd.halt_samples(int(samples))
    out = []
    for s in raw:
        row = dict(s)
        if meta and "pc" in s:
            row["pc_at"] = _a2l(meta["elf"], _hex(s["pc"]))
            row["lr_at"] = _a2l(meta["elf"], _hex(s["lr"]) & ~1)
        out.append(row)
    return {"build_id": bid, "samples": out}


@tool("chip_read", "Over SWD: read memory by address or by global variable name (resolved with the ELF of the "
      "build in flash), without halting. E.g. symbol='g_app', 'g_i2c_stats', 'g_uart_stats', 'g_diag', "
      "'SystemCoreClock'.",
      params={"symbol": {"type": "string"}, "address": {"type": "string", "description": "hex"},
              "words": {"type": "integer", "default": 1}})
def chip_read(symbol=None, address=None, words=1):
    if symbol:
        bid, meta = _chip_image()
        if not meta:
            raise RuntimeError("the build in flash isn't in the registry, so symbols can't be resolved")
        syms = images.symbols(meta["elf"])
        if symbol not in syms:
            raise KeyError("no symbol %r in %s" % (symbol, meta["build_id"]))
        addr, size = syms[symbol]
        words = max(1, math.ceil(size / 4)) if size else int(words)
    elif address:
        addr = _hex(address)
    else:
        raise ValueError("give symbol or address")
    vals = swd.read_words({"m": (addr, min(int(words), 64))})["m"]
    return {"address": "0x%08X" % addr, "words": ["0x%08X" % v for v in vals],
            "bytes_le": "".join(v.to_bytes(4, "little").hex() for v in vals)}


@tool("chip_regs", "Over SWD: dump a peripheral's main registers (hex). peripheral: " + ", ".join(swd.PERIPHERALS),
      params={"peripheral": {"type": "string", "enum": list(swd.PERIPHERALS)}}, required=("peripheral",))
def chip_regs(peripheral):
    base, regs = swd.PERIPHERALS[peripheral.upper()]
    span = max(o for o, _ in regs) // 4 + 1
    vals = swd.read_words({"p": (base, span)})["p"]
    return {"peripheral": peripheral.upper(), "base": "0x%08X" % base,
            "registers": {name: "0x%08X" % vals[o // 4] for o, name in regs}}


@tool("chip_reset", "Over SWD: reset the MCU (works even if the firmware is hung).", risk="runtime")
def chip_reset():
    swd.reset()
    _note("chip_reset")
    return {"ok": True}


# =========================================================================== symbols

_SRC_PATH = re.compile(r"[^\s:]*/(src/[^\s:]+)")


def _short_paths(text):
    """Build-machine paths (/tmp/xyz/pod/src/frame.c) -> workspace-relative (src/frame.c)."""
    return _SRC_PATH.sub(r"\1", text)


def _a2l(elf, addr):
    out = subprocess.run([config.gcc_tool("addr2line"), "-f", "-i", "-C", "-e", str(elf), "0x%x" % addr],
                         capture_output=True, text=True).stdout.strip().splitlines()
    frames = [{"function": out[i], "location": _short_paths(out[i + 1]) if i + 1 < len(out) else "?"}
              for i in range(0, len(out), 2)]
    return frames


@tool("fw_symbolize", "Map a code address (crash pc/lr, halt sample) to function, source file:line (including "
      "inlined frames), the surrounding source lines from that build's own source snapshot, and disassembly. "
      "For lr pass it as-is; the Thumb bit is handled and the call site is the instruction before it.",
      params={"address": {"type": "string", "description": "hex, e.g. 0x08002B9A"},
              "image": {"type": "string", "default": "chip", "description": "build crc (crash build_crc), "
                        "'chip' (what's in flash), 'deployed', 'known-good'"},
              "is_return_address": {"type": "boolean", "default": False}},
      required=("address",))
def fw_symbolize(address, image="chip", is_return_address=False):
    if image == "chip":
        bid, meta = _chip_image()
        if not meta:
            raise RuntimeError("build in flash isn't registered; pass image=<crc>")
    else:
        meta = images.resolve(image)
    addr = _hex(address) & ~1
    if is_return_address:
        addr -= 2
    frames = _a2l(meta["elf"], addr)
    ctx = None
    if frames:
        m = re.match(r"(.*):(\d+)", frames[0]["location"])
        if m:
            src = Path(meta["elf"]).parent / "src" / Path(m.group(1)).name
            p = src if src.exists() else workspace.ws() / m.group(1)
            if p.exists():
                lines = p.read_text(errors="replace").splitlines()
                n = int(m.group(2))
                ctx = "\n".join("%s%5d  %s" % ("->" if i == n else "  ", i, lines[i - 1])
                                for i in range(max(1, n - 6), min(len(lines), n + 4) + 1))
    dis = subprocess.run([config.gcc_tool("objdump"), "-d", "-l", "--no-show-raw-insn",
                          "--start-address=0x%x" % max(0, addr - 16), "--stop-address=0x%x" % (addr + 10), meta["elf"]],
                         capture_output=True, text=True).stdout
    dis = _short_paths("\n".join(l for l in dis.splitlines()
                                  if l.strip() and not l.startswith(("Disassembly", meta["elf"])) and "file format" not in l))
    return {"image": meta["crc"], "build_id": meta["build_id"], "address": "0x%08X" % addr, "frames": frames,
            "source_context": ctx, "disassembly": dis}


# =========================================================================== image registry

@tool("fw_images", "List firmware images in the registry (crc, build ID, version, status, origin) and recent "
      "deployments.")
def fw_images():
    return {"images": [images.public(m) for m in images.all_images()], "deployments": images.deployments(10)}


@tool("fw_mark", "Set an image's status: candidate, known-good or bad (with a note).", risk="runtime",
      params={"image": {"type": "string"}, "status": {"type": "string", "enum": ["candidate", "known-good", "bad"]},
              "note": {"type": "string"}}, required=("image", "status"))
def fw_mark(image, status, note=None):
    return images.public(images.set_status(image, status, note))


# =========================================================================== source workspace

@tool("src_status", "Workspace state: current revision (git describe), branch, uncommitted changes.")
def src_status():
    return workspace.status()


@tool("src_list", "List files in the firmware source workspace with line counts.",
      params={"path": {"type": "string", "default": "."}})
def src_list(path="."):
    return {"files": workspace.list_files(path)}


@tool("src_read", "Read a source file with line numbers (optionally a line range).",
      params={"path": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}},
      required=("path",))
def src_read(path, start=None, end=None):
    r = workspace.read(path, start, end)
    r["text"] = _clip(r["text"])
    return r


@tool("src_search", "Search the source with an extended regex (git grep). Returns path, line, text.",
      params={"pattern": {"type": "string"}, "path": {"type": "string"}, "ignore_case": {"type": "boolean", "default": False}},
      required=("pattern",))
def src_search(pattern, path=None, ignore_case=False):
    return {"hits": workspace.search(pattern, path, ignore_case)}


@tool("src_log", "Commit history of the firmware source (tags mark releases; v1.0 is the known-good baseline).",
      params={"n": {"type": "integer", "default": 15}})
def src_log(n=15):
    return {"log": workspace.log(n)}


@tool("src_show", "Show one commit (message + patch, or just the file stats).",
      params={"rev": {"type": "string"}, "stat_only": {"type": "boolean", "default": False}}, required=("rev",))
def src_show(rev, stat_only=False):
    return {"text": _clip(workspace.show(rev, stat_only))}


@tool("src_diff", "git diff. No revs: all uncommitted changes (vs HEAD). One rev: that rev vs working tree. Two: between them. "
      "E.g. rev_a='v1.0' rev_b='HEAD' shows everything changed since the known-good release.",
      params={"rev_a": {"type": "string"}, "rev_b": {"type": "string"}, "path": {"type": "string"},
              "stat": {"type": "boolean", "default": False}})
def src_diff(rev_a=None, rev_b=None, path=None, stat=False):
    return {"diff": _clip(workspace.diff(rev_a, rev_b, path, stat))}


@tool("src_edit", "Replace an exact text snippet in a source file (must match exactly once unless replace_all). "
      "Returns the resulting diff. Code changes are a last resort: see the rules in system_info.", risk="code",
      params={"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"},
              "replace_all": {"type": "boolean", "default": False}}, required=("path", "old", "new"))
def src_edit(path, old, new, replace_all=False):
    r = workspace.edit(path, old, new, replace_all)
    r["diff"] = _clip(r["diff"])
    return r


@tool("src_write", "Create or overwrite a whole file in the workspace.", risk="code",
      params={"path": {"type": "string"}, "content": {"type": "string"}}, required=("path", "content"))
def src_write(path, content):
    return workspace.write(path, content)


@tool("src_revert", "Discard uncommitted changes (one file, or everything).", risk="code",
      params={"path": {"type": "string"}})
def src_revert(path=None):
    return workspace.revert(path)


@tool("src_restore", "Make the working tree match an older revision (e.g. 'v1.0'); commit afterwards to keep it.",
      risk="code", params={"rev": {"type": "string"}}, required=("rev",))
def src_restore(rev):
    return workspace.restore(rev)


@tool("src_commit", "Commit all workspace changes with a message (explain the fault and the fix).", risk="code",
      params={"message": {"type": "string"}}, required=("message",))
def src_commit(message):
    return workspace.commit(message)


# =========================================================================== build & flash

@tool("fw_build", "Compile the workspace firmware. On success the image is archived in the registry as a "
      "candidate (with its source diff) and its crc is returned for fw_flash. Returns warnings/errors and "
      "flash/RAM use. Does not touch the device.", risk="code",
      params={"clean": {"type": "boolean", "default": False}})
def fw_build(clean=False):
    return workspace.build(clean)


def _boot_events_since(t0):
    rows = client.call("recent", kind="E", seconds=time.time() - t0 + 2, limit=500)["rows"]
    return [r for r in rows if r["ts"] >= t0 and r.get("type") == "E"]


def _verify(meta, t_flash, watch_seconds):
    checks = []
    ok = True
    try:
        bid = swd.chip_build_id()
    except swd.SwdError as e:
        bid = None
        checks.append({"check": "swd_read", "ok": False, "detail": str(e)})
    match = bid == meta["build_id"]
    checks.append({"check": "build_id_in_flash", "ok": match, "detail": bid})
    ok &= match
    try:
        client.call("ping", timeout=2)
    except PoddUnavailable:
        checks.append({"check": "telemetry", "ok": None, "detail": "podd not running: health not verified"})
        return ok, checks, "unverified"

    want = "0X" + meta["crc"]
    boot_ts = None
    deadline = t_flash + 8
    while time.time() < deadline and boot_ts is None:
        for e in _boot_events_since(t_flash):
            if e.get("ev") == "boot" and str(e.get("crc", "")).upper() == want:
                boot_ts = e["ts"]
                break
        if boot_ts is None:
            time.sleep(0.5)
    checks.append({"check": "boot_event", "ok": boot_ts is not None,
                   "detail": "boot line with crc %s seen" % meta["crc"] if boot_ts else
                   "no boot line from this build within 8 s (UART garbled or firmware not starting?)"})
    ok &= boot_ts is not None
    if boot_ts is None:
        return ok, checks, "failed"

    end = boot_ts + float(watch_seconds)
    while time.time() < end:
        evs = _boot_events_since(t_flash)
        if sum(1 for e in evs if e.get("ev") == "boot") > 1:
            break
        time.sleep(1.0)
    evs = _boot_events_since(t_flash)
    boots = [e for e in evs if e.get("ev") == "boot"]
    crashes = [e for e in evs if e.get("ev") == "crash"]
    wdg = [e for e in evs if e.get("ev") == "watchdog_reset"]
    H = [h for h in client.call("recent", kind="H", seconds=time.time() - boot_ts + 1, limit=500)["rows"] if h["ts"] >= boot_ts]
    span = max(time.time() - boot_ts, 1)
    checks.append({"check": "no_reboots", "ok": len(boots) == 1 and not crashes and not wdg,
                   "detail": {"boots": len(boots), "crashes": len(crashes), "watchdog_resets": len(wdg)}})
    checks.append({"check": "health_flowing", "ok": len(H) >= 0.6 * min(span, watch_seconds),
                   "detail": "%d health records in %.0f s" % (len(H), span)})
    checks.append({"check": "normal_mode", "ok": bool(H) and H[-1].get("mode") == "normal",
                   "detail": H[-1].get("mode") if H else None})
    ok &= all(c["ok"] for c in checks[-3:])
    return ok, checks, "ok" if ok else "failed"


@tool("fw_flash", "Write an image to the device and verify it: backs up the current flash, programs and "
      "verifies the image, checks the build ID over SWD, waits for its boot line, then watches telemetry for "
      "watch_seconds (no reboots/crashes, health flowing, normal mode). On failure it marks the image bad and, "
      "if auto_rollback, re-flashes what was running before (or the known-good image). Also returns a triage "
      "run at the end, so functional problems that don't crash are visible.", risk="flash",
      params={"image": {"type": "string", "description": "crc from fw_build/fw_images, 'known-good', 'latest'"},
              "watch_seconds": {"type": "number", "default": 15}, "auto_rollback": {"type": "boolean", "default": True},
              "reason": {"type": "string", "description": "why this flash (logged)"}},
      required=("image",))
def fw_flash(image, watch_seconds=15, auto_rollback=True, reason=None, _allow_bad=False):
    meta = images.resolve(image)
    if meta["status"] == "bad" and not _allow_bad:
        return {"ok": False, "error": "image %s is marked bad; mark it candidate first if you really mean it" % meta["crc"]}
    prev_bid = None
    try:
        prev_bid = swd.chip_build_id()
    except swd.SwdError:
        pass
    prev = images.load(images.crc_of(prev_bid)) if prev_bid else None
    bk = config.BACKUPS / ("%s-%s.bin" % (time.strftime("%Y%m%d-%H%M%S"), images.crc_of(prev_bid) if prev_bid else "unknown"))
    swd.backup(str(bk))
    try:
        if client.call("status", timeout=3)["link"]["baud"] != config.BAUD:
            client.call("set_baud", baud=config.BAUD)
    except PoddUnavailable:
        pass
    _note("flash_start", crc=meta["crc"], version=meta["version"], previous=images.crc_of(prev_bid) if prev_bid else None,
          reason=reason)
    t0 = time.time()
    swd.flash_elf(meta["elf"])
    ok, checks, result = _verify(meta, t0, watch_seconds)
    images.record_deployment(meta["crc"], result, build_id=meta["build_id"], reason=reason, backup=str(bk), checks=checks)
    _note("flash_done", crc=meta["crc"], result=result)
    out = {"ok": ok, "result": result, "image": images.public(meta), "checks": checks, "backup": str(bk)}
    if not ok and result == "failed":
        images.set_status(meta["crc"], "bad", "failed post-flash verification")
        if auto_rollback:
            target = prev if prev and prev["status"] != "bad" and prev["crc"] != meta["crc"] else None
            if target is None:
                try:
                    target = images.resolve("known-good")
                except KeyError:
                    target = None
            if target:
                out["rollback"] = fw_flash(target["crc"], watch_seconds=10, auto_rollback=False,
                                           reason="auto-rollback after %s failed verification" % meta["crc"])
            else:
                bk_ok = swd.flash_bin(str(bk))
                out["rollback"] = {"ok": bk_ok, "result": "restored raw backup %s" % bk}
    if ok:
        time.sleep(2)
        out["triage_after"] = triage()["findings"]
    return out


@tool("fw_rollback", "Flash the known-good image (or another image) back, with the same verification as fw_flash.",
      risk="flash", params={"to": {"type": "string", "default": "known-good"}, "reason": {"type": "string"}})
def fw_rollback(to="known-good", reason=None):
    return fw_flash(to, watch_seconds=15, auto_rollback=False, reason=reason or "rollback")


# =========================================================================== humans & notes

@tool("notify_human", "Ask a person to do something only a person can (reseat a wire, power-cycle, replace a "
      "part) or report an incident. Recorded in var/notifications.jsonl and the event log.", risk="notify",
      params={"summary": {"type": "string"}, "actions": {"type": "string", "description": "specific instructions"},
              "severity": {"type": "string", "enum": ["info", "warn", "critical"], "default": "warn"}},
      required=("summary",))
def notify_human(summary, actions=None, severity="warn"):
    config.ensure_dirs()
    rec = {"ts": time.time(), "severity": severity, "summary": summary, "actions": actions}
    with open(config.VAR / "notifications.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
    _note("notify_human", severity=severity, summary=summary, actions=actions)
    return {"ok": True, "recorded": rec}


@tool("note", "Record a hypothesis, decision or conclusion in the event log (for the incident report).",
      params={"text": {"type": "string"}}, required=("text",))
def note(text):
    _note("agent_note", text=text)
    return {"ok": True}


# =========================================================================== dispatch

def manifest():
    return [{"name": t["name"], "description": t["description"], "risk": t["risk"],
             "input_schema": t["parameters"]} for t in TOOLS.values()]


def call(name, args=None):
    if name not in TOOLS:
        raise KeyError("unknown tool %r" % name)
    args = args or {}
    t0 = time.time()
    rec = {"ts": t0, "tool": name, "args": args}
    try:
        result = TOOLS[name]["fn"](**args)
        rec |= {"ok": True}
        return result
    except Exception as e:
        rec |= {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        return {"ok": False, "error": rec["error"]}
    finally:
        rec["duration_s"] = round(time.time() - t0, 2)
        config.ensure_dirs()
        with open(config.LOGS / "audit.jsonl", "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
