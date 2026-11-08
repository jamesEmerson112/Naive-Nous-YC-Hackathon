import { ext, REPO, repo, type Layer, type Src, type Status } from "./meta";

export interface Person {
  who: string;
  initial: string;
  layer: Layer;
  /** What they're on right now. */
  doing: string;
  /** What landed since the last map (4:06 PM). */
  landed: string;
  /** When their status was last written, and where. */
  synced: string;
}

export const PEOPLE: Person[] = [
  {
    who: "Kent",
    initial: "K",
    layer: "heal",
    doing: "Three PRs open: M8b wires the coding agent into batch and gate (#28); the edge bridge brings Olir's pod healer into Romulus (#25, D29); the Fleet Doctor foundation (#27). Next: the first live Naïve card, then the overnight launch (~12:30 AM).",
    landed: "24 PRs merged: Romulus M0–M7 and M8a (#1–#19), the build-status docs (#21), Fleet Doctor plan, decisions D23–D28 and contracts (#22, #23, #26), the STM32 toolchain in the sandbox (#24, closes B8). Flows F1–F8 and F10 pass on fake devices. B4 cleared: the team key reaches the Naïve proxy.",
    synced: "8:20 PM · his block",
  },
  {
    who: "Olir",
    initial: "O",
    layer: "edge",
    doing: "Pod firmware and healer tools are on main. HARNESS.md hands the pod healer's LLM agent loop and blind eval to whoever builds them; nobody has claimed them.",
    landed: "#20 (7:34 PM): STM32 sensor pod (HC-SR04 + LIS3DH, self-diagnostics), podd + 38 heal tools, six known-bad releases v1.1–v1.6, a portable toolkit/. Bench detection re-verified for scenarios 01–05.",
    synced: "7:34 PM · his merge",
  },
  {
    who: "Ishaan",
    initial: "I",
    layer: "edge",
    doing: "Getting the Jetson edge box to talk to the Mac: it's plugged in, but no USB device comes up and there's no LAN path yet. The device code (B1) is still unpushed.",
    landed: "Healer benchmark (835c4f0): qwen3.5:4b with thinking on, harness h3, passed 10 of 12, and it's the only config to pass D1. Device suite: 18 tests, and each planted fault turns it red.",
    synced: "8:12 PM · his block",
  },
  {
    who: "James",
    initial: "J",
    layer: "hermes",
    doing: "Plan: build arm prototypes tonight without the real arm (a MuJoCo twin, Kent's fake device, Olir's sensor pod), then plug in the red follower arm tomorrow to test and collect data. Owns the Hermes install and this map.",
    landed: "Naïve console + fault lab (98fe550): pi and Hermes each fixed 12 of 12 runs of Ishaan's six faults on gpt-6-sol; Hermes was faster on 5 of 6, and pi spent $1.37. Self-Healing Arm Briefing page (private).",
    synced: "7:58 PM · team status",
  },
  {
    who: "Andre",
    initial: "A",
    layer: "edge",
    doing: "Our main person for the arm: calibrating the SO-101 at the bench and building arm/: teleop, recording and motion primitives. With Olir he also has assembly and the camera and ultrasonic wiring (features.txt).",
    landed: "First push, arm/teleop-recording (8:19 PM, no PR yet): leader → follower teleop, LeRobot v3 recording, JSON motion primitives with play and chain, and a recorded 13.2 s \"wave\".",
    synced: "8:19 PM · his branch",
  },
];

export interface OffMain {
  who: string;
  where: string;
  what: string;
  state: string;
  href?: string;
}

