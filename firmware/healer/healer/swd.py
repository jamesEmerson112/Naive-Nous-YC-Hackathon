"""Talk to the chip over SWD (OpenOCD through the on-board probe).

Everything here works even when the firmware is hung or its UART is garbled,
because SWD is the CPU's debug port, independent of the firmware. Reads are
done without halting the CPU unless stated otherwise.
"""
import fcntl
import os
import re
import subprocess
import time
from contextlib import contextmanager

from . import config

PERIPHERALS = {
    # name: (base, [(offset, register name), ...])
    "RCC": (0x40021000, [(0x00, "CR"), (0x04, "CFGR"), (0x08, "CIR"), (0x14, "AHBENR"), (0x18, "APB2ENR"),
                         (0x1C, "APB1ENR"), (0x24, "CSR"), (0x2C, "CFGR2"), (0x30, "CFGR3"), (0x34, "CR2")]),
    "GPIOA": (0x48000000, [(0x00, "MODER"), (0x04, "OTYPER"), (0x0C, "PUPDR"), (0x10, "IDR"), (0x14, "ODR"),
                           (0x20, "AFRL"), (0x24, "AFRH")]),
    "GPIOB": (0x48000400, [(0x00, "MODER"), (0x04, "OTYPER"), (0x0C, "PUPDR"), (0x10, "IDR"), (0x14, "ODR"),
                           (0x20, "AFRL"), (0x24, "AFRH")]),
    "I2C1": (0x40005400, [(0x00, "CR1"), (0x04, "CR2"), (0x10, "TIMINGR"), (0x18, "ISR")]),
    "USART2": (0x40004400, [(0x00, "CR1"), (0x0C, "BRR"), (0x1C, "ISR")]),
    "TIM3": (0x40000400, [(0x00, "CR1"), (0x0C, "DIER"), (0x10, "SR"), (0x24, "CNT"), (0x28, "PSC"), (0x3C, "CCR3")]),
    "IWDG": (0x40003000, [(0x04, "PR"), (0x08, "RLR"), (0x0C, "SR")]),
    "SCB": (0xE000ED00, [(0x00, "CPUID"), (0x04, "ICSR"), (0x0C, "AIRCR"), (0x10, "SCR"), (0x14, "CCR")]),
    "SYSTICK": (0xE000E010, [(0x00, "CSR"), (0x04, "LOAD"), (0x08, "VAL")]),
    "DBGMCU": (0x40015800, [(0x00, "IDCODE"), (0x04, "CR"), (0x08, "APB1FZ")]),
}


class SwdError(RuntimeError):
    pass


