"""Firmware build check for the `coder` sandbox (blocker B8): build a scratch copy of
`firmware/` with the image's STM32 toolchain, offline, then run the checks that need no board.

    python /opt/romulus/firmware_check.py [FIRMWARE_DIR]

FIRMWARE_DIR defaults to /work/firmware, else /work. It works as a CODER_TEST_CMD too.
It changes the copy: it points `toolkit/opt` at the image's toolchain and writes build output.

1. Every project whose Makefile includes the toolkit's `mk/stm32.mk` is built with the
   firmware's own `make`; it reports pass/fail, flash and RAM use, the .bin size and warnings.
2. `demo/scenario build-images` rebuilds each known-bad demo release (v1.0 + its bug.patch)
   and says whether its commit matches the prebuilt image's build.json.
3. Tests that need the board (`healer/tests/*.py`, make flash/check) are listed, not run.

Ends stdout with one line, `ROMULUS_RESULT <json>`. Exit 0 only if every build passed.
Standard library only: it runs on the device's Python in the image.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TOOLCHAIN = Path(os.environ.get("FW_TOOLCHAIN", "/opt/fw-toolkit"))
MARKER = "ROMULUS_RESULT "
HARDWARE_ONLY = "needs the board, podd and the SWD probe; runs on the bench"
SIZE_LINE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+\d+\s+[0-9a-f]+\s+(\S+)$", re.M)
BUILT_IMAGE = re.compile(r"^(\S+)\s+.*\scommit (\w+)$", re.M)


def run(argv, cwd, timeout_s=600) -> tuple[int, str]:
    """(exit code, stdout + stderr); never raises."""
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout_s)
        return p.returncode, p.stdout + p.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, f"{type(exc).__name__}: {exc}"


def read_pins(path: Path) -> dict[str, str]:
    """KEY=value lines of a versions.env (comments and quotes dropped)."""
    pins = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not line.lstrip().startswith("#"):
                pins[key.strip()] = value.strip().strip('"')
    return pins


def link_toolchain(fw: Path, toolchain: Path) -> None:
    """Point the copy's toolkit/opt at the image's tools; stm32.mk and healer/ look there."""
    opt = fw / "toolkit" / "opt"
    if opt.is_symlink() or opt.is_file():
        opt.unlink()
    elif opt.is_dir():
        shutil.rmtree(opt)  # host tools copied by mistake: wrong OS or CPU for this container
    opt.symlink_to(toolchain / "opt")


def find_projects(fw: Path) -> list[Path]:
    return sorted(
        mk.parent for mk in fw.glob("*/Makefile") if "mk/stm32.mk" in mk.read_text(errors="replace")
    )


def parse_size(text: str) -> dict[str, int] | None:
    """Berkeley `arm-none-eabi-size` output → flash (text + data) and RAM (data + bss) bytes."""
    m = SIZE_LINE.search(text)
    if not m:
        return None
    text_b, data_b, bss_b = (int(m.group(i)) for i in (1, 2, 3))
    return {
        "text": text_b,
        "data": data_b,
        "bss": bss_b,
        "flash": text_b + data_b,
        "ram": data_b + bss_b,
    }


def build_project(proj: Path, size_tool: str) -> dict:
    code, out = run(["make", "-C", str(proj)], cwd=proj)
    result = {
        "project": proj.name,
        "passed": code == 0,
        "exit_code": code,
        "warnings": out.count("warning:"),
    }
    elfs = sorted((proj / "build").glob("*.elf"))
    if code == 0 and elfs:
        _, size_out = run([size_tool, str(elfs[0])], cwd=proj)
        result["size"] = parse_size(size_out)
        bin_file = elfs[0].with_suffix(".bin")
        result["bin_bytes"] = bin_file.stat().st_size if bin_file.exists() else None
    if code != 0:
        result["output"] = out[-4000:]
    return result


def build_demo_images(fw: Path) -> dict:
    """Rebuild the known-bad demo releases; compare each commit with the prebuilt build.json."""
    script, bad = fw / "demo" / "scenario", fw / "demo" / "bad-images"
    if not script.is_file():
        return {"ran": False}
    prebuilt = {}
    for meta in bad.glob("*/image/build.json"):
        prebuilt[meta.parent.parent.name] = json.loads(meta.read_text()).get("commit")
    code, out = run([sys.executable, str(script), "build-images"], cwd=fw)
    images = [
        {"name": name, "commit": commit, "matches_prebuilt": prebuilt.get(name) == commit}
        for name, commit in BUILT_IMAGE.findall(out)
    ]
    result = {
        "ran": True,
        "passed": code == 0 and bool(images),
        "exit_code": code,
        "images": images,
    }
    if code != 0:
        result["output"] = out[-4000:]
    return result


def check(fw: Path, toolchain: Path = TOOLCHAIN) -> dict:
    link_toolchain(fw, toolchain)
    gcc = toolchain / "opt" / "gcc" / "bin" / "arm-none-eabi-gcc"
    _, gcc_version = run([str(gcc), "--version"], cwd=fw)
    _, make_version = run(["make", "--version"], cwd=fw)
    image_pins, wanted_pins = (
        read_pins(toolchain / "versions.env"),
        read_pins(fw / "toolkit" / "versions.env"),
    )
    builds = [build_project(p, str(gcc.with_name("arm-none-eabi-size"))) for p in find_projects(fw)]
    demo = build_demo_images(fw)
    return {
        "toolchain": {
            "gcc": gcc_version.splitlines()[0] if gcc_version else "",
            "make": make_version.splitlines()[0] if make_version else "",
            "pinned": image_pins.get("GCC_VERSION", ""),
            # firmware/ pins another toolchain than the image has: rebuild with make coder-image
            "stale": image_pins != wanted_pins,
        },
        "builds": builds,
        "demo_images": demo,
        "hardware_only": {
            str(t.relative_to(fw)): HARDWARE_ONLY for t in sorted(fw.glob("healer/tests/*.py"))
        }
        | {"make flash / check / gdb (every project)": "SWD probe; no OpenOCD in the sandbox"},
        "passed": bool(builds) and all(b["passed"] for b in builds) and demo.get("passed", True),
    }


def report(result: dict) -> str:
    tc = result["toolchain"]
    lines = [f"toolchain: {tc['gcc']} (pinned {tc['pinned']}); {tc['make']}"]
    if tc["stale"]:
        lines.append(
            "WARN  firmware/toolkit/versions.env differs from the image's: run make coder-image"
        )
    for b in result["builds"]:
        size = b.get("size") or {}
        detail = (
            f"flash {size.get('flash')} B, RAM {size.get('ram')} B, .bin {b.get('bin_bytes')} B"
            if size
            else ""
        )
        mark = "PASS" if b["passed"] else "FAIL"
        lines.append(f"{mark}  make -C {b['project']}  {detail}, {b['warnings']} warnings")
    demo = result["demo_images"]
    if demo.get("ran"):
        mark = "PASS" if demo["passed"] else "FAIL"
        lines.append(f"{mark}  demo/scenario build-images ({len(demo['images'])} images)")
        for img in demo["images"]:
            same = "matches" if img["matches_prebuilt"] else "DIFFERS from"
            lines.append(f"        {img['name']}: commit {img['commit']} {same} the prebuilt image")
    for name, why in result["hardware_only"].items():
        lines.append(f"SKIP  {name}: {why}")
    lines.append(f"RESULT: {'PASS' if result['passed'] else 'FAIL'}")
    return "\n".join(lines)


def main() -> int:
    default = Path("/work/firmware") if Path("/work/firmware").is_dir() else Path("/work")
    fw = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    result = check(fw.resolve())
    print(report(result))
    for b in result["builds"]:
        if "output" in b:
            print(f"\n--- make -C {b['project']} (tail)\n{b['output']}")
    if "output" in result["demo_images"]:
        print(f"\n--- demo/scenario build-images (tail)\n{result['demo_images']['output']}")
    sys.stdout.write("\n" + MARKER + json.dumps(result) + "\n")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
