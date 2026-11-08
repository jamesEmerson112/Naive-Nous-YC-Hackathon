"""A stand-in for the pod healer, for tests: ours, and the cloud's interop test.

`PoddVar` writes a healer `var/` folder the way Olir's code writes it, and `FakeSocket`
answers like podd's request socket. Each record shape is copied from `firmware/healer`:
podd.py (`triage_loop`, `event`, `_route`, `status`, `stats`), tools.py (`call`,
`notify_human`, `note`) and images.py (`record_deployment`, `register`). `tools.json` is
the real one from the repo when it's there, so actions are classified by the healer's own
risk levels. Like `fake_device.fake_cloud`, it lives in the package so other tests can
use it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from edge_bridge.podd import PoddPaths

REPO_TOOLS = Path(__file__).resolve().parents[4] / "firmware" / "healer" / "tools.json"
MINIMAL_TOOLS = {
    "triage": "read",
    "fw_symbolize": "read",
    "chip_halt_sample": "read",
    "note": "read",
    "pod_cmd": "runtime",
    "src_edit": "code",
    "fw_build": "code",
    "fw_flash": "flash",
    "fw_rollback": "flash",
    "notify_human": "notify",
}
"""Used when the repo's tools.json isn't there (the package installed on its own)."""
T0 = 1_794_105_600.0
"""podd's `ts` is host epoch seconds; this is Sat Nov 7 2026, ~5:20 PM PST."""

GOOD_CRC = "1A2B3C4D"
BAD_CRC = "32ACFDAF"
FIXED_CRC = "5E6F7081"
HARDENED_CRC = "9A0B1C2D"


