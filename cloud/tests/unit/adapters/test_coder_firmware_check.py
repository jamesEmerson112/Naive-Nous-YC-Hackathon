"""The firmware build check (`cloud/coder/firmware_check.py`, blocker B8) runs inside the image;
these tests cover the parts that need no STM32 toolchain. The real build is in
`tests/integration/test_coder_firmware.py` (CODER_INTEGRATION=1)."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "cloud" / "coder" / "firmware_check.py"
_spec = importlib.util.spec_from_file_location("coder_firmware_check", SCRIPT)
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)


def test_finds_every_stm32_project_in_firmware():
    names = [p.name for p in fc.find_projects(ROOT / "firmware")]
    assert {"pod", "stm32f042k6-hello", "stm32f042k6-hcsr04"} <= set(names)
    assert "toolkit" not in names and "healer" not in names


def test_parse_size_reads_berkeley_output():
    out = (
        "   text\t   data\t    bss\t    dec\t    hex\tfilename\n"
        "  14512\t    108\t   2436\t  17056\t   42a0\tbuild/pod.elf\n"
    )
    assert fc.parse_size(out) == {
        "text": 14512,
        "data": 108,
        "bss": 2436,
        "flash": 14620,
        "ram": 2544,
    }
    assert fc.parse_size("make: *** [all] Error 1") is None


def test_read_pins_matches_the_toolkit_file():
    pins = fc.read_pins(ROOT / "firmware" / "toolkit" / "versions.env")
    assert pins["GCC_VERSION"] and pins["CMSIS_DEVICE_FAMILIES"] == "f0"
    assert fc.read_pins(ROOT / "nope.env") == {}


def test_link_toolchain_replaces_host_tools_in_the_copy(tmp_path):
    (tmp_path / "toolkit" / "opt" / "gcc").mkdir(parents=True)  # e.g. macOS binaries copied over
    fc.link_toolchain(tmp_path, Path("/opt/fw-toolkit"))
    opt = tmp_path / "toolkit" / "opt"
    assert opt.is_symlink() and opt.readlink() == Path("/opt/fw-toolkit/opt")
    fc.link_toolchain(tmp_path, Path("/opt/fw-toolkit"))  # idempotent
    assert opt.readlink() == Path("/opt/fw-toolkit/opt")


def test_check_fails_without_a_toolchain_and_lists_the_bench_tests(tmp_path):
    fw = tmp_path / "firmware"
    (fw / "toolkit").mkdir(parents=True)
    (fw / "blinky").mkdir()
    (fw / "blinky" / "Makefile").write_text("# built with toolkit/mk/stm32.mk\nall:\n\tfalse\n")
    (fw / "healer" / "tests").mkdir(parents=True)
    (fw / "healer" / "tests" / "bench.py").write_text("")
    result = fc.check(fw, tmp_path / "no-toolchain")
    assert not result["passed"]
    assert [b["project"] for b in result["builds"]] == ["blinky"] and not result["builds"][0][
        "passed"
    ]
    assert "healer/tests/bench.py" in result["hardware_only"]
    assert result["demo_images"] == {"ran": False}
    assert "RESULT: FAIL" in fc.report(result)
