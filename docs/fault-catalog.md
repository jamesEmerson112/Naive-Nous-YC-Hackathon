# Fault catalog: self-healing robot

Every failure we plan to plant, test or demo. Use it to write faults into the firmware and the Pi app, and to build the benchmark. The big picture is in [project-overview.md](project-overview.md).

- **Snapshot:** Sat Nov 7 2026. This is a living doc, so add faults as we find them.
- **Labels:** every fact cites a source. *(team)* is a decision the team made on Nov 7. *(inferred)* is our own reasoning. "Unverified" means a search result nobody has confirmed yet.
- **Error text** is quoted only when a source shows it. Otherwise the symptom is described; capture the real text on the device and paste it in.

## Setup this catalog assumes

- **The Raspberry Pi 5 (8 GB) runs everything for now:** the robot app, the healer agent and its local model. The Jetson AGX Xavier isn't set up yet. *(team)*
- **Wired to the Pi:** the HC-SR04 ultrasonic sensor on GPIO, and a USB camera module (HBV-W20217 V22, HD 720p, 72° lens). *(team)*
- **The arm** is an SO-101 (leader and follower, 6 Feetech STS3215 servos each), driven through LeRobot v0.7.0 with `robot_type` `so101_follower`. [CLAUDE.md, team status]
- **The firmware** is bare-metal C for an STM32F042K6 on a Nucleo-F042K6 board:
  - Cortex-M0, 32 KB flash, 6 KB RAM, no HAL, no printf.
  - UART2 runs at 115200 8N1 to the board's ST-LINK virtual COM port, which shows up as `/dev/ttyACM0` on the dev machine.
  - `make check` reads the build ID back over SWD, which proves the chip runs the new build.

  Source: `firmware/stm32f042k6-hello/` on the `firmware` branch.
- **Not decided:** what the STM32 does in the robot's loop (read the sensor? compute arm targets?). Part A2 assumes the control math lives in the firmware. *(inferred)*

## How to read an entry

- **Plant:** the code change or physical action that creates the fault.
- **Sees:** what the healer can observe: log lines, telemetry, behavior.
- **Tier:** 1 = crash or error. 2 = hang or stuck loop. 3 = runs, but behaves wrong. *(team)*
- **Outcome:** what a correct heal does.
  - `fix-code` / `fix-config`: edit, rebuild (and reflash, for firmware), restart.
  - `ops`: restart something, kill a stale process, clean up.
  - `park`: the cause is hardware. Turn torque off, write an incident and leave the code alone. These tasks test restraint.
- **Verify:** the benchmark's pass/fail check after the heal. Keep it hidden from the agent. *(inferred)*
- **Mac:** how to run the task with no hardware.

## Rules for planting faults

