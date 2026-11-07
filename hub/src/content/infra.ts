import { ext, hermesDoc, local, naiveDoc, repo, type Layer, type Src, type Status } from "./meta";
import type { IconName } from "../viz";

export interface Item {
  label: string;
  layer: Layer;
}

export interface Tier {
  name: string;
  layer: Layer;
  icon: IconName;
  tag?: string;
  why: string;
  where: string;
  items: Item[];
}

/** Naïve vs RunPod, in James's words: the office and the machine room. */
export const OFFICE_VS_ROOM: { row: string; naive: string; runpod: string }[] = [
  { row: "What it is", naive: "Agents that run a company", runpod: "Rented GPUs" },
  { row: "You give it", naive: "Agent configs: role, prompt, tools, budget", runpod: "A container or model to run" },
  { row: "It gives you", naive: "Email customers, run the board, take payments, plan", runpod: "Serve an LLM, run Isaac Sim, train a policy" },
  { row: "Built in", naive: "Board, personas, vault, CRM, apps, approvals", runpod: "Pods, serverless endpoints, storage" },
  { row: "Billing", naive: "Per agent call, budget cap; $250 credit each", runpod: "Per GPU-hour or request; no credits" },
];

/** Apex first. Read bottom-up: each tier depends on the ones below it. */
export const PYRAMID: Tier[] = [
  {
    name: "The company",
    layer: "naive",
    icon: "building",
    tag: "the office · Naïve",
    why: "What the judges score: how much of the company runs without you. It sells arm jobs and uptime.",
    where: "Naïve cloud",
    items: [
      { label: "Naïve SDK (required)", layer: "naive" },
      { label: "board + cards", layer: "naive" },
      { label: "persona inbox + phone", layer: "naive" },
      { label: "Stripe", layer: "neutral" },
      { label: "AgentMail", layer: "neutral" },
      { label: "Mobilerun", layer: "neutral" },
    ],
  },
  {
    name: "Self-healing and fleet ops",
    layer: "heal",
    icon: "shield",
    why: "Romulus: detect → diagnose → act → verify → learn. The model proposes typed, reversible actions; reflexes act.",
    where: "bench reflexes + cloud · Kent, tentative",
    items: [
      { label: "bench reflexes", layer: "heal" },
      { label: "Fleet Doctor", layer: "heal" },
      { label: "canary + rollback", layer: "heal" },
      { label: "OTA versions (PRD)", layer: "heal" },
      { label: "autonomy ladder", layer: "heal" },
    ],
  },
  {
    name: "Agent harnesses: the hosts' stacks",
    layer: "hermes",
    icon: "plug",
    tag: "the hands · Hermes",
    why: "Naïve runs the company's agents in the cloud. Hermes runs at the bench and owns the arm tools.",
    where: "Naïve cloud · bench computer",
    items: [
      { label: "Naïve agents · pi harness", layer: "naive" },
      { label: "Hermes Agent", layer: "hermes" },
      { label: "arm plugin", layer: "hermes" },
      { label: "pre_tool_call guard", layer: "hermes" },
      { label: "cron 60 s · webhooks :8644", layer: "hermes" },
      { label: "gateway → phone", layer: "hermes" },
    ],
  },
  {
    name: "Models and control policies",
    layer: "sim",
    icon: "eye",
    why: "Who picks the next move: an LLM chooses steps, a VLA policy drives the motion. A benchmark decides the mix.",
    where: "RunPod GPUs · small policies on the bench",
    items: [
      { label: "OpenAI (credits)", layer: "compute" },
      { label: "Qwen3-VL-8B on vLLM", layer: "compute" },
      { label: "SmolVLA", layer: "sim" },
      { label: "ACT", layer: "sim" },
      { label: "Robometer-4B success check", layer: "sim" },
    ],
  },
  {
    name: "Robot runtime and simulators",
    layer: "edge",
    icon: "cube",
    why: "Code that talks to the servos, and twins of the arm to test against before the real one moves.",
    where: "bench laptop → Pi 5 · RunPod for Isaac",
    items: [
      { label: "LeRobot 0.7.0", layer: "edge" },
      { label: "arm host (one bus owner)", layer: "edge" },
      { label: "Feetech bus via rustypot", layer: "edge" },
      { label: "Isaac Sim 5.1 + Isaac Lab 2.3", layer: "sim" },
      { label: "LeIsaac SO-101 tasks", layer: "sim" },
      { label: "MuJoCo + Menagerie SO-101", layer: "sim" },
    ],
  },
  {
    name: "Foundation: metal and GPUs",
    layer: "compute",
    icon: "gpu",
    tag: "the machine room · RunPod",
    why: "The physical arm on the bench, and rented GPUs for anything heavy. Nothing runs on James's laptop.",
    where: "bench · RunPod",
    items: [
      { label: "SO-101 · 6× STS3215", layer: "edge" },
      { label: "USB camera", layer: "edge" },
      { label: "laptop → Raspberry Pi 5", layer: "edge" },
      { label: "RunPod RTX A6000 / L40S / 4090", layer: "compute" },
      { label: "no A100/H100 for Isaac", layer: "compute" },
    ],
  },
];

