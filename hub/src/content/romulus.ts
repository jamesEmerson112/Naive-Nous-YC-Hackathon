import { repo, type Src, type Status } from "./meta";
import type { IconName } from "../viz";

/** Kent's v0 heal-and-release loop (docs/cloud-platform-planning/cloud-platform-v0-plan.md; D15 pending). */
export const V0_LOOP: { icon: IconName; title: string; sub: string }[] = [
  { icon: "alert", title: "Fault", sub: "something breaks on the arm" },
  { icon: "shield", title: "Overlay patch", sub: "device agent, self-reverting" },
  { icon: "doc", title: "Report", sub: "incident + overlay diff" },
  { icon: "loop", title: "Batch lock-in", sub: "held overlay → candidate" },
  { icon: "cube", title: "Replay + canary", sub: "one test device, N min" },
  { icon: "gate", title: "Promote / rollback", sub: "automatic, no human gate" },
];

export const V0_SIDE: { icon: IconName; title: string; line: string }[] = [
  { icon: "phone", title: "Hardware fault", line: "Unresolved and hardware → work order → Naïve board card → a Naïve agent phones in the part via Mobilerun (D12)." },
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
  { id: "D2", text: "Narrow to the cloud half of the loop; \"general purpose\" dropped", status: "decided" },
  { id: "D3", text: "Firmware = versioned manifest: code rev + rules/params + policy model", status: "decided" },
  { id: "D4", text: "Harness = versioned folder; the device teammate picks the framework", status: "decided" },
  { id: "D5", text: "The on-device agent may patch code or rules, then report", status: "decided" },
  { id: "D5a", text: "Patches are self-reverting overlays; safety layer and visor can't be patched", status: "decided" },
  { id: "D6", text: "Replay, then canary, then auto promote or rollback; no human gate", status: "decided" },
  { id: "D7", text: "Device: hash check, install, health check, roll back on failure", status: "decided" },
  { id: "D8", text: "Cloud down: run last good, queue reports on disk, never bricked", status: "decided" },
  { id: "D9", text: "Legible, never magical: every step has a readable why", status: "decided" },
  { id: "D10", text: "One screen: the incident timeline", status: "decided" },
  { id: "D11", text: "Humans act only through the kill switch", status: "decided" },
  { id: "D12", text: "Naïve runs parts and technician agents via board cards and Mobilerun", status: "decided" },
  { id: "D13", text: "Cut: twins/ERP, billing per heal, trust ledger, policy retraining", status: "decided" },
  { id: "D14", text: "A teammate (TBD) owns the device side; Kent owns cloud and contracts", status: "open" },
  { id: "D15", text: "The v0 build plan, waiting for approval", status: "proposed" },
];

export const MILESTONES: { id: string; title: string; hours: string; who: string }[] = [
  { id: "M0", title: "Contracts (shared with device side)", hours: "~1 h", who: "Kent + device" },
  { id: "M1", title: "Walking skeleton: registry, device API, event log", hours: "~2 h", who: "Kent" },
  { id: "M2", title: "Incident timeline page", hours: "~1.5 h", who: "Kent" },
  { id: "M3", title: "Batch job: overlay → candidate", hours: "~1.5 h", who: "Kent" },
  { id: "M4", title: "Gate + kill switch", hours: "~2 h", who: "Kent" },
  { id: "M5", title: "Work orders → Naïve → Mobilerun", hours: "~1.5 h", who: "Kent" },
  { id: "M6", title: "Overnight run, counts from events", hours: "~1 h", who: "Kent" },
];

export const V0_STACK: string[] = ["FastAPI on Kent's laptop (bench LAN)", "SQLite", "Jinja + htmx timeline", "fake_device.py test client", "device always pulls"];

/** Where our sim + control lane plugs into Kent's open questions. */
export const HOOKS: { q: string; ours: string }[] = [
  { q: "Which sim or fake device can act as canary?", ours: "Our NVIDIA sim (Isaac + LeIsaac, or MuJoCo) as the canary device." },
  { q: "Which LLM does the batch job use: Claude API or RunPod?", ours: "Our benchmark compares OpenAI and Qwen3-VL on RunPod." },
  { q: "Is the harness Hermes?", ours: "Hermes on the bench is the team's working assumption." },
];

export const ROMULUS_OPEN: string[] = [
  "D14: who owns the device side (embedded agent, overlays, visor)?",
  "D15: approve the v0 plan?",
  "What does the device health check test, and when is an overlay \"held\"?",
  "What marks a fault as suspected hardware?",
  "Overlay code patches in v0: skip, or add a coding agent?",
  "Python + FastAPI + SQLite + htmx: OK?",
  "Which Naïve agent owns work orders; who sets up Mobilerun and its spend cap?",
];

export const ROMULUS_SRC: Src[] = [
  repo("docs/cloud-platform-planning/cloud-platform-v0-plan.md", "Kent's v0 plan"),
  repo("docs/cloud-platform-planning/cloud-platform-decisions.md", "decision log D0–D15"),
];
