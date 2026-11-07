import { repo, type Layer, type Src } from "./meta";
import type { IconName } from "../viz";

/** Device half of the heal loop (Ishaan's docs/project-overview.md §8.1). */
export const DEVICE_LOOP: { icon: IconName; title: string; sub: string }[] = [
  { icon: "alert", title: "Detect", sub: "health tiers 1–3" },
  { icon: "doc", title: "Snapshot", sub: "code, config, firmware" },
  { icon: "shield", title: "Park", sub: "safe pose, torque off" },
  { icon: "eye", title: "Evidence", sub: "logs, telemetry, serial" },
  { icon: "bug", title: "Diagnose + fix", sub: "healer + local model" },
  { icon: "plug", title: "Apply", sub: "restart or reflash" },
  { icon: "gate", title: "Verify", sub: "else roll back" },
  { icon: "mail", title: "Record", sub: "outbox → cloud" },
];

export const HEALTH_TIERS: { tier: string; what: string }[] = [
  { tier: "Tier 1", what: "crash or error" },
  { tier: "Tier 2", what: "hang or stuck loop" },
  { tier: "Tier 3", what: "wrong behavior" },
];

export type Component = "Pi app" | "arm" | "camera" | "ultrasonic" | "STM32" | "model server";
export const COMPONENTS: { name: Component; icon: IconName; layer: Layer }[] = [
  { name: "Pi app", icon: "doc", layer: "edge" },
  { name: "arm", icon: "arm", layer: "edge" },
  { name: "camera", icon: "eye", layer: "edge" },
  { name: "ultrasonic", icon: "loop", layer: "edge" },
  { name: "STM32", icon: "gpu", layer: "edge" },
  { name: "model server", icon: "cube", layer: "compute" },
];

export interface Fault {
  id: string;
  name: string;
  comp: Component;
  plant: string;
  detect: string;
  fix: string;
  verify: string;
  human: string;
}

