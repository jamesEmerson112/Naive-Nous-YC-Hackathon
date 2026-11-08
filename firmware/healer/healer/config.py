"""Paths and settings. Override with environment variables when needed."""
import os
from pathlib import Path

HEALER = Path(__file__).resolve().parent.parent          # firmware/healer
FIRMWARE = HEALER.parent                                 # firmware/
TOOLKIT = FIRMWARE / "toolkit"
GOLDEN_SRC = FIRMWARE / "pod"                            # reference (known-good) firmware source
DEMO = FIRMWARE / "demo"

VAR = Path(os.environ.get("HEALER_VAR", HEALER / "var"))
LOGS = VAR / "logs"
IMAGES = VAR / "images"
BACKUPS = VAR / "backups"
WORKSPACE = VAR / "workspace" / "pod"                    # the firmware source the LLM may edit (its own git repo)
SOCK = VAR / "podd.sock"
SWD_LOCK = VAR / "swd.lock"

PORT = os.environ.get("POD_PORT", "auto")
BAUD = int(os.environ.get("POD_BAUD", "115200"))
PROBE = os.environ.get("POD_PROBE", "jlink")
SWD_KHZ = os.environ.get("SWD_KHZ", "1000")

GCC_BIN = TOOLKIT / "opt" / "gcc" / "bin"
OPENOCD = TOOLKIT / "opt" / "openocd" / "bin" / "openocd"
FW_INFO_ADDR = 0x080000C0      # .fw_info follows the 192-byte STM32F042 vector table
FW_INFO_MAGIC = 0x31495746     # "FWI1"
UART_BYTES_PER_S = BAUD // 10  # 8N1: 10 bits per byte


def gcc_tool(name):
    return str(GCC_BIN / ("arm-none-eabi-" + name))


def openocd_argv():
    return [str(OPENOCD),
            "-f", str(TOOLKIT / "probes" / (PROBE + ".cfg")),
            "-f", "target/stm32f0x.cfg",
            "-f", str(TOOLKIT / "openocd" / "swd-speed.tcl")]


def ensure_dirs():
    for d in (VAR, LOGS, IMAGES, BACKUPS):
        d.mkdir(parents=True, exist_ok=True)
