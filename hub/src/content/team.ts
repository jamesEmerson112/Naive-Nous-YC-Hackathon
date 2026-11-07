import { repo, type Layer, type Src, type Status } from "./meta";

export interface Person {
  who: string;
  initial: string;
  layer: Layer;
  doing: string;
  synced: string;
}

export const PEOPLE: Person[] = [
  { who: "James", initial: "J", layer: "sim", doing: "This map, SO-101 research, sim + control", synced: "3:22 PM" },
  { who: "Kent", initial: "K", layer: "heal", doing: "Romulus: cloud half of the heal loop; v0 plan + decisions D0–D15", synced: "3:16 PM" },
  { who: "Olir", initial: "O", layer: "edge", doing: "STM32F042K6 firmware boilerplate (firmware branch)", synced: "3:11 PM" },
  { who: "Ishaan", initial: "I", layer: "heal", doing: "Project overview + fault catalog", synced: "3:26 PM" },
  { who: "Andre", initial: "A", layer: "neutral", doing: "no status yet", synced: "—" },
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
    who: ["?"],
    owner: "unassigned",
    status: "open",
    pills: ["intake", "board → Stripe", "work orders"],
    sunday: ["The company itself, built with the required Naïve SDK.", "Intake → planner → board card → Stripe → digest.", "A Naïve agent takes Romulus work orders and phones in parts via Mobilerun (D12)."],
    stretch: ["Storefront app", "CRM for customers", "AgentMail customer updates"],
    needs: ["The bench ↔ cloud bridge", "Mobilerun MCP set up with a spend cap"],
    overlaps: "Kent's M5 posts the work-order cards this lane's agent picks up.",
  },
  {
    name: "Bench control: Hermes plugin",
    layer: "hermes",
    who: ["?"],
    owner: "unassigned",
    status: "open",
    pills: ["arm plugin", "guard hook", "phone alerts"],
    sunday: ["Arm tools in a plugin, a pre_tool_call guard, a post_tool_call log.", "Cron pulls the next card; the gateway sends alerts to a phone."],
    stretch: ["Catalog PR, video and form by Wed 2 PM PST"],
    needs: ["One owner of the arm's serial port", "A model with ≥ 64K context"],
    overlaps: "The healer's harness (pi or mini-swe-agent) is a separate, open choice.",
  },
  {
    name: "Device: Pi 5 + STM32",
    layer: "edge",
    who: ["O", "?"],
    owner: "Olir (STM32) · device side TBD (D14)",
    status: "open",
    pills: ["healer", "visor + overlays", "STM32 firmware"],
    sunday: ["Pi app holds an object; ultrasonic drives the arm.", "Health checks, park, healer fixes and verifies.", "Bare-metal C firmware on the STM32."],
    stretch: ["Jetson Xavier as a bigger local model"],
    needs: ["Kent's device contracts (M0)", "LeRobot 0.7 on Python 3.12"],
    overlaps: "Our earlier arm-host draft; the device side now leads.",
  },
  {
    name: "Romulus cloud",
    layer: "heal",
    who: ["K"],
    owner: "Kent",
    status: "tentative",
    pills: ["incident timeline", "replay + canary", "kill switch"],
    sunday: ["M0–M6 (~10.5 h): contracts, registry, timeline, batch job, gate, work orders, overnight run."],
    stretch: ["Fleet grid and version pipeline screens"],
    needs: ["A device speaking the contracts", "A canary device", "A batch LLM (Claude API or RunPod)"],
    overlaps: "The canary device can be our NVIDIA sim.",
  },
  {
    name: "Sim + control",
    layer: "sim",
    who: ["J", "C"],
    owner: "James + Claude",
    status: "proposed",
    pills: ["API bridge", "canary device", "benchmark"],
    sunday: ["Bench ↔ cloud bridge with plain APIs.", "NVIDIA sim as Romulus's canary device.", "Control benchmark: VLA vs LLM."],
    stretch: ["Self-reset retry loop", "A trained VLA, if the team allows training"],
    needs: ["Kent's device contract", "RunPod budget ($75 cap)"],
    overlaps: "Ishaan's overview says no model training this hackathon. P2 (VLA) needs a call.",
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
  repo("docs/project-overview.md", "project overview"),
  repo("docs/cloud-platform-planning/cloud-platform-v0-plan.md", "v0 plan"),
  repo("features.txt", "features.txt"),
];

export interface Question {
  q: string;
  who: string;
  status: Status;
}

export const OPEN: Question[] = [
  { q: "Bench ↔ clouds with plain APIs. Romulus devices pull every ~10 s; how do the bench, Naïve and RunPod connect?", who: "James + Claude, next", status: "open" },
  { q: "D14: who owns the device side (healer, overlays, visor)?", who: "team", status: "open" },
  { q: "D15: approve Kent's v0 plan?", who: "team", status: "proposed" },
  { q: "Train a model this hackathon? The overview says no; P2 (VLA) needs training.", who: "James, team", status: "open" },
  { q: "What does the STM32 do in the loop, and how does it talk to the Pi?", who: "Olir", status: "open" },
  { q: "Which harness and model run on the Pi (pi or mini-swe-agent; llama.cpp)?", who: "device owner", status: "open" },
  { q: "The v0 batch job skips code patches, but D1 and F1–F11 are code fixes.", who: "Kent, device owner", status: "open" },
  { q: "Which device is the canary? Proposed: our NVIDIA sim.", who: "Kent, James", status: "proposed" },
  { q: "Which LLM for the batch job and cloud diagnosis: Claude API or RunPod?", who: "Kent, James", status: "open" },
  { q: "What does the arm do in the demo? (being designed)", who: "team", status: "open" },
  { q: "Can one company win Main plus a sponsor track? AgentMail and Monid bearer auth?", who: "ask organizers", status: "open" },
];