/** Ishaan's docs/fault-catalog.md, condensed. Ids are the catalog's; short names are ours. */
export const CATALOG: Fault[] = [
  { id: "D1", name: "drop rule", comp: "Pi app", plant: "A hardcoded rule: hand too close opens the gripper", detect: "HOLDING → RELEASE, object falls, no error", fix: "Remove the rule (code fix, tier 3)", verify: "Hand at 3 cm for 2 s: gripper stays closed", human: "" },
  { id: "D2", name: "trusts bad readings", comp: "Pi app", plant: "No range check on sensor readings", detect: "ZeroDivisionError, None errors, false triggers", fix: "Drop out-of-range readings; hold last good", verify: "0, None, > 4 m in a trace: no crash", human: "" },
  { id: "D3", name: "no bus error handling", comp: "arm", plant: "No retry around LeRobot reads; tap the arm", detect: "ConnectionError on sync read, stale heartbeat", fix: "Retry and reconnect; park if a servo stays silent", verify: "Mock bus fails 3 reads; loop survives", human: "a servo stays absent" },
  { id: "F1", name: "sin/cos swap", comp: "STM32", plant: "sin where cos is needed, or a flipped sign", detect: "Arm moves mirrored; goals break the spec", fix: "Fix the control law, rebuild, reflash", verify: "Host test at reference angles; new build ID", human: "" },
  { id: "F2", name: "cast and integer bugs", comp: "STM32", plant: "Divide-before-multiply, wrap, overflow, narrowing", detect: "Goal of 65,000, or distance always 0", fix: "Fix the cast, reflash", verify: "Host tests at boundary values", human: "" },
  { id: "F3", name: "unaligned HardFault", comp: "STM32", plant: "uint32_t read through an unaligned pointer", detect: "Heartbeat and LED stop; HardFault", fix: "Use memcpy, reflash", verify: "Heartbeat runs 10 s; value parses", human: "" },
  { id: "F4", name: "echo wait, no timeout", comp: "STM32", plant: "Busy-wait on ECHO with no timeout", detect: "Heartbeat stops; watchdog reset banners", fix: "Add a timeout and an error code, reflash", verify: "No echo: no resets in 60 s", human: "" },
  { id: "F5", name: "wrong clock / baud", comp: "STM32", plant: "Switch the PLL source", detect: "Garbage serial bytes; wrong blink rate", fix: "Restore the clock config, reflash", verify: "Heartbeat parses at 115200", human: "" },
  { id: "F6", name: "UART overrun", comp: "STM32", plant: "Delete the overrun clear in uart_getc()", detect: "Input ignored; rx count stalls", fix: "Restore the clear, reflash", verify: "200 bytes, then 'i' prints the banner", human: "" },
  { id: "F7", name: "stack overflow", comp: "STM32", plant: "A 4 KB local array on a 1 KB stack", detect: "Random resets or HardFault", fix: "Remove the big array, reflash", verify: "5-minute soak, no resets", human: "" },
  { id: "F8", name: "build breaks", comp: "STM32", plant: "Type error or missing include", detect: "make fails", fix: "Fix the source edit", verify: "make and make check pass", human: "" },
  { id: "F9", name: "flash fails", comp: "STM32", plant: "Stale OpenOCD holds the ST-LINK", detect: "OpenOCD can't open the probe", fix: "Kill the stale session, retry", verify: "Flash and make check pass", human: "board unplugged" },
  { id: "F10", name: "stale build", comp: "STM32", plant: "Flash an old ELF or skip the flash", detect: "make check: build ID mismatch", fix: "Reflash", verify: "make check passes", human: "" },
  { id: "F11", name: "port mix-up", comp: "STM32", plant: "Nucleo and arm adapter both /dev/ttyACM*", detect: "Arm opens the Nucleo; missing motor IDs", fix: "Use /dev/serial/by-id", verify: "Right device after replug, either order", human: "" },
  { id: "S1", name: "startup crash", comp: "Pi app", plant: "Exception in the startup path", detect: "Traceback; restart loop", fix: "Fix the startup code", verify: "Up for 60 s", human: "" },
  { id: "S2", name: "hang", comp: "Pi app", plant: "Blocking read or a loop with no exit", detect: "Stale heartbeat; py-spy shows where", fix: "Add a timeout or exit", verify: "Heartbeat < 5 s old for 60 s", human: "" },
  { id: "S3", name: "missing dependency", comp: "Pi app", plant: "Uninstall a dependency", detect: "ImportError", fix: "Reinstall pinned requirements", verify: "import app works", human: "" },
  { id: "S4", name: "disk full", comp: "Pi app", plant: "Log spam fills the disk", detect: "Errno 28: no space left", fix: "Stop the spam; rotate logs", verify: "Disk < 90%; app writes", human: "" },
  { id: "S5", name: "memory leak", comp: "Pi app", plant: "", detect: "Kernel log: OOM-killed", fix: "Fix the leak", verify: "Memory flat for 10 min", human: "" },
  { id: "S6", name: "port permission", comp: "Pi app", plant: "Remove the user from dialout", detect: "PermissionError on /dev/ttyACM0", fix: "Fix the group or udev rule (sudo)", verify: "", human: "" },
  { id: "S7", name: "under-voltage", comp: "Pi app", plant: "", detect: "get_throttled nonzero", fix: "Park and report; no code change", verify: "Arm parked, incident written", human: "a 5 V / 5 A supply" },
  { id: "S8", name: "clock wrong", comp: "Pi app", plant: "Stop time sync; set 1970", detect: "Log timestamps far in the past", fix: "Record the offset, mark the logs", verify: "", human: "" },
  { id: "S9", name: "Python too old", comp: "Pi app", plant: "", detect: "LeRobot 0.7 refuses Python 3.11", fix: "Install 3.12 with uv; new venv", verify: "", human: "" },
  { id: "A1", name: "arm port", comp: "arm", plant: "", detect: "Port won't open, or the wrong one opens", fix: "Use /dev/serial/by-id", verify: "", human: "" },
  { id: "A2", name: "LeRobot 0.6 script", comp: "arm", plant: "Code written for 0.6", detect: "ImportError or AttributeError", fix: "Port to the 0.7 API", verify: "", human: "" },
  { id: "A3", name: "flaky bus", comp: "arm", plant: "", detect: "Transient read failures", fix: "Retry in code", verify: "", human: "" },
  { id: "A4", name: "slow loop", comp: "arm", plant: "A slow call inside the control loop", detect: "Loop period far over 33 ms", fix: "Move the slow call out", verify: "p99 loop < 33 ms", human: "" },
  { id: "A5", name: "dead servo", comp: "arm", plant: "Dead, unpowered or wrong-supply servo", detect: "Missing motor IDs; voltage error", fix: "Park (restraint)", verify: "", human: "hardware repair" },
  { id: "A6", name: "servo jam", comp: "arm", plant: "", detect: "Torque_Enable write fails", fix: "Park; torque off", verify: "", human: "clear the jam" },
  { id: "A7", name: "calibration lost", comp: "arm", plant: "Delete or swap the calibration file", detect: "Calibration prompt or bad pose", fix: "Restore the last good copy", verify: "", human: "recalibrate" },
  { id: "A8", name: "gripper snaps", comp: "arm", plant: "", detect: "Gripper snaps open at start", fix: "Restore calibration, or park", verify: "", human: "" },
  { id: "A9", name: "two port owners", comp: "arm", plant: "Two processes open the serial port", detect: "Second open fails or reads garble", fix: "Route all arm access through one owner", verify: "", human: "" },
  { id: "C1", name: "wrong index", comp: "camera", plant: "Wrong camera index", detect: "Failed to open OpenCVCamera(0)", fix: "Use /dev/v4l/by-id", verify: "", human: "" },
  { id: "C2", name: "unsupported mode", comp: "camera", plant: "Unsupported fps or resolution", detect: "actual_fps differs", fix: "Pick a supported mode; MJPG", verify: "", human: "" },
  { id: "C3", name: "camera busy", comp: "camera", plant: "Another process holds it", detect: "Device or resource busy", fix: "Kill the stale process", verify: "", human: "" },
  { id: "C4", name: "frozen stream", comp: "camera", plant: "", detect: "Timed out waiting for a frame", fix: "Restart the camera or reset USB", verify: "", human: "" },
  { id: "C5", name: "lens covered", comp: "camera", plant: "Cover the lens", detect: "Black frames, no error", fix: "Park and report; don't touch code", verify: "", human: "uncover the lens" },
  { id: "C6", name: "low fps", comp: "camera", plant: "", detect: "Measured fps far below config", fix: "Set fourcc to MJPG", verify: "", human: "" },
  { id: "U1", name: "old GPIO library", comp: "ultrasonic", plant: "RPi.GPIO on a Pi 5", detect: "BadPinFactory", fix: "Switch to gpiozero + lgpio", verify: "", human: "" },
  { id: "U2", name: "pin busy", comp: "ultrasonic", plant: "Another process holds the pin", detect: "GPIO busy", fix: "Kill the stale reader", verify: "", human: "" },
  { id: "U3", name: "TRIG/ECHO swapped", comp: "ultrasonic", plant: "Swap the pins in config", detect: "Readings stuck at max", fix: "Fix the config", verify: "", human: "" },
  { id: "U4", name: "noisy readings", comp: "ultrasonic", plant: "Inject spikes", detect: "Jumps of several cm on a still target", fix: "Median filter", verify: "", human: "" },
  { id: "U5", name: "gpiochip number", comp: "ultrasonic", plant: "Hard-coded chip number", detect: "Can't open gpiochip", fix: "Fix config (gpiodetect)", verify: "", human: "" },
  { id: "J1", name: "model hang", comp: "model server", plant: "", detect: "/health times out", fix: "Restart; fall back to the Pi's small model", verify: "", human: "" },
  { id: "J2", name: "GPU out of memory", comp: "model server", plant: "", detect: "CUDA OOM", fix: "Lower context size, restart", verify: "", human: "" },
  { id: "J3", name: "thermal throttling", comp: "model server", plant: "", detect: "Temps rise, GPU clock drops", fix: "Report only", verify: "", human: "" },
  { id: "J4", name: "llama.cpp build fails", comp: "model server", plant: "", detect: "__fp16 undefined on CUDA 11.4", fix: "Use the patched checkout", verify: "", human: "" },
];

