import { ext, repo, type Src } from "./meta";
import type { SchedBlock } from "../viz";

const LR = "https://huggingface.co/docs/lerobot";

/** James's build plan (the phases in features.txt), on a PST clock. UTC = PST + 8 h. */
export const OUR_WINDOW: { from: string; to: string; scale: [string, string, string] } = {
  from: "2026-11-08T05:00:00Z",
  to: "2026-11-08T21:00:00Z",
  scale: ["Sat 9:00 PM", "overnight: the prototype in sim", "Sun 1:00 PM · present"],
};

/** "Slap everything together" (re-planned Sun 12:15 AM): the whole loop in sim overnight, the same code on the Pi from 7 AM. */
export const OUR_SCHEDULE: SchedBlock[] = [
  { id: "P1", what: "Sim prototypes: sim arm, Hermes so101-arm plugin, level-1 spoken watchdog, approach-then-grasp, fault factory (1,000 runs), ChatTS level-2 test · c42339f…a5ecc11", from: "2026-11-08T05:15:00Z", to: "2026-11-08T07:30:00Z", done: true },
  { id: "P2", what: "Firmware API and data doc (docs/firmware-apis.md) · 235e30d; Olir asked to keep his podd logs overnight", from: "2026-11-08T07:30:00Z", to: "2026-11-08T08:15:00Z", done: true },
  { id: "S0", what: "Unblock: main and Andre's arm/prims merged into james/arm-sim (901aa7d); the restricted bench MCP session needs an org owner or admin (James's key was refused), but James's own key reads the board, so Hermes gets a board-only tool filter instead", from: "2026-11-08T08:15:00Z", to: "2026-11-08T08:45:00Z", done: true },
  { id: "S1", what: "Virtual pod: the sim's rangefinder written as real pod lines on a pty, read by Olir's real podd · 5313a44. Clean link at 9.8 Hz D / 48.6 Hz A; bad image 02 opens podd's own clock_skew (0.50x) in 9 s; podd cmd and Olir's heal pod_cmd work against it", from: "2026-11-08T08:45:00Z", to: "2026-11-08T09:45:00Z", done: true },
  { id: "S2", what: "Arm host reads distance and incidents through podd; restarts the sensor through podd's cmd op; story A's heal stand-in for a firmware rollback · fb01bc9. Story A heals in 17–29 s (podd clock_skew → stand-in rollback → check at home → retry grips); story B 3 of 3 (\"Distance stuck at 110 mm while the arm moved 10 mm\" → restart through podd → retry); 0 false alarms in 62 healthy factory runs", from: "2026-11-08T09:45:00Z", to: "2026-11-08T11:00:00Z", done: true },
  { id: "S3", what: "Romulus on James's Mac: pod-1 through the edge bridge, arm-1 check-ins and incidents from the arm host; hardware fault → work order (fake tonight) · Romulus on the Mac got arm-twin-1's 3 incidents and pod-1's clock_skew (from the edge bridge) with the right outcomes, 5 recordings, and a needs_human work order for the stuck joint", from: "2026-11-08T11:00:00Z", to: "2026-11-08T12:15:00Z", done: true },
  { id: "S4", what: "Naïve card → Hermes: the bench MCP session in Hermes; take the arm job, run the skills, comment RESULT, move the card · Hermes did the test card end to end: the planted stuck sensor surfaced on the reach, it restarted the sensor, retried, picked and lifted the block, commented RESULT and moved the card to done (board-only tool filter on James's key)", from: "2026-11-08T12:15:00Z", to: "2026-11-08T13:00:00Z", done: true },
  { id: "R1", what: "Real-arm adapter on Andre's prims.Arm (arrival, load, Status), tested with a mock", from: "2026-11-08T13:00:00Z", to: "2026-11-08T13:45:00Z", done: false },
  { id: "S5", what: "Both stories end to end in sim from one script each; GIFs for the backup video; MORNING.md", from: "2026-11-08T13:45:00Z", to: "2026-11-08T14:45:00Z", done: false },
  { id: "R0", what: "Pi check: SSH, Python 3.12 + LeRobot 0.7, calibration, both ports pinned by /dev/serial/by-id, podd on the real pod", from: "2026-11-08T15:00:00Z", to: "2026-11-08T15:30:00Z" },
  { id: "R2", what: "Both stories on the real arm and pod, with Andre; story A's rollback by Olir's real pod healer", from: "2026-11-08T15:30:00Z", to: "2026-11-08T17:00:00Z" },
  { id: "R3", what: "First real runs and the overnight podd slice into samples/; push and PR (with approval); Kent's real-arm slot 9–10", from: "2026-11-08T17:00:00Z", to: "2026-11-08T18:00:00Z" },
  { id: "FZ", what: "10 AM code freeze, rehearse both stories ×3, backup video (the sim recording), present at 1 PM", from: "2026-11-08T18:00:00Z", to: "2026-11-08T21:00:00Z" },
  { id: "P5", what: "Hermes plugin entry: so101-arm plugin, pre_tool_call guard, catalog PR, video · due Wed Nov 11, 2 PM PST", from: "2026-11-08T21:00:00Z", to: "2026-11-11T22:00:00Z" },
];

