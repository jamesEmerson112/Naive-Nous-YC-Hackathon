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


def _fake_firmware(tmp_path: Path, *, built_commit: str = "abc1234"):
    """A firmware/ whose build and demo script succeed without a toolchain, and a fake image."""
    fw, image = tmp_path / "firmware", tmp_path / "image"
    (fw / "toolkit").mkdir(parents=True)
    (fw / "toolkit" / "versions.env").write_text("GCC_VERSION=15.2.1-1.1\n")
    (image / "opt").mkdir(parents=True)
    (image / "versions.env").write_text("GCC_VERSION=15.2.1-1.1\n")
    (fw / "blinky").mkdir()
    (fw / "blinky" / "Makefile").write_text("# built with toolkit/mk/stm32.mk\nall:\n\ttrue\n")
    (fw / "demo" / "bad-images" / "01-bug" / "image").mkdir(parents=True)
    (fw / "demo" / "bad-images" / "01-bug" / "image" / "build.json").write_text(
        '{"commit": "abc1234"}'
    )
    (fw / "demo" / "scenario").write_text(
        f'print("01-bug                 pod v1.1 2026-11-08T01:57:45Z  crc 32ACFDAF  '
        f'commit {built_commit}")\n'
    )
    return fw, image


def test_check_passes_when_everything_matches(tmp_path):
    fw, image = _fake_firmware(tmp_path)
    result = fc.check(fw, image)
    assert result["passed"] and result["failures"] == [], result["failures"]
    assert "make -C blinky  0 warnings" in fc.report(result)  # no stray comma without a size


def test_a_stale_toolchain_fails_the_check(tmp_path):
    fw, image = _fake_firmware(tmp_path)
    (fw / "toolkit" / "versions.env").write_text("GCC_VERSION=16.1.0-1.1\n")  # Olir upgraded
    result = fc.check(fw, image)
    assert not result["passed"] and result["toolchain"]["stale"]
    assert any("make coder-image" in why for why in result["failures"])
    assert "RESULT: FAIL" in fc.report(result)


def test_a_demo_image_that_differs_from_its_prebuilt_one_fails_the_check(tmp_path):
    fw, image = _fake_firmware(tmp_path, built_commit="def5678")
    result = fc.check(fw, image)
    assert not result["passed"]
    assert result["failures"] == [
        "demo image 01-bug: commit def5678 differs from its prebuilt build.json"
    ]
