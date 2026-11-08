# Firmware

Bare-metal STM32 firmware, built and flashed from a Raspberry Pi 5. Everything
needed (compiler, flasher/debugger, CMSIS headers) lives in `toolkit/`. You can
copy that folder to another machine or bundle it with one command.

```
firmware/
├── toolkit/                  portable toolchain package
│   ├── setup.sh              installs everything into toolkit/opt (no root)
│   ├── versions.env          pinned versions + SHA-256 of every download
│   ├── env.sh                `source` it to put the tools on PATH
│   ├── pack.sh               bundle toolkit + projects for another machine
│   ├── mk/stm32.mk           shared build/flash/debug rules
│   ├── probes/*.cfg          jlink (default), stlink, cmsis-dap
│   ├── openocd/swd-speed.tcl pins the SWD clock (see "Board notes")
│   ├── src/newlib_stubs.c    weak syscall stubs (override _write for printf)
│   └── bin/fwtool            serial console + "is my build running?" check
├── stm32f042k6-hello/        sanity firmware for the STM32F042K6
├── stm32f042k6-hcsr04/       HC-SR04 ultrasonic distance, streamed as CSV
├── pod/                      sensor pod firmware: HC-SR04 + LIS3DH + self-diagnostics (pod/README.md)
├── healer/                   Pi-side logging, triage and repair tools for an LLM (healer/README.md)
├── demo/                     known-bad firmware images + operator script for the demo (docs/DEMO.md)
└── docs/                     PLAN.md (the plan), DEMO.md (every demo problem and how the tools solve it)
```

## Quick start (this Pi)

```bash
cd firmware/stm32f042k6-hello
make flash      # build, write, verify, run
make check      # prove the board runs the build you just made
make monitor    # serial console at 115200 (Ctrl-] exits)
```

A passing `make check` looks like this:

```
[PASS] probe connected; SWD reads work
[PASS] build ID on the chip matches build/hello.elf
       "hello 6ba404e 2026-11-07T22:58:01Z"
[PASS] firmware is running: uptime went 161 ms -> 462 ms in 300 ms
[PASS] boot banner on /dev/ttyACM0 shows the same build ID (after a reset)
       build : hello 6ba404e 2026-11-07T22:58:01Z

RESULT: PASS: the code built here is what's running on the board
```

Each build stamps a unique ID (git revision + build time) into a `.fw_info`
section in flash. `check` reads that ID back from the chip over SWD and compares
it with `build/hello.elf`. It also confirms the CPU is running (an uptime
counter advancing in RAM) and that the boot banner on the serial port shows the
same ID. If you rebuild without flashing, `check` fails and says so.

On the serial console you also see the LED (PB3) blinking at 1 Hz, plus:

```
=== STM32F042K6 sanity firmware ===
build : hello 6ba404e 2026-11-07T22:58:01Z
chip  : dev 0x445 rev 0x1000, 32 KB flash
uid   : 20313153-41304312-8009001E
clock : 48000000 Hz
reset : pin software
keys  : 'i' info, 'r' reset, anything else echoes

alive: uptime 8.5 s, rx 1 bytes
```

### All make targets

| target | does |
|---|---|
| `make` | build `build/<project>.elf/.hex/.bin` and print memory use |
| `make flash` | build, program, verify, reset |
| `make check` | confirm the chip runs this exact build (see above) |
| `make monitor` | serial console |
| `make gdb` | source-level debugging (OpenOCD runs as a gdb pipe) |
| `make gdbserver` | OpenOCD on :3333 for VS Code Cortex-Debug or a remote gdb |
| `make backup` / `make restore BIN=…` | dump the chip's flash to `backups/`, or write a dump back |
| `make reset`, `erase`, `size`, `disasm`, `clean` | what they say |

Variables: `PROBE=stlink|cmsis-dap|jlink`, `PORT=/dev/ttyACM0` (auto-detected
by default), `BAUD=115200`, `SWD_KHZ=1000`.

## HC-SR04 distance streaming (`stm32f042k6-hcsr04/`)

Wiring: sensor VCC → 5V, GND → GND, TRIG → A0 (PA0), ECHO → 10 kΩ → D3 (PB0),
with 18 kΩ from D3 to GND. PA0 and PB0 are not 5 V tolerant, so the divider is required.

```bash
cd firmware/stm32f042k6-hcsr04
make flash && make monitor
```

At boot the firmware runs a wiring self-test. It fires the sensor and, while
ECHO is high, measures the voltage on D3 with the chip's ADC. It flags DANGER if
D3 reaches the 3.3 V rail (divider missing) and WARN if the voltage is too low
(resistors swapped). Press `v` to rerun it. Then it streams 10 measurements a second:

```
# wiring check: echo high = 3.11 V at D3 (MCU supply 3.31 V), so sensor VCC ~ 4.84 V: OK
# t_ms,dist_mm,echo_us,status
45,530,3088,ok
144,533,3109,ok
```