1. **One fault per task.** Each task ships three scripts *(inferred; the same shape as AIOpsLab's inject-then-evaluate problems, https://github.com/microsoft/AIOpsLab)*:
   - `inject` plants the fault.
   - `verify` exits 0 only when the system is healthy and nothing else broke.
   - `oracle` applies the known fix, to prove the task is solvable.
2. **Write down the intended behavior.** A tier-3 fault is only "wrong" when measured against a written spec, such as "while holding an object, never open the gripper because of the ultrasonic reading". Put it in a `SPEC.md` the healer can read. *(inferred)*
3. **Keep the control logic apart from hardware access.** Put the math and the state machine (distance in, arm command out) in files with no register or GPIO access. `verify` can then unit-test them on a Mac or the Pi with plain gcc or Python, without flashing anything. *(inferred)*
4. **Follow the logging contract below.** The healer can only fix what it can see.

## Logging contract

`firmware/stm32f042k6-hello/src/main.c` already prints a boot banner (build ID, chip, clock, reset cause) and an `alive: uptime …` heartbeat every second [`firmware` branch]. Suggested additions *(inferred)*:

- **Telemetry, one line per control step:** `T t=<ms> raw_us=<echo µs> dist_mm=<mm> valid=<0|1> state=<HOLDING|…> goal=<servo targets>`
- **State changes, with the reason:** `S HOLDING->RELEASE reason=dist_lt_threshold dist_mm=31`
- **Errors, with a code:** `E code=ECHO_TIMEOUT t=<ms>`
- **The Pi app** logs the same fields as JSON lines, plus the joint targets it sends through LeRobot and every exception with its traceback.

---

## Part A: demo faults

### A1. Python on the Pi (iteration 1)

The arm holds an object, and the ultrasonic reading drives what it does. *(team)*

**D1. Drop-on-proximity rule (bad code)**
- **Plant:** a hardcoded rule such as `if dist < THRESHOLD: open_gripper()` that runs while the arm is holding. *(team)*
- **Sees:** a `HOLDING -> RELEASE` state change when a hand comes near, and the object falls (the camera can show it). No error.
- **Tier 3. Outcome:** `fix-code`. Remove or disable the rule, because the spec says to keep holding. *(team)*
- **Verify:** feed a "hand at 3 cm for 2 s" distance trace through the control step. The gripper command stays closed, and normal behavior is unchanged.
- **Mac:** run the control logic on a scripted distance trace, with LeRobot's mock servo bus (`tests/mocks/mock_motors_bus.py`, confirmed on GitHub).

**D2. Reading inside the sensor's blind zone**
- **Plant:** code that trusts every reading, divides by it, or has no range check.
- **Trigger:** a hand closer than the HC-SR04's minimum range. The datasheet gives 2–400 cm (https://cdn.sparkfun.com/datasheets/Sensors/Proximity/HCSR04.pdf).
- **Sees:** a crash (`ZeroDivisionError`, or a `TypeError` on `None`), or a false trigger like D1. Note that gpiozero's `DistanceSensor` caps readings at `max_distance`, which defaults to 1 m (https://gpiozero.readthedocs.io/en/stable/api_input.html).
- **Tier 1 or 3. Outcome:** `fix-code`. Drop readings outside the valid range, then hold the last good value or use a median filter.
- **Verify:** a trace with 0, `None` and over-4 m values causes no crash and no state change.
- **Mac:** gpiozero's `MockFactory` (https://gpiozero.readthedocs.io/en/stable/api_pins.html), or a scripted trace.

**D3. Tapping the arm freezes or crashes the control loop**
- **Plant:** no error handling or timeout around LeRobot reads and writes. The physical trigger is tapping the arm. *(team)*
- **Sees:** either a traceback after `ConnectionError: Failed to sync read 'Present_Position' on ids=[…] after 1 tries. [TxRxResult] Incorrect status packet!`, or a hung loop with a stale heartbeat. The error text comes from https://github.com/huggingface/lerobot/issues/3131 and predates v0.7.0's new servo bus, so recheck it.
- **Tier 1 or 2. Outcome:** depends on whether the fault clears.
  - Transient: `fix-code`. Add a retry and reconnect while the arm keeps holding. Upstream's fix was more retries (https://github.com/huggingface/lerobot/pull/3888).
  - A servo stays silent (`Missing motor IDs: …`, https://github.com/huggingface/lerobot/issues/3211): `park`.
- **Verify:** the mock bus fails 3 reads, then recovers. The loop survives and the gripper doesn't change. When one servo stays absent instead, the arm is parked and an incident is written.
- **Mac:** `MockBus` and its `absent` set (confirmed in `tests/mocks/mock_motors_bus.py`), plus a shim that fails the Nth read.

### A2. C firmware on the STM32 (iteration 2)

The healer fixes the C code and flashes the board. *(team)*

A firmware heal goes like this: read the serial log, find the bug, run `make`, flash over SWD (OpenOCD, with `target/stm32f0x.cfg` named in the Makefile), run `make check` to confirm the chip now runs the new build ID, then watch the telemetry [`firmware` branch Makefile]. The Makefile includes `../toolkit/mk/stm32.mk`, and `fw_info.h` refers to `toolkit/bin/fwtool`; neither is on the branch yet.

Two Cortex-M0 facts shape these bugs. It has no fault status registers, and unaligned memory accesses always fault (https://interrupt.memfault.com/blog/cortex-m-hardfault-debug).

**F1. sin and cos swapped**
- **Plant:** `sin(theta)` where the control law needs `cos(theta)`, or a flipped sign. *(team)*
- **Sees:** the arm moves the mirrored or wrong way, and telemetry goals don't match the spec's reference points. No error.
- **Tier 3. Outcome:** `fix-code`, then reflash.
- **Verify:** a host unit test of the control law at reference angles. On the device: telemetry goals within tolerance, and a new build ID on the chip.
- **Mac:** compile the control-law file with host gcc and run the test vectors. Needs rule 3.

**F2. Casting and integer bugs** (one per task; the team expects "a casting error")
- **F2a, divide before multiply:** `dist_mm = (echo_us / 2000) * 343` truncates to 0 for short echoes, so everything reads as "very close".
- **F2b, negative angle cast to unsigned:** `uint16_t goal = (uint16_t)(deg * 4096 / 360)` with `deg < 0` wraps to about 65,000. That is far outside the servo's position range, so the arm slams to a limit or the servo rejects the command. STS3215 positions are 12-bit, 0–4095 (https://core-electronics.com.au/attachments/uploads/sts3215-smart-servo-datasheet-translated.pdf, unverified).
- **F2c, int16 overflow:** angles in centi-degrees above 327.67° (`int16_t` max is 32767) wrap negative, and the arm swings the other way.
- **F2d, unsigned comparison:** `if (dist_mm - limit_mm < 0)` on unsigned types is never true, so a rule silently never fires.
- **F2e, narrowing:** `uint8_t speed = 300;` stores 44.
- **Sees:** telemetry outside the physical range (a goal of 65,000, a distance that is always 0), or the arm moving wrong.
- **Tier 3** (tier 1 if the servo rejects the command). **Outcome:** `fix-code`, then reflash.
- **Verify:** host unit tests at the boundary values (negative, 0, maximum range), and in-range telemetry on the device.
- **Mac:** host gcc unit tests. Needs rule 3.

**F3. Unaligned pointer cast → HardFault**
- **Plant:** parse a UART packet with `uint32_t v = *(uint32_t *)&buf[1];`.
- **Sees:** the heartbeat and LED stop, and there's no more serial output. Under a debugger the core sits in `HardFault_Handler`. Running the stacked PC through `arm-none-eabi-addr2line` gives the source line (https://interrupt.memfault.com/blog/cortex-m-hardfault-debug).
- **Tier 2** (from the Pi it just looks like silence). **Outcome:** `fix-code` (use `memcpy`, or assemble the bytes), then reflash.
- **Verify:** send the triggering packet. The heartbeat keeps going for 10 s and the value parses correctly.
- **Mac:** board only. A Mac and the Pi both allow unaligned reads, so the bug won't show there.

**F4. Waiting for the echo with no timeout → firmware hang**
- **Plant:** `while (!(ECHO_PORT->IDR & ECHO_PIN)) {}` with no timeout. This applies if the HC-SR04 moves to the STM32; on the Pi the same bug is a Python busy-wait.
- **Trigger:** the sensor is unplugged, nothing is in range, or a hand is in the blind zone.
- **Sees:** the heartbeat stops. If the independent watchdog (IWDG) is enabled, the chip resets and the boot banner shows `reset : iwdg` over and over, because main.c prints the reset cause [`firmware` branch].
- **Tier 2. Outcome:** `fix-code` (a timeout plus `E code=ECHO_TIMEOUT`), then reflash.
- **Verify:** with no echo, the heartbeat continues, timeout errors are logged, and there are no watchdog resets in 60 s.
- **Mac:** a host test with a fake pin-read function that never changes. Needs rule 3.

**F5. Clock misconfigured → wrong UART baud**
- **Plant:** switch back to the fixed HSI/2 PLL source that the comment in main.c warns about. `SystemCoreClockUpdate()` then reports 96 MHz, which doubles the UART baud and SysTick math. The comment says this was measured on the board. [`firmware` branch]
- **Sees:** garbage bytes on the serial port, no readable heartbeat, and the wrong blink rate.
- **Tier 1 or 2. Outcome:** `fix-code`, then reflash.
- **Verify:** heartbeat lines parse at 115200 for 10 s, and the banner shows `clock : 48000000 Hz`.
- **Mac:** board only.

**F6. UART overrun never cleared → input ignored**
- **Plant:** delete the overrun (ORE) clear in `uart_getc()`. main.c notes that "a full RX latches the port until cleared". [`firmware` branch]
- **Trigger:** a burst of bytes faster than the loop reads them.
- **Sees:** the firmware stops reacting to input while the heartbeat continues, and its `rx` byte count stops growing.
- **Tier 3. Outcome:** `fix-code`, then reflash.
- **Verify:** send 200 bytes, then `i`. The banner prints.

**F7. Stack overflow (6 KB of RAM)**
- **Plant:** a large local array, such as `uint16_t samples[2048];` (4 KB), in a function the loop calls. The linker script reserves only 1 KB of stack (`_Min_Stack_Size = 0x400`). [`firmware` branch]
- **Sees:** random resets or a HardFault, and corrupted values.
- **Tier 1 or 2. Outcome:** `fix-code`, then reflash.
- **Verify:** a 5-minute soak with no resets.

**F8. An edit breaks the build**
- **Plant:** a type error, a missing include, or a change to `fw_info_t` that trips its `_Static_assert` [`fw_info.h`].
- **Sees:** `make` fails with a compiler error.
- **Tier 1. Outcome:** `fix-code`.
- **Verify:** `make` exits 0 and `make check` passes.
- **Mac:** yes, with `arm-none-eabi-gcc` installed.

**F9. Flashing fails**
- **Trigger:** another program, such as an old OpenOCD or GDB session, holds the ST-LINK, or the board is unplugged.
- **Sees:** OpenOCD can't open the probe or connect to the target. Capture the exact text on the device.
- **Tier 1. Outcome:** `ops` (kill the stale session and retry), or `park` if the board is gone.
- **Verify:** flashing and `make check` both pass.

**F10. The chip isn't running the new build**
- **Plant:** flash an old ELF, or skip the flash step.
- **Sees:** `make check` reports that the build ID on the chip doesn't match the new ELF's [`fw_info.h` comment].
- **Tier 1. Outcome:** `ops` (reflash).
- **Verify:** `make check` passes.

**F11. Serial port mix-up between the STM32 board and the arm**
- **Trigger:** the Nucleo's virtual COM port and the arm's servo-bus adapter both appear as `/dev/ttyACM*`, so plug order decides which is which. *(inferred; main.c notes that the Nucleo shows up as `/dev/ttyACM0`)*
- **Sees:** the arm code opens the Nucleo, so no servos answer (`Missing motor IDs`). Or the firmware monitor reads servo bytes.
- **Tier 1. Outcome:** `fix-config`. Use `/dev/serial/by-id/…` paths or udev symlinks, as one LeRobot user did (https://github.com/huggingface/lerobot/issues/3299).
- **Verify:** both programs open the right device after replugging in either order.

---

## Part B: background catalog

These come from the Nov 7 research. Pick from here for the wider benchmark.

### Pi app and system

- **S1. Crash at startup → restart loop.**
  - Plant: an exception in the startup path.
  - Sees: a traceback and repeated restarts. Under systemd the unit ends in `start-limit-hit` (unverified; https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html).
  - Tier 1, `fix-code`. Verify: up for 60 s. Mac: yes.
- **S2. Blocking call with no timeout.**
  - Plant: a read or a `while True` with no exit.
  - Sees: a stale heartbeat. `py-spy dump --pid N` shows where it's stuck (https://github.com/benfred/py-spy).
  - Tier 2, `fix-code`. Verify: heartbeat under 5 s old for 60 s. Mac: yes.
- **S3. Broken venv or missing package.**
  - Plant: uninstall a dependency, or delete site-packages.
  - Sees: `ImportError` or `ModuleNotFoundError`. On Bookworm, the system pip refuses installs with `externally-managed-environment` (https://raspberrytips.com/fix-externally-managed-environment-error/).
  - Tier 1, `ops`: reinstall the pinned requirements into the venv. Verify: `python -c "import app"` exits 0. Mac: yes.
- **S4. Log spam fills the disk.**
  - Sees: `OSError: [Errno 28] No space left on device`.
  - Tier 1, `ops` + `fix-code` (stop the spam, rotate logs). Verify: disk under 90% and the app can write. Mac: a small disk image.
- **S5. Memory leak → killed for running out of memory.**
  - Sees: the kernel log names the killed process (exact text unverified).
  - Tier 1, `fix-code`. Verify: memory use flat for 10 min. Mac: yes, with a memory cap.
- **S6. No serial permission.**
  - Plant: remove the user from `dialout`, or `chmod 000` the device.
  - Sees: `PermissionError: [Errno 13] Permission denied: '/dev/ttyACM0'` (pyserial wording, unverified).
  - Tier 1, `fix-config` (group or udev rule; needs sudo). Mac: chmod a pty.
- **S7. Undervoltage.**
  - Sees: `vcgencmd get_throttled` is nonzero, and USB devices may drop out. Bit 0 is under-voltage now and bit 16 is under-voltage since boot, so `0x50005` means under-voltage and throttling, both now and since boot (https://www.raspberrypi.com/documentation/computers/os.html).
  - Tier 3, `park`: the real fix is a 5V/5A supply. Verify: arm parked, incident written, no code changed. Mac: a fake `vcgencmd` script on PATH.
- **S8. Wrong clock while offline.**
  - Plant: stop time sync and set the date to 1970.
  - Sees: log timestamps far in the past.
  - Tier 3, `ops`: record the offset and mark the logs. *(inferred)* Mac: `faketime`.
- **S9. Python too old for LeRobot 0.7.**
  - LeRobot v0.7.0 needs Python 3.12 or newer, installed with `uv sync --locked --extra feetech` (`docs/so101-research.md`). Raspberry Pi OS Bookworm ships Python 3.11 *(general knowledge: Debian 12)*.
  - Sees: the install refuses the interpreter version (capture the exact text on the Pi).
  - Tier 1, `ops`: install Python 3.12 with uv and recreate the venv. Mac: run the install under a Python 3.11 venv.

### Arm (SO-101, LeRobot v0.7.0)

- **A1. Wrong or swapped arm port.** Sees: the port won't open, or the wrong device opens (see F11). Tier 1, `fix-config` (use `/dev/serial/by-id`). Source: https://github.com/huggingface/lerobot/issues/3299
- **A2. Script written for LeRobot 0.6.**
  - What changed: v0.7.0 (Nov 5) moved Feetech servos to a rustypot-backed `SerialMotorsBus`, replaced `OperatingMode`/`TorqueMode` with `bus.set_operating_mode(...)`, and removed `scan_port` and `broadcast_ping` (release notes, confirmed: https://github.com/huggingface/lerobot/releases/tag/v0.7.0).
  - Sees: `ImportError` or `AttributeError`.
  - Tier 1, `fix-code`. Mac: yes.
- **A3. Flaky bus reads mid-loop.** The transient case of D3. Tier 1, `fix-code`.
- **A4. Slow call inside the control loop.**
  - Sees: loop period far above 33 ms, then bus timeouts (https://github.com/huggingface/lerobot/issues/3299).
  - Tier 2, `fix-code` (move the slow call out). Verify: 99th-percentile loop period under 33 ms. Mac: yes.
- **A5. Servo dead, unpowered or on the wrong supply.**
  - Sees: `Motor 'gripper' (model 'sts3215') was not found` or `Missing motor IDs` (https://github.com/huggingface/lerobot/issues/3394, https://github.com/huggingface/lerobot/issues/3211). With the wrong supply: `[RxPacketError] Input voltage error!` (https://github.com/TheRobotStudio/SO-ARM100/issues/174).
  - Tier 1, `park`. Mac: `MockBus` with `absent`.
- **A6. Jammed or overloaded servo.**
  - Sees: `Failed to write 'Torque_Enable' … Incorrect status packet!` (https://github.com/huggingface/lerobot/issues/2819).
  - Servo alarm bits never raise an exception, and `sync_write` drops packets silently. So the health check has to poll the `status` register (`robot.bus.sync_read("status", normalize=False)`). A gripper overload alarm can need a power cycle (`docs/so101-research.md`).
  - Tier 1–2, `park` (torque off).
- **A7. Calibration file deleted or wrong.**
  - Plant: delete or swap the file under `~/.cache/huggingface/lerobot/calibration/` (path unverified on v0.7.0).
  - Sees: a calibration prompt, or a bad pose.
  - Tier 3, `ops`: restore the last good copy from the snapshot. Recalibrating needs a person.
- **A8. Gripper snaps open at the start of teleop.** Caused by a drive-mode or offset mismatch (https://github.com/huggingface/lerobot/issues/3942). Tier 3, restore the calibration or `park`.
- **A9. Two processes open the arm's serial port.**
  - Only one process can own the port, so a single arm host should hold it (`docs/so101-research.md`, inferred there).
  - This matters for the healer itself: it must talk to the arm through the running app, or stop the app first, and never open the port alongside it. *(inferred)*
  - Sees: the second open fails, or reads from the arm turn garbled (exact text unverified).
  - Tier 1, `fix-code`: route every arm access through one owner.

### Camera (HBV-W20217 V22 USB module on the Pi)

First list the module's real formats: `v4l2-ctl --list-formats-ext -d /dev/videoN`. LeRobot's `OpenCVCamera` also accepts a video file path, so every camera task can run on a Mac (confirmed in `configuration_opencv.py`).

- **C1. Wrong camera index.**
  - The Pi 5 exposes many `/dev/video*` nodes (https://forum.opencv.org/t/rpi5-camera-doesnt-work/17303/4).
  - Sees: `Failed to open OpenCVCamera(0). Run lerobot-find-cameras opencv …` (LeRobot source on main).
  - Tier 1, `fix-config` (use the capture node or `/dev/v4l/by-id`). Mac: index 99.
- **C2. Unsupported fps or resolution.**
  - Sees: `OpenCVCamera(...) failed to set fps=30 (actual_fps=25.0).` (https://github.com/huggingface/lerobot/pull/2275)
  - Tier 1, `fix-config` (pick a supported mode, use MJPG). Mac: request 1080p60 from a file.
- **C3. Another process holds the camera.**
  - Sees: `VIDIOC_REQBUFS: Device or resource busy` (https://answers.opencv.org/question/44104/device-or-resource-busy).
  - Tier 1, `ops` (kill the stale process). Mac: a lock-file shim.
- **C4. Frozen stream.**
  - Sees: `Timed out waiting for frame from camera OpenCVCamera(...) after 200 ms` (LeRobot source on main).
  - Tier 2, `ops` (restart the camera object or reset USB). Mac: a shim that stops producing frames.
- **C5. Lens covered or dark.**
  - Sees: black frames (mean brightness under about 10) and no error.
  - Tier 3, `park` and report; don't touch code. Mac: all-zero frames.
- **C6. Low fps from uncompressed YUYV.**
  - Sees: measured fps far below the config, with no error (https://github.com/huggingface/lerobot/issues/4084).
  - Tier 3, `fix-config` (`fourcc="MJPG"`).

### Ultrasonic (HC-SR04 on the Pi 5's GPIO)

Wiring note: the HC-SR04 runs on 5V, so its ECHO output needs a voltage divider before a 3.3V Pi GPIO pin. gpiozero's docs suggest 330 Ω and 470 Ω (https://gpiozero.readthedocs.io/en/stable/api_input.html).

- **U1. Old GPIO library on the Pi 5.**
  - RPi.GPIO doesn't drive the Pi 5's new GPIO chip, and gpiozero needs the `lgpio` pin factory there.
  - Sees: `RuntimeError: Failed to add edge detection` (https://bugs.launchpad.net/raspbian/+bug/2058658), a `PinFactoryFallback` warning, or `BadPinFactory: Unable to load any default pin factory!` (https://gpiozero.readthedocs.io/en/stable/api_pins.html).
  - Tier 1, `fix-code` (switch to gpiozero with lgpio, or install `python3-rpi-lgpio`). Mac: a stub module that raises.
- **U2. Pin held by another process.**
  - Sees: `lgpio.error: 'GPIO busy'` (unverified) or `'GPIO not allocated'` (https://rpi-lgpio.readthedocs.io/en/latest/differences.html).
  - Tier 1, `ops` (kill the stale reader). Mac: `MockFactory`.
- **U3. TRIG and ECHO swapped in the config.** Sees: readings stuck at the maximum, with no error. Tier 3, `fix-config`. Mac: `MockFactory`.
- **U4. Noisy readings.** Sees: jumps of several cm on a still target. Tier 3, `fix-code` (median filter, more samples). Mac: injected spikes.
- **U5. Hard-coded GPIO chip number.**
  - Which `/dev/gpiochipN` holds the header pins can differ across Pi 5 kernels and libraries; rpi-lgpio exposes `RPI_LGPIO_CHIP` for this (https://rpi-lgpio.readthedocs.io/en/latest/differences.html). Check on the device with `gpiodetect`.
  - Sees: `lgpio.error: 'can not open gpiochip'`.
  - Tier 1, `fix-config`.

### Model server (later, once the Xavier is set up)

- **J1. Model server hangs while loading.**
  - Sees: `/health` times out; the process is alive but stuck at "loading model" (https://github.com/ggml-org/llama.cpp/issues/10555).
  - Tier 2, `ops`: the supervisor restarts it, and the healer falls back to the Pi's small model.
- **J2. Out of GPU memory.**
  - Sees: `NvMapMemAllocInternalTagged: … error 12`, then a CUDA out-of-memory error (https://forums.developer.nvidia.com/t/jetson-xavier-nx-8gb-and-unified-ram-management/369075).
  - Tier 1, `ops` (lower the context size and restart).
- **J3. Throttling.** Sees: tegrastats temperatures rising while GPU frequency drops. Tier 3, report.
- **J4. llama.cpp build fails on CUDA 11.4.**
  - Sees: `ggml-impl.h(314): error: identifier "__fp16" is undefined` (https://github.com/ggml-org/llama.cpp/issues/10555).
  - Tier 1, `fix-code`: use a checkout that includes PR #10616.

---

## Suggested first set *(inferred)*

- **Iteration 1 (Pi, Python):** D1, D2, D3, S1, S2, S3, A5, C1, C4, U1, S7. All of them run on a Mac.
- **Iteration 2 (STM32, C):** F1, F2b, F4, F8, F11. F1, F2 and F4 can be unit-tested on a Mac if the control logic follows rule 3.
- **Restraint checks**, where the right answer is `park`: S7, A5, C5.

## Scoring, per task *(inferred)*

- **Healed:** `verify` passes on the first attempt.
- **Cost:** time to heal, plus agent steps and tokens.
- **Regressions:** other checks that broke.
- **For `park` tasks:** the arm is parked, an incident is written, and no code was edited.
