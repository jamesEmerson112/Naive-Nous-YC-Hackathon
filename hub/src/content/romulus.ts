import { repo, type Src, type Status } from "./meta";
import type { IconName } from "../viz";

/** Kent's v0 heal-and-release loop (docs/cloud-platform-planning/cloud-platform-v0-plan.md; D15 pending). */
export const V0_LOOP: { icon: IconName; title: string; sub: string }[] = [
  { icon: "alert", title: "Fault", sub: "something breaks on the arm" },
  { icon: "shield", title: "Overlay patch", sub: "device agent, self-reverting" },
  { icon: "doc", title: "Report", sub: "incident + overlay diff" },
  { icon: "loop", title: "Batch lock-in", sub: "held overlay → candidate" },
  { icon: "cube", title: "Replay + canary", sub: "one sim device, N min" },
  { icon: "gate", title: "Promote / rollback", sub: "automatic, no human gate" },
];

export const V0_SIDE: { icon: IconName; title: string; line: string }[] = [
  { icon: "phone", title: "Hardware fault", line: "Unresolved and hardware → work order → Naïve board card → the fleet-ops agent phones in the part via Mobilerun." },
  { icon: "lock", title: "Kill switch", line: "The only human control: freeze every rollout, pin all devices to last good (D11)." },
  { icon: "eye", title: "One screen", line: "The incident timeline: broke → agent action → overlay → held or reverted → version or work order, each with a why (D10)." },
];

export interface Decision {
  id: string;
  text: string;
  status: Status;
}

export const DECISIONS: Decision[] = [
  { id: "D0", text: "The agent fixes; the batch locks it in as firmware and skills", status: "decided" },
  { id: "D1", text: "User is us, the arm company; single tenant", status: "decided" },
  { id: "D2", text: "Narrow loop: the cloud half of self-healing (refined by D16: narrow loop, general data)", status: "decided" },
  { id: "D3", text: "Firmware = versioned manifest: code rev + rules/params + policy model", status: "decided" },
  { id: "D4", text: "Harness = versioned folder; the device teammate picks the framework", status: "decided" },
  { id: "D5", text: "The on-device agent may patch code or rules, then report", status: "decided" },
  { id: "D5a", text: "Patches are self-reverting overlays. Ishaan now proposes the healer may patch everything, safety layer and visor included; Kent to review", status: "open" },
  { id: "D6", text: "Replay, then canary, then auto promote or rollback; no human gate", status: "decided" },
  { id: "D7", text: "Device: hash check, install, health check, roll back on failure", status: "decided" },
  { id: "D8", text: "Cloud down: run last good, queue reports on disk, never bricked", status: "decided" },
  { id: "D9", text: "Legible, never magical: every step has a readable why", status: "decided" },
  { id: "D10", text: "One screen: the incident timeline", status: "decided" },
  { id: "D11", text: "Humans act only through the kill switch", status: "decided" },
  { id: "D12", text: "Naïve runs parts and technician agents via board cards and Mobilerun", status: "decided" },
  { id: "D13", text: "Cut: twins/ERP, billing per heal, trust ledger, policy retraining", status: "decided" },
  { id: "D14", text: "A teammate (TBD) owns the device side; Kent owns cloud and contracts", status: "open" },
  { id: "D15", text: "The v0 build plan: approved in principle (v2)", status: "decided" },
  { id: "D16", text: "Robot telemetry is a first-class asset: synced, curated, versioned datasets", status: "decided" },
  { id: "D17", text: "Runtime: local Docker containers; Python + FastAPI", status: "decided" },
  { id: "D18", text: "Architecture: Compose, Postgres 17, one image for api + worker, Jinja + htmx", status: "decided" },
  { id: "D19", text: "Naïve integration design, pending", status: "proposed" },
  { id: "D19b", text: "Over the spend cap: (a) block and wait for a person (recommended) or (b) gated agent", status: "open" },
  { id: "D20", text: "Telemetry: LeRobot v3 incident windows, MCAP logs, JSONL traces, schema v0.1", status: "decided" },
  { id: "D21", text: "A coding agent turns code patches into candidates through the gate", status: "decided" },
  { id: "D21a", text: "Where the coding agent sits in the build order", status: "open" },
];