export const HARDWARE: { name: string; icon: IconName; layer: Layer; role: string; note?: string }[] = [
  { name: "Raspberry Pi 5 · 8 GB", icon: "gpu", layer: "edge", role: "robot app, healer agent, local model" },
  { name: "SO-101 arm", icon: "arm", layer: "edge", role: "6× STS3215 servos, LeRobot 0.7" },
  { name: "HC-SR04 ultrasonic", icon: "loop", layer: "edge", role: "2–400 cm; its reading drives the arm" },
  { name: "USB camera · 720p", icon: "eye", layer: "edge", role: "evidence for faults" },
  { name: "STM32 Nucleo-F042K6", icon: "gpu", layer: "edge", role: "sanity firmware: LED, boot banner, 1 s heartbeat, build ID via make check", note: "Olir · firmware branch" },
  { name: "Jetson AGX Xavier", icon: "cube", layer: "compute", role: "bigger local model, later", note: "not set up" },
];
export const HARDWARE_NOTE = "Also on the bench: a 5 V / 5 A supply for the Pi, a matching servo supply, and a router with Ethernet as the Wi-Fi backup.";

/** The demo plan in Ishaan's overview (§7). Still being designed. */
export const DEMO_PLAN: { icon: IconName; title: string; sub: string }[] = [
  { icon: "arm", title: "Hold", sub: "arm holds an object" },
  { icon: "bug", title: "Planted bug", sub: "hand near sonar → it drops" },
  { icon: "shield", title: "Heal", sub: "healer removes the rule" },
  { icon: "gpu", title: "C bug", sub: "STM32 math moves arm wrong" },
  { icon: "plug", title: "Reflash", sub: "healer fixes C, reflashes" },
  { icon: "phone", title: "Hero", sub: "cover camera → park → report → parts call" },
];

export const HEAL_TERMS: { term: string; what: string }[] = [
  { term: "Healer", what: "On-device agent + local model: detects, diagnoses, fixes, verifies" },
  { term: "Harness", what: "Prompt, tools, playbooks, checks and policy around the model; versioned by the cloud" },
  { term: "Overlay", what: "Local patch over the cloud version; reverts itself if health fails or the fault returns" },
  { term: "Visor", what: "Device layer that pulls, checks, installs and rolls back versions" },
  { term: "Canary", what: "The one device a new version runs on before the fleet" },
  { term: "Park", what: "Safe pose, torque off" },
];

export const HEAL_SRC: Src[] = [repo("docs/project-overview.md", "Ishaan's project overview"), repo("docs/fault-catalog.md", "fault catalog")];