`status` is `ok`, `out_of_range` (outside 2–400 cm, often nothing in front),
`no_echo` (check wiring) or `busy`. Lines starting with `#` are comments, so
`grep -v '^#'` leaves plain CSV. Keys: `p` pause/resume, `v` wiring check, `i` info, `r` reset.
TIM3 timestamps the ECHO edges in hardware at 1 µs resolution (≈0.17 mm);
distance assumes sound travels at 343 m/s (20 °C).

## Setting up another machine

The toolkit runs on Linux and macOS, on arm64 or x86-64. It needs `make`,
`python3` and `curl` (or `wget`). There are three ways:

1. **Online:** copy or clone `firmware/`, then run `toolkit/setup.sh`.
   Downloads are about 300 MB, the install about 500 MB, and it takes about a minute.
   Add `--udev` on Linux if the probe needs USB permission rules (it uses sudo once).
2. **Ready-to-run bundle** (same OS and CPU, e.g. Pi 5 → Pi 5): run
   `toolkit/pack.sh` to get a 150 MB tarball in `toolkit/dist/` with the tools
   already installed. On the target: `tar xzf fw-toolkit-*.tar.gz && cd firmware/stm32f042k6-hello && make flash`.
3. **Offline installer for any platform:** run `toolkit/pack.sh --sources linux-x64 darwin-arm64`
   (or `--all`). The bundle holds the checksummed downloads; on the target run
   `firmware/toolkit/setup.sh --offline`.

Both bundle types were tested by extracting them into a fresh directory and
running build → flash → check.

What gets installed (pinned in `toolkit/versions.env`):

| tool | version | from |
|---|---|---|
| arm-none-eabi-gcc, binutils, newlib(-nano), gdb | 15.2.1-1.1 | [xPack](https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack/releases) |
| OpenOCD | 0.12.0-7 | [xPack](https://github.com/xpack-dev-tools/openocd-xpack/releases) |
| CMSIS core headers | 6.3.0 | [ARM-software/CMSIS_6](https://github.com/ARM-software/CMSIS_6) |
| CMSIS STM32F0 device headers + startup | 2.3.8 | [STMicroelectronics/cmsis-device-f0](https://github.com/STMicroelectronics/cmsis-device-f0) |

`setup.sh` trims the compiler from 1.1 GB to about 0.5 GB. It keeps only the
Cortex-M libraries (M0 through M33, covering every STM32 family except the
M55-based N6) and drops Fortran and the Python-enabled gdb. `--full` keeps everything.

## Starting a new project / another STM32

1. Copy `stm32f042k6-hello/` to a new folder.
2. Edit its `Makefile`: `CPU`, `DEVICE`, `FAMILY`, `STARTUP`, `OPENOCD_TARGET`,
   plus `FPU` for chips with an FPU. For example, an STM32G431 uses `cortex-m4`,
   `STM32G431xx`, `g4`, `startup_stm32g431xx.s`, `target/stm32g4x.cfg`, and
   `FPU := -mfpu=fpv4-sp-d16 -mfloat-abi=hard`.
3. Set the flash and RAM sizes in the linker script.
4. For a new family, add it to `CMSIS_DEVICE_FAMILIES` in `toolkit/versions.env`
   with its version and SHA-256, then rerun `toolkit/setup.sh`. If you leave the
   SHA-256 blank, setup stops and prints the checksum it got.
5. Keep `src/fw_info.h` and define `g_fw_info` / `g_uptime_ms` (see `main.c`)
   if you want `make check` to keep working.

## Board notes (this setup)

- **Board:** STM32F042K6, device ID 0x445, 32 KB flash, 6 KB RAM, on a Nucleo-32
  style board. Its on-board ST-LINK/V2-1 has been reflashed to **SEGGER J-Link OB**
  firmware (OpenOCD reports `J-Link STLink V21`), so `PROBE=jlink` is the default.
  With stock ST-LINK firmware, use `PROBE=stlink`.
- **Pins used:** LED on PB3 (LD3); USART2 TX on PA2 and RX on PA15, which reach
  the probe's virtual COM port (`/dev/ttyACM0`, `/dev/serial/by-id/usb-SEGGER_J-Link_*`).
- **SWD clock:** OpenOCD's `stm32f0x.cfg` raises SWD to 8 MHz after reset
  (capped at 4 MHz by this probe). At 4 MHz this probe gets SWD parity errors
  and flashing fails; 2 MHz and below are reliable. `toolkit/openocd/swd-speed.tcl`
  holds it at `SWD_KHZ` (default 1000).
- **CMSIS clock quirk:** on the F042, ST's `SystemCoreClockUpdate()` misreads the
  PLL source "HSI/2" and reports 96 MHz for a real 48 MHz. That makes the UART run
  at half speed. `main.c` selects the equivalent "HSI/PREDIV, PREDIV=2" instead,
  which CMSIS computes correctly. This was measured on the board.
- **Original firmware backup:** before the first flash, the chip's original
  firmware was dumped to `~/stm32-backup/f042k6-original-flash.bin` (SHA-256
  `ff89d911…160e`), along with the option bytes. To put it back:
  `make restore BIN=$HOME/stm32-backup/f042k6-original-flash.bin`.