/** Kent's timed build plan (cloud-platform-build-plan.md). Times are PST; Sun = day 2. */
export const SCHEDULE: { id: string; what: string; from: string; to: string; flows?: string }[] = [
  { id: "M0", what: "Scaffold, contracts v0.1, Naïve + LLM spikes", from: "2026-11-08T00:15:00Z", to: "2026-11-08T01:15:00Z" },
  { id: "M1", what: "Walking skeleton", from: "2026-11-08T01:15:00Z", to: "2026-11-08T02:45:00Z", flows: "F1 F2" },
  { id: "M2", what: "Incident timeline page", from: "2026-11-08T02:45:00Z", to: "2026-11-08T03:30:00Z" },
  { id: "M3", what: "Batch, gate, kill switch", from: "2026-11-08T03:30:00Z", to: "2026-11-08T05:30:00Z", flows: "F3 F4 F5" },
  { id: "M4", what: "Telemetry ingest", from: "2026-11-08T05:30:00Z", to: "2026-11-08T06:30:00Z", flows: "F7" },
  { id: "M5", what: "Work orders to Naïve", from: "2026-11-08T06:30:00Z", to: "2026-11-08T07:30:00Z", flows: "F6" },
  { id: "M7", what: "Overnight launch", from: "2026-11-08T07:30:00Z", to: "2026-11-08T08:30:00Z", flows: "F10" },
  { id: "ON", what: "Overnight run, hands off", from: "2026-11-08T08:30:00Z", to: "2026-11-08T15:00:00Z" },
  { id: "AM", what: "Read digest, fix breakage", from: "2026-11-08T15:00:00Z", to: "2026-11-08T15:30:00Z" },
  { id: "M6", what: "Datasets and exports", from: "2026-11-08T15:30:00Z", to: "2026-11-08T17:00:00Z", flows: "F8" },
  { id: "RA", what: "Real arm + polish", from: "2026-11-08T17:00:00Z", to: "2026-11-08T18:00:00Z" },
  { id: "FZ", what: "Code freeze, rehearse ×3", from: "2026-11-08T18:00:00Z", to: "2026-11-08T19:00:00Z" },
];

/** What Kent needs from others, and by when. */
export const NEEDS: { what: string; from: string; by: string }[] = [
  { what: "Device contracts agreed", from: "device teammate (D14)", by: "5:15 PM" },
  { what: "Naïve API key", from: "Naïve owner", by: "5:15 PM" },
  { what: "LLM key or RunPod endpoint", from: "whoever owns models", by: "5:15 PM" },
  { what: "First real check-in", from: "device teammate", by: "9:30 PM" },
  { what: "fleet-ops agent live", from: "Naïve owner", by: "11:30 PM" },
  { what: "Real arm ready", from: "device teammate", by: "Sun 9:00 AM" },
];

/** Flows F1–F10 from cloud-platform-flows.md (the progress tracker). */
export const FLOWS: { id: string; name: string; p: "P0" | "P1" }[] = [
  { id: "F1", name: "Version sync", p: "P0" },
  { id: "F2", name: "Incident → story", p: "P0" },
  { id: "F3", name: "Lock-in to fleet", p: "P0" },
  { id: "F4", name: "Bad candidate rollback", p: "P0" },
  { id: "F5", name: "Kill switch", p: "P0" },
  { id: "F6", name: "Hardware → Naïve work order", p: "P0" },
  { id: "F7", name: "Telemetry sync", p: "P0" },
  { id: "F8", name: "Dataset snapshot + export", p: "P1" },
  { id: "F9", name: "Offline backfill", p: "P1" },
  { id: "F10", name: "Overnight unattended run", p: "P0" },
];

export const DEMO_ORDER: { icon: IconName; title: string; sub: string }[] = [
  { icon: "eye", title: "F2", sub: "judge covers the camera" },
  { icon: "doc", title: "F7", sub: "evidence" },
  { icon: "loop", title: "F3", sub: "fix locked in" },
  { icon: "gate", title: "F4", sub: "bad version rolled back" },
  { icon: "phone", title: "F6", sub: "Naïve + Mobilerun" },
  { icon: "lock", title: "F5", sub: "kill switch" },
  { icon: "chart", title: "F10", sub: "overnight numbers" },
];

/** How a hardware fault becomes a phone order (cloud-platform-naive-integration.md). */
export const WORK_ORDER_FLOW: { icon: IconName; title: string; sub: string }[] = [
  { icon: "alert", title: "Triage", sub: "Romulus: needs a part" },
  { icon: "dollar", title: "Cap check", sub: "our code, not Naïve" },
  { icon: "doc", title: "Post card", sub: "POST /v1/boards/…/cards" },
  { icon: "building", title: "fleet-ops", sub: "Naïve agent starts" },
  { icon: "phone", title: "Mobilerun", sub: "phones in the order" },
  { icon: "gate", title: "Close card", sub: "RESULT: ORDER_REF=…" },
  { icon: "loop", title: "Poll", sub: "every ~15 s → timeline" },
];

