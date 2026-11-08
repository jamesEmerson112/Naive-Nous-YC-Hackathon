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
      { label: "work orders", layer: "naive" },
      { label: "Stripe", layer: "neutral" },
      { label: "Mobilerun", layer: "neutral" },
    ],
  },
  {
    name: "Self-healing",
    layer: "heal",
    icon: "shield",
    why: "The device heals first (park, fix, verify). Romulus locks good fixes into versions: replay, one canary, auto promote or rollback.",
    where: "Pi 5 healer + Romulus cloud · Kent",
    items: [
      { label: "healer on the Pi", layer: "edge" },
      { label: "overlays + visor", layer: "edge" },
      { label: "replay + canary", layer: "heal" },
      { label: "kill switch", layer: "heal" },
      { label: "incident timeline", layer: "heal" },
    ],
  },
  {
    name: "Agent harnesses: the hosts' stacks",
    layer: "hermes",
    icon: "plug",
    tag: "the hands · Hermes",
    why: "Naïve runs the company's agents in the cloud. Hermes runs at the bench and owns the arm tools.",
    where: "Naïve cloud · bench",
    items: [
      { label: "Naïve agents · pi harness", layer: "naive" },
      { label: "Hermes Agent", layer: "hermes" },
      { label: "arm plugin", layer: "hermes" },
      { label: "pre_tool_call guard", layer: "hermes" },
      { label: "cron 60 s · webhooks :8644", layer: "hermes" },
    ],
  },
  {
    name: "Models and policies",
    layer: "sim",
    icon: "eye",
    why: "A small local model on the Pi for the healer; bigger models on RunPod or an API; a VLA only if the team allows training.",
    where: "Pi 5 · RunPod · OpenAI",
    items: [
      { label: "local model (llama.cpp)", layer: "edge" },
      { label: "Qwen3-VL-8B on vLLM", layer: "compute" },
      { label: "OpenAI (credits)", layer: "compute" },
      { label: "SmolVLA / ACT (if training)", layer: "sim" },
    ],
  },
  {
    name: "Runtime and simulators",
    layer: "edge",
    icon: "cube",
    why: "Code that talks to the servos and sensors, and twins of the arm to test against before the real one moves.",
    where: "Pi 5 · STM32 · RunPod for Isaac",
    items: [
      { label: "Pi app (Python)", layer: "edge" },
      { label: "LeRobot 0.7.0", layer: "edge" },
      { label: "bare-metal C on STM32", layer: "edge" },
      { label: "Romulus: FastAPI + SQLite", layer: "heal" },
      { label: "Isaac Sim + LeIsaac", layer: "sim" },
      { label: "MuJoCo", layer: "sim" },
    ],
  },
  {
    name: "Foundation: metal and GPUs",
    layer: "compute",
    icon: "gpu",
    tag: "the machine room · RunPod",
    why: "The arm and its sensors on the bench, and rented GPUs for anything heavy.",
    where: "bench · RunPod",
    items: [
      { label: "SO-101 · 6× STS3215", layer: "edge" },
      { label: "Raspberry Pi 5", layer: "edge" },
      { label: "STM32 Nucleo-F042K6", layer: "edge" },
      { label: "HC-SR04 · USB camera", layer: "edge" },
      { label: "RunPod GPUs", layer: "compute" },
      { label: "Jetson Xavier (later)", layer: "compute" },
    ],
  },
];

export const PYRAMID_SRC: Src[] = [
  repo("docs/project-overview.md", "project overview"),
  repo("docs/cloud-platform-planning/cloud-platform-v0-plan.md", "Kent's v0 plan"),
  ext("LeRobot docs", "https://huggingface.co/docs/lerobot"),
  ext("Isaac Sim requirements", "https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html"),
];

