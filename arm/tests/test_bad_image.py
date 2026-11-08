"""The v1.7 bad image: the patch applies to the v1.0 source, changes one line of the distance math, and that
line (compiled as C, 32-bit unsigned like the Cortex-M0) wraps every in-range echo into 0-32 mm, so the
arm's start gate would refuse whatever is in front of the sensor.

  ~/lerobot-venv/bin/python -m pytest arm/tests -q
"""
import json
import re
import shutil
import subprocess

import pytest

from simpod import BAD_DIR, REPO, mm_v10, mm_v17
import safe_replay

GOLDEN = REPO / "firmware" / "pod"
DIST_LINE = re.compile(r"out->dist_mm = [^;]*343[^;]*;")   # the conversion, not the "= 0" reset


def changed_lines(patch):
    return ([ln[1:] for ln in patch.splitlines() if ln.startswith("+") and not ln.startswith("+++")],
            [ln[1:] for ln in patch.splitlines() if ln.startswith("-") and not ln.startswith("---")])


def test_patch_applies_to_v10_and_changes_only_the_distance_math(tmp_path):
    shutil.copytree(GOLDEN / "src", tmp_path / "src")
    patch = (BAD_DIR / "bug.patch").read_text()
    r = subprocess.run(["git", "apply", "--check", str(BAD_DIR / "bug.patch")], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert re.findall(r"^\+\+\+ b/(\S+)", patch, re.M) == ["src/hcsr04.c"]
    added, removed = changed_lines(patch)
    code_added = [ln for ln in added if not ln.strip().startswith(("/*", "*"))]
    assert len(removed) == 1 and len(code_added) == 1
    assert DIST_LINE.search(removed[0]) and DIST_LINE.search(code_added[0])
    assert "(uint16_t)" in code_added[0] and "(uint16_t)" not in removed[0]


def test_scenario_metadata_matches_the_prebuilt_image():
    sc = json.loads((BAD_DIR / "scenario.json").read_text())
    build = json.loads((BAD_DIR / "image" / "build.json").read_text())
    assert sc["name"] == BAD_DIR.name and sc["version"] == build["version"] == "v1.7"
    assert (BAD_DIR / "image" / "pod.elf").exists() and (BAD_DIR / "image" / "pod.bin").exists()


@pytest.mark.skipif(not shutil.which("gcc"), reason="needs a host C compiler")
def test_compiled_firmware_math_wraps_every_distance_into_0_to_32_mm(tmp_path):
    """Compile the two dist_mm lines exactly as written in hcsr04.c (v1.0) and in the patch (v1.7)."""
    good = DIST_LINE.search((GOLDEN / "src" / "hcsr04.c").read_text()).group(0)
    bad = DIST_LINE.search(changed_lines((BAD_DIR / "bug.patch").read_text())[0][-1]).group(0)
    (tmp_path / "m.c").write_text("""
#include <stdint.h>
#include <stdio.h>
typedef struct { uint32_t dist_mm; } r_t;
static uint32_t good(uint32_t us) { r_t o, *out = &o; %s return o.dist_mm; }
static uint32_t bad(uint32_t us)  { r_t o, *out = &o; %s return o.dist_mm; }
int main(void) { for (uint32_t us = 116; us <= 23300; us++) printf("%%u %%u %%u\\n", us, good(us), bad(us)); return 0; }
""" % (good, bad))
    subprocess.run(["gcc", "-O1", "-o", str(tmp_path / "m"), str(tmp_path / "m.c")], check=True)
    rows = [tuple(map(int, ln.split())) for ln in subprocess.run([str(tmp_path / "m")], capture_output=True,
                                                                 text=True, check=True).stdout.split("\n") if ln]
    assert len(rows) == 23300 - 116 + 1
    for us, g, b in rows:
        assert g == mm_v10(us) and b == mm_v17(us)          # the simulator uses the same arithmetic
        assert abs(g - us * 0.1715) <= 1                   # v1.0: distance = echo_us * 0.343 / 2
        assert 0 <= b <= 32                                # v1.7: always "something within 33 mm"
        assert safe_replay.classify({"st": "ok", "mm": b, "us": us}, 200) == "blocked"
    # On v1.0 everything past the gate's 200 mm limit is clear: only the firmware blocks the arm.
    assert all(safe_replay.classify({"st": "ok", "mm": g, "us": us}, 200) == "clear" for us, g, _ in rows if g >= 200)