export const NAIVE_LIMITS: string[] = [
  "Outbound only: Naïve never calls Romulus, so v0 needs no tunnel.",
  "No webhooks for board cards: poll each card by id.",
  "Naïve policy is allow/ask/deny per tool; it can't cap dollars, so caps live in our code.",
  "Wallet and prepaid cards return 501: how a phone order gets paid is open.",
  "There is no Python SDK (TypeScript only); Romulus calls REST with httpx. Does that count as \"using the Naïve SDK\"? (Q1)",
  "Overnight runs use NAIVE_MODE=fake, so chaos tests never phone a real supplier (K13).",
];

/** What the arm sends (cloud-platform-telemetry.md, pending D20). */
export const STREAMS: { name: string; what: string; size: string }[] = [
  { name: "incident_window", what: "±30 s of joints, servos, camera", size: "LeRobot v3 tar · ≤ 50 MB" },
  { name: "log_window", what: "control logs, servo diag ~100 Hz", size: "MCAP · ≤ 20 MB" },
  { name: "agent_trace", what: "what the healer did", size: "JSONL · ≤ 5 MB" },
  { name: "episode", what: "normal tries, 1 in 5", size: "LeRobot v3 · P1" },
  { name: "metrics", what: "success rate, servo temp, voltage", size: "every check-in" },
];

/** The device side of Kent's API (cloud-platform-apis.md, contract v0.1). */
export const DEVICE_API: [string, string, string][] = [
  ["POST", "/v1/devices/{id}/checkin", "every ~10 s; returns assignments, frozen"],
  ["GET", "/v1/firmware/{version}", "manifest + sha256"],
  ["GET", "/v1/harness/{version}/package", "tarball + X-Sha256"],
  ["POST", "/v1/devices/{id}/installs", "install result"],
  ["POST", "/v1/devices/{id}/incidents", "incident report (idempotent)"],
  ["POST", "/v1/devices/{id}/overlays/{overlay_id}", "overlay held or reverted"],
  ["POST", "/v1/devices/{id}/recordings", "recording metadata"],
  ["PUT", "/v1/recordings/{id}/blob", "upload bytes + X-Sha256"],
];
export const API_META = "Base http://<Kent's laptop LAN IP>:8000 · contract v0.1 · no auth in v0 (bench network only) · the device always initiates · ~40 operator endpoints too.";

export const V0_STACK: string[] = ["Docker Compose on Kent's laptop", "api: FastAPI :8000", "worker: batch · gate · Naïve sync", "Postgres 17", "content-addressed blobs (20 GB)", "Jinja + htmx timeline", "fake devices (profile sim)"];

/** Kent's architecture doc §1–2: who calls whom. */
export const V0_NETWORK: string[] = [
  "Romulus runs on Kent's laptop on the bench network (our travel router), not in a cloud.",
  "Devices always pull; Romulus never calls a device.",
  "Romulus only calls out: Naïve (board cards) and an LLM API. Nothing calls in, so no tunnel.",
];

/** Where our sim + control lane plugs into Kent's open questions. */
export const HOOKS: { q: string; ours: string }[] = [
  { q: "The canary is always a simulated device. Which sim?", ours: "Our NVIDIA sim (Isaac + LeIsaac, or MuJoCo) as canary sim-2." },
  { q: "Batch LLM by 5:15 PM: API key or RunPod endpoint?", ours: "Our RunPod vLLM pod or OpenAI credits." },
  { q: "Is the device agent Hermes (D4)?", ours: "Hermes on the bench is the team's working assumption." },
];

export const ROMULUS_OPEN: string[] = [
  "D14: who owns the device side (embedded agent, overlays, visor)?",
  "D19, D19b, D21a: Naïve integration, the over-cap rule, and when the coding agent gets built.",
  "D5a: may the healer patch the safety layer and visor (Ishaan's answer), or not?",
  "Q1: does calling Naïve's REST API from Python count as using the Naïve SDK?",
  "How does a phone order get paid (wallet returns 501)? Parts catalogue (parts.yaml) is undefined.",
  "Is the arm run by lerobot-rollout or a scripted stack?",
  "Signal and action lists for rules need the device teammate's agreement.",
  "What marks a fault as suspected hardware? When is an overlay \"held\"?",
];

export const ROMULUS_SRC: Src[] = [
  repo("docs/cloud-platform-planning/cloud-platform-build-plan.md", "build plan"),
  repo("docs/cloud-platform-planning/cloud-platform-flows.md", "flows tracker"),
  repo("docs/cloud-platform-planning/cloud-platform-naive-integration.md", "Naïve integration"),
  repo("docs/cloud-platform-planning/cloud-platform-telemetry.md", "telemetry"),
  repo("docs/cloud-platform-planning/cloud-platform-apis.md", "APIs"),
  repo("docs/cloud-platform-planning/cloud-platform-architecture.md", "architecture"),
  repo("docs/cloud-platform-planning/cloud-platform-v0-plan.md", "v0 plan"),
  repo("docs/cloud-platform-planning/cloud-platform-decisions.md", "decision log"),
];