/** Work that isn't on main: every branch on GitHub (no forks exist), plus what status blocks say is local. Checked 8:10 PM. */
export const OFF_MAIN: OffMain[] = [
  { who: "Kent", where: "cloud/m8b-wiring", what: "M8b: the coding agent wired into batch and gate (F11)", state: "PR #28 open", href: `${REPO}/pull/28` },
  { who: "Kent", where: "edge/bridge", what: "Edge bridge: Olir's pod healer reports to Romulus with no device changes (D29)", state: "PR #25 open", href: `${REPO}/pull/25` },
  { who: "Kent", where: "doctor/foundation", what: "Fleet Doctor foundation: settings, tables, domain rules, stubs (doctor_mode=off)", state: "PR #27 open", href: `${REPO}/pull/27` },
  { who: "Kent", where: "cloud/m8-coder-wip", what: "M8 WIP: toy device, code replay, an F11 draft", state: "marked \"not for merge\"; the toy device and replay landed in #19 (inferred)" },
  { who: "Olir", where: "firmware, demo", what: "Nothing: both branches end at ded518a, which is in main", state: "merged as #20" },
  { who: "Ishaan", where: "his own checkout (no branch, no fork)", what: "device/ (app, fake hardware, 18 tests), harness/ (h1–h3), bench/ (benchmark runner)", state: "not pushed: blocker B1 for Kent" },
  { who: "Andre", where: "arm/teleop-recording", what: "arm/: SO-101 teleop, LeRobot recording, motion primitives, a recorded \"wave\"", state: "pushed 8:19 PM; no PR yet" },
  { who: "James", where: "claude/robot-library-olir-firmware-1b9411 (this Mac)", what: "79f938b: marks the pod's ultrasonic sample as verified pod output", state: "local commit, not pushed" },
  { who: "James", where: "private artifact + local note", what: "Self-Healing Arm Briefing; a phased plan for tomorrow's demo", state: "not in the repo", href: "https://claude.ai/artifact/7MVLeY6DMTCrLLodGUT9Ft" },
];

export interface Lane {
  name: string;
  layer: Layer;
  who: string[];
  owner: string;
  status: Status;
  pills: string[];
  sunday: string[];
  stretch: string[];
  needs: string[];
  overlaps: string;
}

