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
  { who: "Arm host", title: "Move", layer: "edge", icon: "arm", line: "POST /skill/gesture plays a pre-defined wave through LeRobot send_action, every step checked against joint limits. The model never moves joints itself." },
  { who: "Back up", title: "Report", layer: "naive", icon: "chart", line: "post_tool_call logs \"wave ok, 3.2 s\"; Hermes comments on the card and marks it done; Naïve replies to you." },
];

export const JOURNEY_FAIL: Hop[] = [
  { who: "Arm host", title: "Overheat", layer: "edge", icon: "alert", line: "The gripper servo overheats mid-wave. The reflex stops the motion and goes to a safe pose." },
  { who: "Romulus", title: "Diagnose", layer: "heal", icon: "doc", line: "The watchdog reads the logs, diagnoses \"overheat\", and proposes a cooldown." },
  { who: "Hermes gateway", title: "Alert", layer: "hermes", icon: "phone", line: "Your phone gets a text." },
  { who: "Board", title: "Blocked", layer: "naive", icon: "doc", line: "The card goes to blocked, with the reason." },
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