export const PYRAMID_SRC: Src[] = [
  repo("CLAUDE.md", "CLAUDE.md (team + Romulus)"),
  repo("docs/cloud-platform-planning/cloud-platform-prd.md", "Kent's PRD"),
  ext("LeRobot docs", "https://huggingface.co/docs/lerobot"),
  ext("Isaac Sim requirements", "https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html"),
];

export interface RackRow {
  u: string;
  name: string;
  layer: Layer;
  where: string;
  body: string;
  chips: string[];
  ports: string[];
  flowBelow?: string;
}

/** Top (business, cloud) to bottom (metal, bench). */
export const RACK: RackRow[] = [
  {
    u: "L4",
    name: "Company",
    layer: "naive",
    where: "Naïve cloud",
    body: "The business runs as Naïve agents: intake, planning, money, customer contact.",
    chips: ["agents · pi harness", "coordinator + team", "persona inbox + phone", "vault", "apps / storefront", "CRM", "deployments (cron)", "budgets", "digest + events"],
    ports: ["REST api.vetta.sh/v1", "Bearer key", "SDK @usenaive-sdk/managed-agents", "MCP: public HTTPS only"],
    flowBelow: "jobs ↓ · results, digest ↑",
  },
  {
    u: "L3",
    name: "Job queue",
    layer: "naive",
    where: "Naïve board",
    body: "One card per arm job: todo → doing → blocked or done. The bench pulls cards (proposed), or Naïve calls in through a tunnel (open).",
    chips: ["card per job", "status = headline", "card comments = summary"],
    ports: ["pull: REST poll, no inbound port", "push: tunnel + MCP (open)"],
    flowBelow: "card pull ↓ · comments + status ↑",
  },
  {
    u: "L2",
    name: "Bench agent",
    layer: "hermes",
    where: "Hermes on the bench computer",
    body: "Hermes takes the card, decides each step with its model, and calls the arm through the plugin. Its guard checks every call.",
    chips: ["arm plugin tools", "pre_tool_call guard", "post_tool_call log", "cron pulls cards", "webhooks", "gateway alerts", "profiles", "skills"],
    ports: ["webhooks :8644 · HMAC", "cron tick 60 s", "approvals in cron: deny", "model context ≥ 64K"],
    flowBelow: "typed tool calls ↓ · status + events ↑",
  },
  {
    u: "L1",
    name: "Arm host",
    layer: "edge",
    where: "laptop now → Pi 5 later",
    body: "One process owns the servo bus, since only one can open the port. It polls servo health, runs fast reflexes and exposes typed actions. Proposed; owner open.",
    chips: ["LeRobot SO101Follower", "telemetry JSONL ~10 Hz", "reflexes: hold, safe pose", "set_mode · rollback · restart"],
    ports: ["HTTP 127.0.0.1:8765 (proposed)", "control loop 30 Hz", "Python ≥ 3.12"],
    flowBelow: "joint goals ↓ · position, temp, load, status ↑",
  },
  {
    u: "L0",
    name: "Metal",
    layer: "edge",
    where: "the bench",
    body: "The red SO-101 follower, its white leader arm, a camera and a physical e-stop.",
    chips: ["SO-101 follower", "6× Feetech STS3215", "USB bus adapter", "USB camera", "leader arm", "e-stop"],
    ports: ["servo bus 1 Mbps", "camera 640×480 @ 30 fps"],
  },
];

export const RACK_SRC: Src[] = [
  local("docs/robotic-arm.html (draft, not committed)"),
  repo("docs/setup-diagrams.html"),
  naiveDoc("team/board"),
  hermesDoc("developer-guide/plugins"),
  ext("LeRobot SO-101", "https://huggingface.co/docs/lerobot/so101"),
];

export interface Beside {
  title: string;
  layer: Layer;
  status: Status;
  lines: string[];
  src: Src[];
}