export interface RackRow {
  u: string;
  name: string;
  layer: Layer;
  where: string;
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
    chips: ["agents · pi harness", "coordinator + team", "persona inbox + phone", "vault", "apps", "CRM", "budgets", "work-order agent"],
    ports: ["REST api.vetta.sh/v1", "Bearer key", "MCP: public HTTPS only"],
    flowBelow: "jobs + work orders ↓ · results ↑",
  },
  {
    u: "L3",
    name: "Romulus + board",
    layer: "heal",
    where: "Kent's server · Naïve board",
    chips: ["version registry", "device API", "batch job", "replay + canary", "kill switch", "incident timeline", "board cards"],
    ports: ["FastAPI + SQLite (bench LAN)", "device pulls, ~10 s check-in", "POST /v1/boards/{agt}/cards"],
    flowBelow: "versions + cards ↓ · incidents + overlays ↑",
  },
  {
    u: "L2",
    name: "Bench agent",
    layer: "hermes",
    where: "Hermes on the bench",
    chips: ["arm plugin tools", "pre_tool_call guard", "post_tool_call log", "cron pulls cards", "gateway alerts"],
    ports: ["webhooks :8644 · HMAC", "cron tick 60 s", "approvals in cron: deny", "model context ≥ 64K"],
    flowBelow: "tool calls ↓ · status ↑",
  },
  {
    u: "L1",
    name: "Edge computer",
    layer: "edge",
    where: "Raspberry Pi 5 + STM32",
    chips: ["Pi app", "healer + local model", "visor", "overlays", "LeRobot SO101Follower", "STM32 C firmware"],
    ports: ["control loop 30 Hz", "Python ≥ 3.12", "ST-LINK VCP 115200"],
    flowBelow: "joint goals ↓ · position, temp, load, distance ↑",
  },
  {
    u: "L0",
    name: "Metal",
    layer: "edge",
    where: "the bench",
    chips: ["SO-101 follower", "6× STS3215", "HC-SR04 ultrasonic", "USB camera 720p", "STM32 Nucleo", "e-stop"],
    ports: ["servo bus 1 Mbps", "HC-SR04 2–400 cm", "5 V / 5 A for the Pi"],
  },
];

export const RACK_SRC: Src[] = [
  repo("docs/project-overview.md", "project overview §4–5"),
  repo("docs/cloud-platform-planning/cloud-platform-v0-plan.md", "v0 plan"),
  local("docs/robotic-arm.html (draft, not committed)"),
  naiveDoc("team/board"),
  hermesDoc("developer-guide/plugins"),
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
    lines: ["vLLM endpoint for Hermes and the benchmark.", "Isaac Sim pod: RT cores needed (no A100/H100).", "Training pod, only if training is allowed."],
    src: [ext("Isaac Sim requirements", "https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html")],
  },
  {
    title: "Simulation",
    layer: "sim",
    status: "proposed",
    lines: ["Isaac Sim + LeIsaac, headless on RunPod.", "Could be Romulus's canary device.", "MuJoCo as the fast twin."],
    src: [ext("LeIsaac", "https://github.com/LightwheelAI/leisaac")],
  },
  {
    title: "Romulus (Kent's v0)",
    layer: "heal",
    status: "tentative",
    lines: ["Incidents in; held fixes locked into versions.", "Replay, one canary, auto promote or rollback.", "Kill switch is the only human control."],
    src: [repo("docs/cloud-platform-planning/cloud-platform-v0-plan.md", "v0 plan")],
  },
  {
    title: "Work orders",
    layer: "naive",
    status: "tentative",
    lines: ["Unresolved hardware fault → work order.", "Becomes a Naïve board card.", "A Naïve agent phones in the part via Mobilerun."],
    src: [repo("docs/cloud-platform-planning/cloud-platform-decisions.md", "D12")],
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
  { what: "Company agents + board", where: "Naïve cloud", lane: "Business", layer: "naive", status: "proposed", note: "pi harness keeps approvals" },
  { what: "Work-order agent", where: "Naïve agent + Mobilerun MCP", lane: "Business", layer: "naive", status: "open", note: "caps live in our code (D19b); no fleet-ops agent yet" },
  { what: "Romulus server", where: "Kent's laptop, bench LAN", lane: "Romulus", layer: "heal", status: "decided", note: "FastAPI + Postgres 17 + htmx; M0–M8a merged" },
  { what: "Hermes + arm plugin", where: "James's Mac for now", lane: "Bench control", layer: "hermes", status: "tentative", note: "Hermes runs with Naïve's MCP; the plugin isn't started" },
  { what: "Pi app + healer + visor", where: "Raspberry Pi 5", lane: "Device (Ishaan)", layer: "edge", status: "tentative", note: "code not pushed yet (B1); one serial-port owner" },
  { what: "STM32 sensor pod", where: "Nucleo-F042K6", lane: "Device (Olir)", layer: "edge", status: "tentative", note: "HC-SR04 + LIS3DH; podd + heal tools (#20)" },
  { what: "Local model", where: "Pi 5, later the Jetson", lane: "Device", layer: "edge", status: "tentative", note: "Ollama · qwen3.5:4b, thinking on (Ishaan's pick)" },
  { what: "Batch LLM + coding agent", where: "Naïve model proxy", lane: "Romulus", layer: "compute", status: "decided", note: "gpt-oss-120b, ~1.8 s; RunPod optional (D22)" },
  { what: "Isaac Sim + LeIsaac", where: "RunPod RTX pod", lane: "Sim + control", layer: "sim", status: "proposed", note: "candidate canary device" },
];