class PoddVar:
    """Writes `healer/var/` like podd and the heal tools do."""

    def __init__(self, root: Path) -> None:
        self.healer = root / "healer"
        self.paths = PoddPaths.at(self.healer, self.healer / "var")
        self.paths.logs.mkdir(parents=True)
        if REPO_TOOLS.exists():
            shutil.copy(REPO_TOOLS, self.healer / "tools.json")
        else:
            tools = [{"name": n, "risk": r} for n, r in MINIMAL_TOOLS.items()]
            (self.healer / "tools.json").write_text(json.dumps(tools))
        self.opened: dict[str, tuple[str, float]] = {}

    def _append(self, path: Path, rec: dict[str, Any]) -> None:
        with open(path, "a") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def event(self, ts: float, ev: str, **fields: Any) -> None:
        self._append(self.paths.log("events"), {"ts": ts, "type": "host", "ev": ev} | fields)

    # podd triage_loop
    def open(self, kind: str, ts: float, severity: str = "critical", summary: str = "") -> str:
        incident_id = f"{kind}-{int(ts)}"
        inc = {
            "id": incident_id,
            "kind": kind,
            "opened": ts,
            "last_seen": ts,
            "severity": severity,
            "summary": summary or f"{kind} detected",
            "first_evidence": {"window_s": 3},
        }
        self._append(self.paths.log("incidents"), {"ts": ts, "status": "open"} | inc)
        self.event(
            ts,
            "incident_open",
            id=incident_id,
            kind=kind,
            severity=severity,
            summary=inc["summary"],
        )
        self.opened[incident_id] = (kind, ts)
        return incident_id

    def resolve(self, incident_id: str, ts: float) -> None:
        kind, opened = self.opened[incident_id]
        rec = {
            "ts": ts,
            "status": "resolved",
            "id": incident_id,
            "kind": kind,
            "duration_s": round(ts - opened, 1),
        }
        self._append(self.paths.log("incidents"), rec)
        self.event(ts, "incident_resolved", id=incident_id, kind=kind)

    # tools.call
    def call(
        self,
        tool: str,
        ts: float,
        args: dict | None = None,
        ok: bool = True,
        error: str | None = None,
        duration: float = 0.2,
    ) -> None:
        rec: dict[str, Any] = {"ts": ts, "tool": tool, "args": args or {}}
        rec |= {"ok": True} if ok else {"ok": False, "error": error or "RuntimeError: failed"}
        rec["duration_s"] = duration
        self._append(self.paths.log("audit"), rec)

    def note(self, text: str, ts: float) -> None:
        self.event(ts, "agent_note", text=text)
        self.call("note", ts, {"text": text}, duration=0.01)

    def pod_cmd(self, command: str, ts: float, ok: bool = True) -> None:
        """tools.pod_cmd(command): podd sends `@<id> <command>`, the pod answers with `R`
        lines ending `R id=<id> end=1 ok=1` (or `ok=0 err="…"`), which podd logs as events."""
        end = {"type": "R", "id": 1001, "end": 1, "ok": 1 if ok else 0, "ts": ts + 0.1}
        if not ok:
            end["err"] = "unknown command"
        self._append(self.paths.log("events"), end)
        self.call("pod_cmd", ts, {"command": command}, duration=0.2)

    def notify(self, summary: str, ts: float, actions: str | None = None) -> None:
        rec = {"ts": ts, "severity": "warn", "summary": summary, "actions": actions}
        self._append(self.paths.notifications, rec)
        self.event(ts, "notify_human", severity="warn", summary=summary, actions=actions)
        self.call("notify_human", ts, {"summary": summary, "actions": actions}, duration=0.01)

    # images.register / record_deployment, and fw_flash around it
    def image(
        self,
        crc: str,
        version: str,
        origin: str,
        status: str = "candidate",
        patch: str | None = None,
    ) -> None:
        folder = self.paths.images / crc
        folder.mkdir(parents=True, exist_ok=True)
        meta = {
            "crc": crc,
            "build_id": f"pod {version} 2026-11-08T01:00:00Z",
            "version": version,
            "status": status,
            "origin": origin,
            "created": T0,
            "source_rev": version,
            "notes": [],
            "elf": str(folder / "pod.elf"),
            "bin": str(folder / "pod.bin"),
        }
        (folder / "meta.json").write_text(json.dumps(meta, indent=2))
        if patch is not None:
            (folder / "source.patch").write_text(patch)

    def flash(
        self, crc: str, version: str, ts: float, result: str = "ok", duration: float = 25.0
    ) -> None:
        build_id = f"pod {version} 2026-11-08T01:00:00Z"
        self.event(ts, "flash_start", crc=crc, version=version, previous=None, reason="fix")
        self.boot(ts + 3, version, crc)
        dep = {
            "ts": ts + duration,
            "crc": crc,
            "result": result,
            "build_id": build_id,
            "reason": "fix",
            "backup": f"/var/backups/{crc}.bin",
            "checks": [{"check": "build_id_in_flash", "ok": True, "detail": build_id}],
        }
        self._append(self.paths.deployments, dep)
        self.event(ts + duration, "flash_done", crc=crc, result=result)
        self.call("fw_flash", ts, {"image": crc, "reason": "fix"}, duration=duration)

    # podd _route: a pod boot line (E ev=boot build="…" crc=0x…)
    def boot(self, ts: float, version: str, crc: str) -> None:
        rec = {
            "type": "E",
            "t": 120,
            "ev": "boot",
            "build": f"pod {version} 2026-11-08T01:00:00Z",
            "crc": f"0x{crc}",
            "rst": "por",
            "ts": ts,
        }
        self._append(self.paths.log("events"), rec)

    def telemetry(self, t0: float, t1: float, mm: int = 480) -> None:
        """D at 10 Hz, A at 50 Hz, H at 1 Hz, as podd logs them."""
        t = t0
        while t < t1:
            ms = int((t - T0) * 1000)
            if round((t - t0) * 50) % 5 == 0:
                rec = {"type": "D", "t": ms, "mm": mm, "us": mm * 6, "st": "ok", "ts": t}
                self._append(self.paths.log("telemetry"), rec)
            rec = {"type": "A", "t": ms, "x": 12, "y": -8, "z": 1002, "st": "ok", "ts": t}
            self._append(self.paths.log("telemetry"), rec)
            if round((t - t0) * 50) % 50 == 0:
                h = {
                    "type": "H",
                    "t": ms,
                    "mode": "run",
                    "acc_hz": 50,
                    "loop_us": 850,
                    "stk_free": 1200,
                    "vdda": 3300,
                    "temp": 31.5,
                    "crashes": 0,
                    "ts": t,
                }
                self._append(self.paths.log("health"), h)
            t = round(t + 0.02, 3)


class FakeSocket:
    """podd's `status` and `stats` answers (podd.py), settable; `up = False` is podd down."""

    def __init__(self) -> None:
        self.up = True
        self.link_state = "up"
        self.open_incidents: list[dict[str, Any]] = []
        self.last_boot: dict[str, Any] | None = None
        self.calls: list[str] = []

    def call(self, op: str, **fields: Any) -> dict[str, Any] | None:
        self.calls.append(op)
        if not self.up:
            return None
        link = {
            "state": self.link_state,
            "port": "/dev/ttyACM0",
            "baud": 115200,
            "error": None,
            "bytes_per_s": 2400,
            "valid_ratio": 1.0,
            "silent_s": 0.1,
        }
        if op == "status":
            health = {
                "type": "H",
                "t": 9000,
                "mode": "run",
                "acc_hz": 50,
                "loop_us": 850,
                "stk_free": 1200,
                "vdda": 3300,
                "temp": 31.5,
                "ts": T0,
            }
            return {
                "ok": True,
                "link": link,
                "last_boot": self.last_boot,
                "last_health": health,
                "open_incidents": self.open_incidents,
                "uptime_s": 100,
            }
        if op == "stats":
            return {
                "ok": True,
                "window_s": 10,
                "link": link,
                "accel": {"n": 500, "rate_hz": 50.0, "magnitude_mg": {"mean": 1002.1}},
                "distance": {"n": 100, "rate_hz": 10.0, "mm_ok": {"mean": 480.0}},
            }
        return {"ok": True}