/** Five lanes by layer. Owners and scope are tentative: nothing here is final. */
export const LANES: Lane[] = [
  {
    name: "Business on Naïve",
    layer: "naive",
    who: ["K", "?"],
    owner: "Kent: the org, parts.yaml and posting work orders · the fleet-ops agent and intake: nobody yet",
    status: "open",
    pills: ["intake", "board → Stripe", "work orders"],
    sunday: [
      "Romulus posts work orders as Naïve board cards: built and tested in fake mode (M5, #11, #15). The first live card waits on Kent's go-ahead.",
      "No fleet-ops agent exists yet. The org has the CEO agent and James's test agents (Kent's spike, PR #21).",
      "Hires (Operator, Insights, Roboticist) are on hold (N1).",
    ],
    stretch: ["Storefront app", "CRM for customers", "AgentMail customer updates"],
    needs: ["An owner for the fleet-ops agent", "Mobilerun MCP set up with a spend cap", "Stripe direction: bill the robot owner, or pay the supplier"],
    overlaps: "Kent's M5 posts the work-order cards this lane's agent picks up. Romulus's batch model already runs on the Naïve proxy (gpt-oss-120b).",
  },
  {
    name: "Bench control: Hermes plugin",
    layer: "hermes",
    who: ["J"],
    owner: "James: Hermes installed and tested · the arm plugin: nobody yet",
    status: "tentative",
    pills: ["arm plugin", "guard hook", "phone alerts"],
    sunday: [
      "Hermes v0.23 runs on James's Mac with Naïve's MCP tools and a ChatGPT-plan model (gpt-6-luna or gpt-6-sol).",
      "Fault lab: Hermes and pi each fixed 12 of 12 stand-in faults; Hermes was faster on 5 of 6 (docs/pi-vs-hermes.md).",
      "Not started: arm tools in a plugin, the pre_tool_call guard, cron pulling the next card, gateway alerts.",
    ],
    stretch: ["Catalog PR, video and form by Wed 2 PM PST"],
    needs: ["An owner for the arm plugin", "One owner of the arm's serial port", "Hermes compaction fix: its summarizer can't load the ChatGPT login"],
    overlaps: "The healers don't run on Hermes: Ishaan's device healer is mini-swe-agent + qwen3.5:4b, and Olir's pod healer is a tool set waiting for an agent loop.",
  },
  {
    name: "Device: Pi 5 + STM32",
    layer: "edge",
    who: ["I", "O", "A"],
    owner: "Ishaan: the device side (D14) · Olir: the STM32 sensor pod · Andre: the arm itself",
    status: "tentative",
    pills: ["healer", "sensor pod", "arm calibration"],
    sunday: [
      "Ishaan: device app, fake hardware and 18 tests in his checkout, not pushed (B1). Healer: qwen3.5:4b with thinking on, harness h3 (10 of 12).",
      "Olir: sensor pod firmware, podd with 38 heal tools, and six known-bad releases, on main (#20).",
      "Andre: calibrating the arm, with teleop, recording and motion primitives on his branch arm/teleop-recording. The arm has to be on the bench by Sun 9 AM for F2 and F3 on real hardware (Kent's plan).",
    ],
    stretch: ["The Jetson as a bigger local model (Ishaan is connecting it)", "An LLM agent loop for the pod healer (HARNESS.md)"],
    needs: ["device/ in the repo with its tests (B1)", "Contracts review (I0: B2, B3)", "The K17 safety and visor path list", "A LAN path to the Jetson"],
    overlaps: "Two healers, one demo story: Olir's pod tools and Ishaan's device healer are separate code (inferred).",
  },
  {
    name: "Romulus cloud",
    layer: "heal",
    who: ["K"],
    owner: "Kent",
    status: "tentative",
    pills: ["incident timeline", "replay + canary", "Fleet Doctor"],
    sunday: [
      "Merged: M0–M7 and M8a (#1–#19). 82 flow tests pass; F1–F8 and F10 work on fake devices.",
      "In review: M8b (#28), the edge bridge from podd to the cloud (#25, D29), the Fleet Doctor foundation behind doctor_mode=off (#27). Merged: B8's STM32 toolchain (#24) and the Fleet Doctor contracts (#26).",
      "Next: the first live Naïve card, the overnight launch (~12:30 AM), then code freeze Sun 10 AM.",
    ],
    stretch: ["Fleet Doctor L1 advise and L2 consult (off by default)", "Fleet Doctor L3 live sessions (D27, deferred)"],
    needs: ["Device code in the repo (B1)", "K17 safety and visor paths (the Fleet Doctor waits on them too, D25)", "A canary that runs real device code (B7)"],
    overlaps: "B7's canary needs a sim twin running the device stack, and the sim box is still unclaimed.",
  },
  {
    name: "Sim + control",
    layer: "sim",
    who: ["J", "C"],
    owner: "James + Claude · no sim running yet",
    status: "proposed",
    pills: ["sim twin", "canary device", "benchmark"],
    sunday: [
      "Done instead: the Naïve console and fault lab (pi vs Hermes), and the Self-Healing Arm Briefing (MuJoCo vs Isaac, GR00T).",
      "James's plan: arm prototypes tonight without the real arm (a MuJoCo twin, Kent's fake device, Olir's sensor pod); the red follower arm plugs in tomorrow for tests and data.",
      "Pitch only: a fault factory of 1,000 sim copies to train a level-3 model (inferred).",
    ],
    stretch: ["Self-reset retry loop", "A trained VLA, if the team allows training"],
    needs: ["Pick MuJoCo (runs on a Mac) or Isaac on RunPod", "RunPod budget ($75 cap)"],
    overlaps: "Kent's B7 needs exactly this sim twin. The motor layer is still open: scripted skills, SmolVLA or Instant Policy.",
  },
];

