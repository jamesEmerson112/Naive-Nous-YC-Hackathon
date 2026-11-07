import { repo, type Layer, type Src, type Status } from "./meta";
import type { IconName } from "../viz";

export interface Person {
  who: string;
  initial: string;
  layer: Layer;
  doing: string;
  synced: string;
}

export const PEOPLE: Person[] = [
  { who: "James", initial: "J", layer: "sim", doing: "Stack research, this map, sim + control", synced: "2:48 PM" },
  { who: "Kent", initial: "K", layer: "heal", doing: "Romulus self-healing + cloud-platform PRD", synced: "2:15 PM" },
  { who: "Olir", initial: "O", layer: "edge", doing: "firmware branch (placeholder so far)", synced: "2:48 PM" },
  { who: "Ishaan", initial: "I", layer: "neutral", doing: "no status yet", synced: "—" },
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
    owner: "unassigned · Ishaan or Andre?",
    status: "open",
    pills: ["intake", "board → Stripe", "runs overnight"],
    sunday: ["The company itself, built with the required Naïve SDK.", "Intake (inbox or form) → planner → board card → Stripe → digest.", "Runs overnight with budgets: the morning shows what ran without us."],
    stretch: ["Storefront app", "CRM for customers", "AgentMail customer updates"],
    needs: ["The bench ↔ cloud bridge"],
    overlaps: "None yet. This is what the Main track judges.",
  },
  {
    name: "Bench control: Hermes plugin",
    layer: "hermes",
    who: ["?"],
    owner: "unassigned",
    status: "open",
    pills: ["arm plugin", "guard hook", "phone alerts"],
    sunday: ["Arm tools in a plugin, a pre_tool_call guard, a post_tool_call log.", "Cron pulls the next card; the gateway sends alerts to a phone."],
    stretch: ["Webhook that wakes a diagnosis job", "Skills for calibration and pick-and-place", "Catalog PR, video and form by Wed 2 PM PST"],
    needs: ["Arm host API", "A model with ≥ 64K context that reads images"],
    overlaps: "Its guard and Romulus's checker should share one rule set.",
  },
  {
    name: "Edge firmware",
    layer: "edge",
    who: ["O"],
    owner: "Olir (tentative)",
    status: "tentative",
    pills: ["bus owner", "telemetry", "update visor"],
    sunday: ["The program on the arm's computer: owns the servo bus, streams telemetry, runs reflexes.", "A visor layer that pulls and validates updates (PRD)."],
    stretch: ["Move from the laptop to a Pi 5 as a system service"],
    needs: ["LeRobot 0.7.0 on the device (Python ≥ 3.12)", "A telemetry schema agreed with Romulus"],
    overlaps: "Same ground as our arm-host draft. Who builds it is open.",
  },
  {
    name: "Romulus + cloud platform",
    layer: "heal",
    who: ["K"],
    owner: "Kent",
    status: "tentative",
    pills: ["detect → heal", "root cause", "incident email"],
    sunday: ["Arm loop: detect → diagnose → act → verify → learn.", "Fleet Doctor root cause; AgentMail incident report.", "Autonomy ladder shown live, including a demotion."],
    stretch: ["Fleet canary and auto rollback", "Cloud log ingest and batch analysis", "Dashboard; Mobilerun parts call"],
    needs: ["Telemetry and typed actions from the edge", "A diagnosis model (Kent leans RunPod serverless)"],
    overlaps: "Our fault-taxonomy draft. Kent's version wins where they differ.",
  },
  {
    name: "Sim + control",
    layer: "sim",
    who: ["J", "C"],
    owner: "James + Claude",
    status: "proposed",
    pills: ["canary gate", "fault drills", "VLA vs LLM"],
    sunday: ["NVIDIA sim on RunPod as a canary gate for policy versions, and as fault drills.", "Benchmark: VLA vs LLM-chosen steps, OpenAI vs an open model."],
    stretch: ["Synthetic demos from LeIsaac", "Self-reset retry loop", "SmolVLA on the real arm"],
    needs: ["The arm host for real runs", "RunPod budget ($75 cap)"],
    overlaps: "Feeds Kent's \"push a bad policy\" demo with the canary gate.",
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

export const HERO_STEPS: { icon: IconName; title: string; sub: string }[] = [
  { icon: "eye", title: "Fault", sub: "camera covered or bad policy" },
  { icon: "shield", title: "Reflex", sub: "safe pose or rollback" },
  { icon: "doc", title: "Root cause", sub: "Fleet Doctor" },
  { icon: "mail", title: "Incident email", sub: "AgentMail" },
  { icon: "phone", title: "Parts call", sub: "Mobilerun, if hardware" },
];

export const PRD: { objective: string; requirements: string[]; apis: string[] } = {
  objective: "A general-purpose platform for managing the software and operations of robotic systems.",
  requirements: [
    "CI/CD for firmware and the self-healing agent, with a visor-like layer on the device that pulls and validates updates.",
    "Firmware pushes logs and environment data to the cloud for batch analysis.",
    "Cloud batch analysis creates new firmware, harness and model versions.",
    "Mobilerun phone calls to order parts or book technicians.",
  ],
  apis: ["CRUD on firmware", "CRUD on the self-healing harness (embedded SRE)", "Push logs from edge to cloud", "Batch jobs that update the harness"],
};

export const TEAM_SRC: Src[] = [repo("CLAUDE.md"), repo("docs/cloud-platform-planning/cloud-platform-prd.md", "PRD"), repo("docs/cloud-platform-planning/cloud-platform-apis.md", "APIs")];

export interface Question {
  q: string;
  who: string;
  status: Status;
}

export const OPEN: Question[] = [
  { q: "How does the bench talk to the clouds with pure APIs: connect out (pull), or a tunnel in?", who: "James + Claude, next", status: "open" },
  { q: "Who builds the arm host: Olir's firmware lane or ours?", who: "Olir, James", status: "open" },
  { q: "Whose fault taxonomy and telemetry schema is canonical?", who: "Kent, James", status: "open" },
  { q: "Who owns the business layer and the Hermes plugin?", who: "Ishaan, Andre", status: "open" },
  { q: "Where does Hermes run: the bench machine, or a RunPod pod through a tunnel?", who: "team", status: "open" },
  { q: "Which model does Hermes call, serverless or a pod? (≥ 64K context, reads images)", who: "Kent, James", status: "open" },
  { q: "Which GPU for NVIDIA sim? Proposed: RTX A6000 at $0.33/h.", who: "James", status: "proposed" },
  { q: "AgentMail and Monid MCP auth, and can one company win Main plus a sponsor track?", who: "ask organizers", status: "open" },
];