export const BESIDE: Beside[] = [
  {
    title: "RunPod compute",
    layer: "compute",
    status: "proposed",
    lines: [
      "vLLM endpoint: the model Hermes calls (custom provider) and the benchmark LLM.",
      "Isaac Sim pod: needs RT cores (RTX A6000 / L40S / 4090; not A100 or H100).",
      "Training pod: ACT and SmolVLA.",
      "Policy server: LeRobot remote inference over Zenoh :7447, through an SSH tunnel.",
      "Not a sponsor, so no credits. James's cap: $75.",
    ],
    src: [ext("Isaac Sim requirements", "https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html"), ext("LeRobot remote inference", "https://huggingface.co/docs/lerobot/main/en/remote_inference")],
  },
  {
    title: "Simulation",
    layer: "sim",
    status: "proposed",
    lines: [
      "Isaac Sim 5.1 + Isaac Lab 2.3 + LeIsaac, headless on RunPod.",
      "No live WebRTC view: RunPod forwards no inbound UDP. Record video instead.",
      "MuJoCo + Menagerie SO-101 as the fast twin that runs anywhere.",
    ],
    src: [ext("LeIsaac", "https://github.com/LightwheelAI/leisaac"), ext("RunPod UDP note", "https://forums.developer.nvidia.com/t/running-isaac-sim-in-a-browser-on-tcp-only-clouds-runpod-webrtc-livestream-cant-work-heres-a-novnc-approach-that-does/376664")],
  },
  {
    title: "Romulus self-healing",
    layer: "heal",
    status: "tentative",
    lines: [
      "Bench reflexes act first: safe pose, rollback.",
      "Fleet Doctor writes the root cause.",
      "AgentMail sends the incident report; Mobilerun phones in parts for hardware faults.",
      "Each action type earns autonomy: suggest → approve → auto.",
    ],
    src: [repo("CLAUDE.md", "CLAUDE.md · Romulus (Kent)")],
  },
  {
    title: "Cloud platform",
    layer: "heal",
    status: "tentative",
    lines: [
      "CI/CD for firmware and the self-healing harness; the device pulls and validates updates.",
      "Edge logs pushed to the cloud for batch analysis.",
      "Dashboard: operate robots, order parts, dispatch technicians.",
    ],
    src: [repo("docs/cloud-platform-planning/cloud-platform-prd.md", "Kent's PRD")],
  },
];

export interface Placement {
  what: string;
  where: string;
  lane: string;
  layer: Layer;
  status: Status;
  note: string;
}

export const PLACEMENTS: Placement[] = [
  { what: "Company agents (planner, ops, outreach)", where: "Naïve cloud micro-VMs", lane: "Business", layer: "naive", status: "proposed", note: "pi harness keeps approvals and streaming" },
  { what: "Board and arm job cards", where: "Naïve cloud", lane: "Business", layer: "naive", status: "proposed", note: "the meeting point between company and bench" },
  { what: "Storefront / order page", where: "Naïve app", lane: "Business", layer: "naive", status: "open", note: "or an email intake on the persona inbox" },
  { what: "Hermes + arm plugin", where: "bench laptop → Pi 5", lane: "Bench control", layer: "hermes", status: "open", note: "alternative: a RunPod pod reaching the bench through a tunnel" },
  { what: "Arm host (bus owner, telemetry, reflexes)", where: "bench laptop → Pi 5", lane: "Edge firmware or ours", layer: "edge", status: "open", note: "only one process can open the serial port" },
  { what: "Model Hermes calls", where: "RunPod vLLM pod or serverless", lane: "Compute", layer: "compute", status: "open", note: "Kent leans serverless; needs ≥ 64K context and vision" },
  { what: "Fleet Doctor diagnosis", where: "Naïve agent or RunPod", lane: "Romulus", layer: "heal", status: "tentative", note: "not latency-critical: the LLM is outside the control loop" },
  { what: "Isaac Sim + LeIsaac", where: "RunPod RTX pod", lane: "Sim + control", layer: "sim", status: "proposed", note: "headless; record video" },
  { what: "Policy training (ACT, SmolVLA)", where: "RunPod pod", lane: "Sim + control", layer: "sim", status: "proposed", note: "SmolVLA 10–16 GB, ACT 2–6 GB" },
  { what: "Policy inference", where: "bench in-process, or RunPod policy server", lane: "Sim + control", layer: "sim", status: "open", note: "venue Wi-Fi favors running on the bench" },
  { what: "Alerts", where: "Hermes gateway → phone; AgentMail; Mobilerun", lane: "Romulus / Business", layer: "heal", status: "proposed", note: "the alarm path skips the queue" },
  { what: "Logs, OTA versions, dashboard", where: "cloud platform", lane: "Romulus", layer: "heal", status: "tentative", note: "Kent's PRD" },
];