export const SIM_JOBS: { name: string; what: string; how: string }[] = [
  { name: "Test bench", what: "An LLM or VLA drives the simulated SO-101 through the same tool API as the real arm.", how: "Thousands of cheap tries before anything moves on the bench." },
  { name: "Safety twin", what: "Check a planned move before the real arm runs it.", how: "Per move: a local kinematic check (reach, joint limits, workspace box, table clearance). Per new sequence: a dry run in sim." },
  { name: "Synthetic demos", what: "Generate training demos for a VLA instead of hours of teleoperation.", how: "LeIsaac's scripted state machine records straight into LeRobot format; add 10–20 real demos to close the gap." },
  { name: "Fault drills", what: "Inject failures in sim to test the watchdog without risking hardware.", how: "Dropped cube, blocked joint, overheating, camera loss; each drill writes labeled logs so diagnosis can be scored." },
];

export const CANARY: string[] = [
  "A new policy checkpoint (or harness version) arrives.",
  "It runs N tries in sim on held-out cube positions.",
  "If its success rate falls below the current version's, it is rejected.",
  "rollback(policy, rev) keeps the last good version on the arm.",
  "For the demo, push a deliberately bad version (undertrained) and watch the gate catch it.",
];

export const BENCHMARK = {
  conditions: [
    ["VLA drives", "SmolVLA (language-conditioned) or ACT runs the motion from camera images", "fast loop on the bench"],
    ["LLM picks skills", "The LLM calls pick(x, y), place(bin), home(); IK does the motion", "one call every few seconds"],
    ["LLM gives poses", "The LLM outputs x, y, z and gripper targets; IK turns them into joint moves", "most flexible, most ways to fail"],
    ["Scripted baseline", "Color-blob detection plus IK pick-and-place, no model", "the floor to beat"],
  ],
  models: ["OpenAI mini-class model (hackathon credits)", "Qwen3-VL-8B-Instruct on vLLM (RunPod 4090)"],
  metrics: ["success (0 / 0.5 grasp / 1 placed)", "time per try", "model calls, tokens and $ per try", "interventions", "moves blocked by the guard"],
  plan: "20 tries per condition in sim with randomized cube spots, then 10 real tries for the top two.",
  reference: "SmolVLA paper, SO-101 Lego pick-and-place: SmolVLA 90% in-distribution / 50% out-of-distribution, ACT 70% / 40%.",
  src: [ext("SmolVLA paper", "https://arxiv.org/html/2506.01844v1")] as Src[],
};

export const RESET = {
  hilserl: "HIL-SERL resets are scripted (6 tasks) or done by a person (5 tasks). No task learns its own reset (§3.5, Appendix A tables).",
  serl: "SERL trained a forward and a backward policy that reset each other, moving an object between two bins without a person.",
  lerobot: "LeRobot's HIL-SERL port resets only the arm joints (fixed_reset_joint_positions, reset_time_s = 5). Objects go back by hand.",
  ours: [
    "Cube lands in the target bin → success; the next try runs the other direction.",
    "Cube still in the start bin → retry.",
    "Cube elsewhere but visible → scripted IK reset puts it back.",
    "Cube lost, or 3 failures in a row → a person (phone alert + blocked card).",
  ],
  oneLiner: "One language-conditioned SmolVLA (\"move the cube to the left bin\" / \"right bin\") can be its own reset, which is SERL's forward/backward idea done with language (inferred design).",
  src: [ext("HIL-SERL", "https://arxiv.org/html/2410.21845"), ext("SERL", "https://arxiv.org/html/2401.16013"), ext("LeRobot HIL-SERL", `${LR}/hilserl`)] as Src[],
};

export const PODS: { pod: string; gpu: string; price: string; hours: string; cost: string }[] = [
  { pod: "Isaac Sim + LeIsaac", gpu: "RTX A6000 48 GB", price: "$0.33 / $0.59 per h", hours: "~16", cost: "~$5 / ~$10" },
  { pod: "Training (ACT, SmolVLA)", gpu: "RTX A6000 48 GB", price: "$0.33 / $0.59 per h", hours: "~8", cost: "~$3 / ~$5" },
  { pod: "vLLM + policy server", gpu: "RTX 4090 24 GB", price: "$0.34 / $0.89 per h", hours: "~14", cost: "~$5 / ~$13" },
];
export const PODS_NOTE = "Community / secure cloud prices from RunPod's live list. Total ≈ $13–28 against the $75 cap. If Hermes shares the model, it needs 64K context, so that pod moves to a 48 GB card.";

