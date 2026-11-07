import { repo, type Layer, type Src } from "./meta";
import type { IconName } from "../viz";

export interface Hop {
  who: string;
  title: string;
  layer: Layer;
  icon: IconName;
  line: string;
}

/** One request through the proposed stack (from James's walkthrough; a proposal, not built). */
export const JOURNEY: Hop[] = [
  { who: "You", title: "Ask", layer: "neutral", icon: "mail", line: "Email the company inbox, fill a Naïve app form, or use naive run: \"wave if the weather is good, shake if not\"." },
  { who: "Naïve agent", title: "Decide", layer: "naive", icon: "building", line: "A coordinator session checks the weather with a web tool (sunny, 22 °C) and decides: wave." },
  { who: "Board", title: "Card", layer: "naive", icon: "doc", line: "The agent can't reach a USB arm, so it creates a card, \"Arm job: wave\", status todo, for the bench." },
  { who: "Hermes cron", title: "Pull ≤ 60 s", layer: "hermes", icon: "loop", line: "Every minute a cheap script asks Naïve's REST API for new cards; only then does the agent wake. Card → doing." },
  { who: "Model on RunPod", title: "Pick a tool", layer: "compute", icon: "eye", line: "Hermes sends the card and the arm's tool list to its model (e.g. Qwen3-VL on vLLM). Reply: arm_gesture(\"wave\")." },
  { who: "Guard hook", title: "Check", layer: "hermes", icon: "shield", line: "pre_tool_call checks that the gesture is allowed, the arm is in a safe mode, and no faults are active." },
  { who: "Pi 5 app", title: "Move", layer: "edge", icon: "arm", line: "The Pi app plays a pre-defined wave through LeRobot send_action, every step checked against joint limits. The model never moves joints itself." },
  { who: "Back up", title: "Report", layer: "naive", icon: "chart", line: "post_tool_call logs \"wave ok, 3.2 s\"; Hermes comments on the card and marks it done; Naïve replies to you." },
];

export const JOURNEY_FAIL: Hop[] = [
  { who: "SO-101", title: "Servo jams", layer: "edge", icon: "alert", line: "A servo jams mid-wave (A6 in the fault catalog). A tier 1 health check catches it." },
  { who: "Pi 5", title: "Park", layer: "edge", icon: "shield", line: "Snapshot first, then safe pose and torque off." },
  { who: "Healer", title: "Diagnose", layer: "edge", icon: "bug", line: "The healer and its local model see a hardware fault: no code fix, so it shows restraint." },
  { who: "Romulus", title: "Work order", layer: "heal", icon: "doc", line: "The incident reaches the cloud and becomes a work order: a Naïve board card (D12)." },
  { who: "Naïve agent", title: "Parts call", layer: "naive", icon: "phone", line: "The assigned agent phones in the part via Mobilerun. The incident timeline shows every step and why." },
];

export const JOURNEY_NOTES: { k: string; v: string }[] = [
  { k: "Shortcut", v: "For a quick demo you could type the request straight into Hermes on the bench. But the hackathon requires Naïve, and the company story lives there." },
  { k: "Delay", v: "Mostly the 60 s poll. A tunnel and webhook would make it instant, at the cost of exposing the bench." },
];

export interface SponsorPick {
  name: string;
  pick?: boolean;
  adds: number; // what it adds that Naïve lacks, 1–3 (Claude's estimate)
  fit: number; // fit with the arm-company story, 1–3 (Claude's estimate)
  what: string;
  story: string;
  prize: string;
  setup: string;
}

/** One sponsor track is enough. Scores are an inferred judgment, not from a source. */
export const SPONSOR_PICK: SponsorPick[] = [
  {
    name: "Mobilerun",
    pick: true,
    adds: 3,
    fit: 3,
    what: "Agents that operate real phones, including apps that have no API.",
    story: "After a hardware fault, it orders the part or books a technician: the \"hands\" beyond the bench.",
    prize: "1st 3 virtual iPhones ($8,520) · 2nd $5,760 · 3rd $3,000",
    setup: "Direct MCP with a bearer key works from Naïve.",
  },
  {
    name: "AgentMail",
    adds: 1,
    fit: 2,
    what: "Email inboxes for agents.",
    story: "Sends incident reports, but Naïve personas already have email.",
    prize: "1st $500 gift card + 6 months free",
    setup: "MCP auth unconfirmed (x-api-key vs bearer).",
  },
  {
    name: "Monid",
    adds: 2,
    fit: 1,
    what: "2,981 paid tools and APIs behind one integration.",
    story: "Could price parts or look up specs; a weak link to the arm.",
    prize: "1st $1,500 credits + $1,500 Visa",
    setup: "OAuth MCP; Naïve needs a bearer key or a wrapper.",
  },
];

export const SPONSOR_SRC: Src[] = [repo("docs/sponsors.md"), repo("docs/hackathons.md")];