# -- demo faults as the healer would leave them (firmware/docs/DEMO.md) ----------------


def scenario_01_fixed(pod: PoddVar, t: float) -> tuple[str, str, str]:
    """01: the v1.1 image crash-loops; the agent fixes frame_pack, flashes, it resolves.
    Returns podd's incident ids (crash, reboot_loop, safe_mode)."""
    pod.image(BAD_CRC, "v1.1", "release")
    pod.boot(t - 50, "v1.1", BAD_CRC)
    pod.telemetry(t - 40, t)
    crash = pod.open("crash", t, summary="HardFault at pc 0x08002B9A (frame_pack)")
    loop = pod.open("reboot_loop", t + 2, summary="3 abnormal boots in 30 s")
    safe = pod.open("safe_mode", t + 4, summary="firmware entered safe mode")
    pod.call("triage", t + 15)
    pod.call("fw_symbolize", t + 18, {"pc": "0x08002B9A"})
    pod.note("Unaligned halfword store in frame_pack faults on Cortex-M0.", t + 25)
    pod.call("src_edit", t + 30, {"path": "src/frame.c", "old": "*(uint16_t*)p", "new": "memcpy"})
    pod.call("fw_build", t + 40, duration=6)
    pod.image(FIXED_CRC, "v1.1-1-gabc1234-dirty", "workspace", patch="--- a/src/frame.c\n")
    pod.flash(FIXED_CRC, "v1.1-1-gabc1234-dirty", t + 50)
    pod.telemetry(t + 55, t + 100)
    for incident_id, at in ((crash, t + 88), (loop, t + 88), (safe, t + 89)):
        pod.resolve(incident_id, at)
    pod.note("Fixed: byte-wise packing; frames decode.", t + 92)
    return crash, loop, safe


def scenario_06_handed_off(pod: PoddVar, t: float) -> None:
    """06: the LIS3DH is gone; the agent restores I2C timeouts, flashes, then asks a
    person to reseat the sensor. acc_error stays open until someone does."""
    pod.boot(t - 50, "v1.0", GOOD_CRC)
    pod.telemetry(t - 40, t)
    pod.open("acc_error", t, severity="warn", summary="LIS3DH not responding (acc_errs rising)")
    pod.call("chip_halt_sample", t + 10)
    pod.call("src_edit", t + 20, {"path": "src/i2c.c", "old": "while", "new": "timeout"})
    pod.call("fw_build", t + 30, duration=6)
    pod.image(HARDENED_CRC, "v1.0-1-gdef5678-dirty", "workspace", patch="--- a/src/i2c.c\n")
    pod.flash(HARDENED_CRC, "v1.0-1-gdef5678-dirty", t + 40)
    pod.notify(
        "LIS3DH accelerometer not responding on I2C",
        t + 70,
        actions="Reseat the LIS3DH SDA/SCL wires, or replace the sensor.",
    )


# -- the heal agent's run folder (firmware/harness/runs/<run>/), as agent.py writes it -------