export const RISKS: string[] = [
  "Sim-to-real gap: one report saw 0% on the real arm until the physical setup matched, then 60–80%; sim tends to overrate real success by 9–31 points.",
  "Isaac on RunPod: version drift and no display. Go/no-go after 2 hours, then fall back to MuJoCo.",
  "Remote inference to RunPod over the internet is not validated by LeRobot, and a dropped link means a restart. Venue Wi-Fi makes it worse.",
  "Servo telemetry names in LeRobot 0.7 (rustypot) are partly unconfirmed: probe at startup and run with what exists.",
  "A gripper overload alarm can need a power cycle: keep the switch within reach.",
];

export const LEROBOT: { k: string; v: string }[] = [
  { k: "Version", v: "v0.7.0, released Nov 5, 2026. Needs Python ≥ 3.12; Kent pins it." },
  { k: "Robot class", v: "SO101Follower(SO101FollowerConfig(port, id, use_degrees, cameras, max_relative_target))" },
  { k: "Actions", v: "send_action({\"shoulder_pan.pos\": …}) for shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper. Degrees or −100…100; gripper 0…100." },
  { k: "CLIs", v: "lerobot-find-port, -find-cameras, -calibrate, -teleoperate, -record, -rollout, -train, -edit-dataset, -policy-server" },
  { k: "IK", v: "Placo kinematics with the SO-ARM100 repo's so101_new_calib.urdf; EEBoundsAndSafety clamps the workspace." },
  { k: "Telemetry", v: "bus.sync_read(\"present_temperature\" | \"present_load\" | \"status\" …). Alarm bits never raise and sync_write drops packets silently, so a watchdog must poll." },
  { k: "Policies", v: "act, diffusion, smolvla, pi0, pi05, groot and more. About 50 episodes recommended; 25 did badly for SmolVLA." },
  { k: "VRAM", v: "Training at batch 8: ACT 2–6 GB, SmolVLA 10–16 GB, pi0/groot 24–40 GB." },
  { k: "Remote inference", v: "lerobot-policy-server on a GPU; lerobot-rollout --inference.type=remote on the bench. Zenoh on TCP 7447, unauthenticated: tunnel it." },
  { k: "Datasets", v: "LeRobotDataset v3. Merging needs identical features: camera names, resolution, fps." },
];

export const ISAAC: { k: string; v: string }[] = [
  { k: "GPU", v: "RT cores required: \"GPUs without RT Cores (A100, H100) are not supported\". Minimum RTX 4080 16 GB." },
  { k: "LeIsaac pins", v: "Isaac Sim 5.1 · Isaac Lab 2.3.0 · Python 3.11 (separate env from LeRobot 0.7)." },
  { k: "Task", v: "LeIsaac-SO101-PickOrange-v0. Copy it for a cube-between-bins task." },
  { k: "Demos", v: "scripts/datagen/state_machine/generate.py --use_lerobot_recorder writes LeRobot datasets; cameras front and wrist at 640×480, 30 fps." },
  { k: "Headless", v: "--enable_cameras plus --no-window; watch recorded video, since RunPod blocks the UDP that WebRTC needs." },
  { k: "Fallback", v: "MuJoCo Menagerie robotstudio_so101 runs on a Mac; Kent pins MuJoCo 4.0.0." },
];

export const EDGE: { k: string; v: string }[] = [
  { k: "Pi 5", v: "LeRobot runs there (Python ≥ 3.12) but SO101Follower imports torch; a torch-free path is an open PR." },
  { k: "Host pattern", v: "Copy LeKiwi's host/client split (ZMQ, JSON + JPEG, 500 ms stop-on-silence) for a plain SO-101; no official SO-101 host exists." },
  { k: "Arduino / ESP32", v: "Can bridge the servo bus or watch servo health, but cannot run LeRobot. A host computer still owns the arm." },
  { k: "Servo registers", v: "STS3215: position 56, load 60, voltage 62, temperature 63, status 65, moving 66, current 69. Status bits: overheat, over-current, overload (partly confirmed)." },
];

export const LLM_CHOICE: { k: string; v: string }[] = [
  { k: "OpenAI", v: "A mini-class model for the loop (about $1 or less per hour of 5-second calls); a strong model for comparison. Paid from the $100 credits." },
  { k: "Open model", v: "Qwen3-VL-8B-Instruct: vllm serve … --enable-auto-tool-choice --tool-call-parser hermes (parser inferred; smoke-test it)." },
  { k: "Pod, not serverless", v: "RunPod's serverless vLLM worker doesn't document image input; a plain pod avoids cold starts." },
];