@contextmanager
def _lock():
    config.ensure_dirs()
    with open(config.SWD_LOCK, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def openocd(commands, timeout=60):
    """Run OpenOCD with a list of commands. Returns its combined output; raises SwdError on failure."""
    env = os.environ | {"SWD_KHZ": config.SWD_KHZ}
    argv = config.openocd_argv() + ["-c", "; ".join(commands)]
    with _lock():
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired as e:
            raise SwdError("OpenOCD timed out after %d s" % timeout) from e
    out = p.stdout + p.stderr
    if p.returncode != 0:
        errs = [l for l in out.splitlines() if "Error" in l or "error" in l]
        raise SwdError("OpenOCD failed (rc=%d): %s" % (p.returncode, " | ".join(errs[-5:]) or out[-500:]))
    return out


def read_words(spec):
    """spec: {tag: (address, nwords)} -> {tag: [int, ...]} in one OpenOCD session, CPU keeps running."""
    cmds = ["init"] + ['echo "@%s [read_memory 0x%08x 32 %d]"' % (tag, addr, n) for tag, (addr, n) in spec.items()] + ["shutdown"]
    out = openocd(cmds)
    res = {}
    for tag in spec:
        m = re.search(r"^@%s (.*)$" % re.escape(tag), out, re.M)
        if not m:
            raise SwdError("no value for %s" % tag)
        res[tag] = [int(v, 0) for v in m.group(1).split()]
    return res


def parse_fw_info(words):
    if not words or words[0] != config.FW_INFO_MAGIC:
        return None
    raw = b"".join(w.to_bytes(4, "little") for w in words[1:16])
    return raw.split(b"\0", 1)[0].decode("ascii", "replace")


def chip_build_id():
    w = read_words({"info": (config.FW_INFO_ADDR, 16)})["info"]
    return parse_fw_info(w)


def uptime_rate(uptime_addr, seconds=1.0):
    """Read g_uptime_ms twice, `seconds` apart (host clock). Returns (t0, t1, host_dt)."""
    out = openocd(["init", 'echo "@T0 [read_memory 0x%08x 32 1]"' % uptime_addr, "sleep %d" % int(seconds * 1000),
                   'echo "@T1 [read_memory 0x%08x 32 1]"' % uptime_addr, "shutdown"])
    t0 = int(re.search(r"^@T0 (\S+)", out, re.M).group(1), 0)
    t1 = int(re.search(r"^@T1 (\S+)", out, re.M).group(1), 0)
    return t0, t1, seconds


def halt_samples(n=5, gap_ms=150):
    """Halt the CPU briefly n times and record pc/lr/sp; it resumes after each sample.
    The firmware freezes its watchdog while halted (DBGMCU), so this doesn't cause resets."""
    cmds = ["init"]
    for i in range(n):
        cmds += ["halt", 'echo "@S%d [reg pc] [reg lr] [reg sp] [reg xpsr]"' % i, "resume", "sleep %d" % gap_ms]
    cmds.append("shutdown")
    out = openocd(cmds)
    samples = []
    for i in range(n):
        m = re.search(r"^@S%d (.*)$" % i, out, re.M)
        if not m:
            continue
        vals = re.findall(r"(pc|lr|sp|xpsr) \(/32\): (0x[0-9a-fA-F]+)", m.group(1))
        samples.append({k.lower(): v for k, v in vals})
    return samples


def reset():
    openocd(["init", "reset run", "shutdown"])


def flash_elf(elf):
    out = openocd(["program {%s} verify reset exit" % elf], timeout=90)
    if "Verified OK" not in out:
        raise SwdError("flash verify did not report OK")
    return True


def flash_bin(path, address=0x08000000):
    out = openocd(["program {%s} 0x%08x verify reset exit" % (path, address)], timeout=90)
    if "Verified OK" not in out:
        raise SwdError("flash verify did not report OK")
    return True


def backup(path):
    """Dump all 32 KB of flash without halting or resetting the CPU."""
    openocd(["init", "dump_image {%s} 0x08000000 32768" % path, "shutdown"])
    return path


def decode_clock(r):
    """Decode STM32F04x RCC registers into the real clock tree (RM0091 section 6.4)."""
    cr, cfgr, cfgr2 = r["CR"], r["CFGR"], r["CFGR2"]
    HSI, HSI48, HSE = 8_000_000, 48_000_000, None
    sws = (cfgr >> 2) & 3
    pllsrc = (cfgr >> 15) & 3
    pllmul = min(((cfgr >> 18) & 0xF) + 2, 16)
    prediv = (cfgr2 & 0xF) + 1
    src_name = {0: "HSI/2", 1: "HSI/PREDIV", 2: "HSE/PREDIV", 3: "HSI48/PREDIV"}[pllsrc]
    pll_in = {0: HSI / 2, 1: HSI / prediv, 2: None, 3: HSI48 / prediv}[pllsrc]
    pll_out = pll_in * pllmul if pll_in else None
    sysclk = {0: HSI, 1: HSE, 2: pll_out, 3: HSI48}[sws]
    hpre = (cfgr >> 4) & 0xF
    ahb_div = 1 if hpre < 8 else [2, 4, 8, 16, 64, 128, 256, 512][hpre - 8]
    ppre = (cfgr >> 8) & 0x7
    apb_div = 1 if ppre < 4 else [2, 4, 8, 16][ppre - 4]
    hclk = sysclk / ahb_div if sysclk else None
    pclk = hclk / apb_div if hclk else None
    return {"sysclk_source": {0: "HSI", 1: "HSE", 2: "PLL", 3: "HSI48"}[sws],
            "pll_source": src_name, "pll_mul": pllmul, "prediv": prediv,
            "pll_on": bool(cr & (1 << 24)), "sysclk_hz": sysclk, "hclk_hz": hclk, "pclk_hz": pclk}