class AgentRunDir:
    """One harness run folder: `transcript.jsonl` (and `score.json`), written record by
    record in the shapes `firmware/harness/agent.py` writes, so a test can stop mid-run."""

    def __init__(self, runs_dir: Path, run_id: str) -> None:
        self.dir = Path(runs_dir) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "transcript.jsonl"
        self.turn = 0

    def write(self, type_: str, t: float, **rec: Any) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps({"t": t, "type": type_} | rec) + "\n")

    def start(self, t: float, podd_incidents: list[dict[str, Any]] | None, chip: dict[str, Any]):
        self.write(
            "run_start",
            t,
            label="watch",
            driver="ollama",
            model="qwen3.5:4b",
            target=None,
            tools=["triage", "fw_flash"],
            kickoff="podd opened these incidents:\n...",
            podd_incidents=podd_incidents,
            chip_at_start=chip,
            arm=None,
        )

    def call(
        self,
        t: float,
        name: str,
        args: dict[str, Any],
        result: Any,
        *,
        thinking: str = "",
        risk: str = "read",
        gate: str = "allow",
        reason: str = "",
        is_error: bool = False,
    ):
        self.write(
            "model_turn",
            t - 1,
            turn=self.turn,
            stop_reason="tool_use",
            text="",
            thinking=thinking,
            calls=[{"id": f"c{self.turn}", "name": name, "input": args}],
            usage={"input_tokens": 1000},
        )
        text = result if isinstance(result, str) else json.dumps(result)
        self.write(
            "tool_call",
            t,
            turn=self.turn,
            id=f"c{self.turn}",
            name=name,
            input=args,
            risk=risk,
            gate=gate,
            reason=reason or risk,
            result=text,
            is_error=is_error,
            seconds=1.0,
        )
        self.turn += 1

    def end(self, t: float, text: str, check: dict[str, Any], stop: str = "end_turn") -> None:
        self.write(
            "model_turn",
            t - 2,
            turn=self.turn,
            stop_reason="end_turn",
            text=text,
            thinking="",
            calls=[],
        )
        self.write("device_check", t - 1, **check)
        self.write(
            "run_end",
            t,
            turns=self.turn + 1,
            tool_calls=self.turn,
            stop=stop,
            seconds=120.0,
            usage={"input_tokens": 50_000, "output_tokens": 2_000},
            flashes_allowed=2,
            arm_runs_allowed=0,
        )


def agent_run_01(
    runs_dir: Path, t: float, podd_ids: tuple[str, ...], *, finish: bool = True
) -> AgentRunDir:
    """The agent's side of scenario 01: a first flash that fails its checks and rolls back, a
    request for a stronger model, a source fix, a verified flash, a clean triage."""
    run = AgentRunDir(runs_dir, "20261108-120000-ollama-watch")
    bad = {"crc": BAD_CRC, "version": "v1.1", "status": "candidate", "origin": "release"}
    run.start(
        t,
        [
            {"id": i, "kind": k, "severity": "critical", "summary": k}
            for i, k in zip(podd_ids, ("crash", "reboot_loop", "safe_mode"), strict=False)
        ],
        {
            "crc": BAD_CRC,
            "build_id": "pod v1.1 x",
            "running": True,
            "version": "v1.1",
            "status": "candidate",
            "origin": "release",
        },
    )
    run.call(
        t + 5,
        "triage",
        {},
        {
            "findings": [
                {"kind": "crash", "severity": "critical", "summary": "HardFault at pc 0x08002B9A"}
            ]
        },
        thinking="The pod is crash-looping. Let me look at the fault.",
    )
    run.call(
        t + 20,
        "fw_flash",
        {"image": "CAFEF00D", "reason": "try the other candidate"},
        {
            "ok": False,
            "result": "failed",
            "image": {"crc": "CAFEF00D", "version": "v1.1-x", "status": "candidate"},
            "checks": [{"check": "boot_event", "ok": True}, {"check": "no_reboots", "ok": False}],
            "rollback": {"ok": True, "image": bad},
        },
        risk="flash",
        reason="flash 1 of 3",
        is_error=True,
    )
    run.call(
        t + 25,
        "ask_expert",
        {"question": "Why does frame_pack fault on the M0?", "tried": "flashed CAFEF00D"},
        {"ok": True, "recorded": True, "answered": False},
        risk="notify",
    )
    if not finish:
        return run
    run.call(
        t + 40,
        "fw_build",
        {},
        {
            "ok": True,
            "image": {
                "crc": FIXED_CRC,
                "version": "v1.1-1-dirty",
                "status": "candidate",
                "origin": "workspace",
            },
        },
        risk="code",
    )
    run.call(
        t + 50,
        "fw_flash",
        {"image": FIXED_CRC, "reason": "unaligned store fixed with memcpy"},
        {
            "ok": True,
            "result": "ok",
            "image": {
                "crc": FIXED_CRC,
                "version": "v1.1-1-dirty",
                "status": "candidate",
                "origin": "workspace",
            },
            "checks": [{"check": "boot_event", "ok": True}, {"check": "no_reboots", "ok": True}],
            "triage_after": [],
        },
        risk="flash",
        reason="flash 2 of 3",
    )
    run.call(t + 95, "triage", {}, {"findings": []})
    run.end(
        t + 100,
        "Root cause: unaligned halfword store in frame_pack. Fixed with memcpy; verified.",
        {
            "chip": {
                "crc": FIXED_CRC,
                "build_id": "pod v1.1-1-dirty",
                "running": True,
                "version": "v1.1-1-dirty",
                "status": "candidate",
                "origin": "workspace",
            },
            "findings": [],
            "open_incidents": [],
        },
    )
    return run