export const PROPOSAL_SRC: Src[] = [
  ext("LeRobot docs", LR),
  ext("LeIsaac", "https://github.com/LightwheelAI/leisaac"),
  ext("Isaac Sim requirements", "https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html"),
  ext("vLLM tool calling", "https://docs.vllm.ai/en/latest/features/tool_calling.html"),
  repo("docs/so101-research.md", "docs/so101-research.md"),
];

/* ---------- Interfaces offered to Romulus (draft) ---------- */

export const API: [string, string, string][] = [
  ["GET /v1/status", "arm_status", "mode, joints, gripper %, EE xyz, holding, active faults, policy rev"],
  ["POST /v1/photo", "arm_photo", "image path; the model reads it with vision_analyze"],
  ["POST /v1/skill/pick", "arm_pick", "x, y in metres → grasped, reason (table area only)"],
  ["POST /v1/skill/place", "arm_place", "bin A or B → ok, reason"],
  ["POST /v1/move/ee", "arm_move_ee", "x, y, z, gripper → reached; 422 if outside the box or below table + 10 mm"],
  ["POST /v1/home · /v1/stop", "arm_home · arm_stop", "stop is always allowed"],
  ["POST /v1/check", "guard only", "same safety check with no motion, under 10 ms (for pre_tool_call)"],
  ["POST /v1/policy/run", "arm_run_policy", "task, max seconds → success, duration"],
  ["POST /v1/reset_scene", "arm_reset_scene", "two tries, then escalate"],
  ["POST /v1/mode", "set_mode (Kent)", "safe | run"],
  ["POST /v1/policy/rollback", "rollback (Kent)", "back to the last good policy rev"],
  ["POST /v1/restart", "restart (Kent)", "camera | policy | bus"],
  ["POST /v1/drill/inject", "operator only", "fault id, seconds; never an LLM tool"],
];

export const TELEMETRY: string[] = [
  "Every line: v, type, ts, seq, arm_id, backend (real | mujoco | isaac), session_id, episode_id, mode",
  "tlm (~10 Hz), per joint: q, q_goal, q_err, temp_c, load, current_ma, voltage_v, status_bits, moving; plus ee_xyz_m, holding, read errors, loop ms, camera fps / frozen",
  "event: kind (fault_detected, reflex, recovery_start/end, escalation, cmd_blocked, success_check), fault_id, severity, metric, value, threshold, action, result, snapshot",
  "episode: controller, task, success, duration, model calls, tokens, $, interventions, resets",
  "drills: ground-truth labels in a separate file, never shown to the diagnosis model",
];

export const FAULTS: [string, string, string, string, string][] = [
  ["grasp_slip", "gripper closes past cube width, or load drops mid-carry", "open the gripper in software; pull the cube", "locate, re-pick (≤ 2)", "2 fails or cube not found"],
  ["camera_lost", "fps < 5, frozen frame, very dark", "cover the lens; blank frames in sim", "pause, reopen camera", "> 30 s"],
  ["collision_stall", "joint error > 10° with high load and no motion", "a hand blocks a slow move", "hold, back off 2 cm, retry higher", "2 retries"],
  ["overheat", "≥ 55 °C warn, ≥ 65 °C critical (placeholders)", "software temperature offset", "half speed, then rest pose; resume below 45 °C", "> 10 min or 3× per hour"],
  ["bus_fault", "3 failed reads in a row, or a silently dropped write", "drop reads/writes in software", "re-send, reconnect, verify", "3 failed reconnects"],
  ["gripper_overload", "overload status bit, gripper unresponsive", "set the bit in sim", "stop gripper, torque cycle", "not cleared: power cycle by a person"],
  ["calib_drift", "FK vs camera off > 15 mm, wrist_roll > 5° at home", "add a joint offset", "flag only", "a person runs lerobot-calibrate"],
  ["bad_policy", "success < 40% over 5 tries, spike in blocked moves", "activate a known-bad version", "stop policy, roll back", "last good also fails"],
];

export const PLUGIN_WIRING: string[] = [
  "The plugin is a thin client: each arm tool calls the arm host's HTTP API and returns JSON; handlers never raise.",
  "pre_tool_call returns early for non-arm tools, calls /v1/check, and blocks with a reason the model can read. In cron runs approvals default to deny, so block rather than approve.",
  "post_tool_call logs every call; the plugin posts a short summary on the job's card.",
  "The arm host POSTs incidents to a Hermes webhook (:8644, HMAC) whose route fires a diagnosis cron job.",
  "Fast alerts skip the model: hermes send --to telegram.",
];

export const INTERFACE_SRC: Src[] = [
  repo("CLAUDE.md", "CLAUDE.md · Romulus action names"),
  ext("Hermes hooks", "https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks"),
  ext("Hermes webhooks", "https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks"),
  ext("Feetech STS register map", "https://raw.githubusercontent.com/waveshare/Servo-Driver-with-ESP32/main/SCServo/SMS_STS.h"),
];