export const ROMULUS_RULES: string[] = [
  "The company sells uptime. One loop at three scales: arm, fleet, company.",
  "The model never sits in the control or safety loop. Reflexes are deterministic; the model proposes typed, reversible actions such as rollback(model, rev), set_mode(safe) and restart(proc), and a checker approves them (inferred).",
  "Each action type earns autonomy (suggest → approve → auto) and loses it after a bad fix (inferred).",
  "Get the company loop live tonight; a morning count of agent vs human actions answers \"how much runs without you\" (inferred).",
  "Pitch: \"PagerDuty with hands\" (inferred). Formant F3 only recommends fixes; Alloy raised $8M in Aug 2026 to diagnose.",
  "Venue Wi-Fi sinks robot demos: bring a router, an Ethernet cable and a recorded backup.",
];

export const PRD: { objective: string; requirements: string[]; apis: string[] } = {
  objective: "A general-purpose platform for managing the software and operations of robotic systems. Narrowed since (D2): the cloud half of the loop, for our own arms.",
  requirements: [
    "CI/CD for firmware and the self-healing agent, with a visor-like layer on the device that pulls and validates updates.",
    "Firmware pushes logs and environment data to the cloud for batch analysis.",
    "Cloud batch analysis creates new firmware, harness and model versions.",
    "Mobilerun phone calls to order parts or book technicians.",
  ],
  apis: ["CRUD on firmware", "CRUD on the self-healing harness (embedded SRE)", "Push logs from edge to cloud", "Batch jobs that update the harness"],
};

export const TEAM_SRC: Src[] = [
  repo("CLAUDE.md"),
  repo("features.txt", "features.txt"),
  ext("PR #21: build status", `${REPO}/pull/21`),
  repo("docs/fleet-doctor/README.md", "Fleet Doctor"),
  repo("firmware/docs/HARNESS.md", "HARNESS.md"),
  repo("docs/healer-benchmark-progress.md", "healer benchmark"),
  repo("docs/pi-vs-hermes.md", "pi vs Hermes"),
  repo("docs/project-overview.md", "project overview"),
];

export interface Question {
  q: string;
  who: string;
  status: Status;
}

export const OPEN: Question[] = [
  { q: "Who builds the pod healer's LLM agent loop and blind eval? HARNESS.md says \"you build this\": Ishaan's device healer, a Hermes plugin, or new work?", who: "team", status: "open" },
  { q: "Two healers, one demo story: Olir's pod healer and Ishaan's device healer are separate code (inferred).", who: "Olir, Ishaan", status: "open" },
  { q: "Is the ultrasonic read through the STM32 pod or through Pi GPIO?", who: "Olir, Ishaan", status: "open" },
  { q: "Should pi (Naïve's harness) drive the arm at all, or only Hermes on the bench? In the fault lab both fixed 12 of 12, and Hermes was faster.", who: "team", status: "open" },
  { q: "Triage gaps: no rule catches a frozen ultrasonic, and the health check doesn't test that the arm lifts, so a wrong D2 fix passes it.", who: "Olir, Ishaan", status: "open" },
  { q: "Owners for the unclaimed lines: the fleet-ops agent, the Hermes arm plugin, the Mobilerun key, Stripe payment, the AgentMail email.", who: "team", status: "open" },
  { q: "Stripe direction: bill the robot owner, or pay the supplier? And what is Monid's job?", who: "team", status: "open" },
  { q: "The go-ahead for the first live Naïve card, before the overnight launch (~12:30 AM).", who: "Kent", status: "open" },
  { q: "Sim for tomorrow: a twin beside the real arm, or the code canary (B7)? Motor layer: scripted skills, SmolVLA or Instant Policy?", who: "James, team", status: "open" },
  { q: "A Naïve key with Board scopes for James.", who: "Kent", status: "open" },
  { q: "When is the arm calibrated and ready for the real-arm run (Sun 9 AM, F2 and F3)? And do Andre's recorded motion primitives become the commander's skill menu?", who: "Andre, James", status: "open" },
  { q: "Can one company win Main plus a sponsor track? AgentMail and Monid bearer auth?", who: "ask organizers", status: "open" },
];
