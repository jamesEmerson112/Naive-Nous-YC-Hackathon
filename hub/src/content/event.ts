import { local, repo, type Src } from "./meta";

export const NAIVE_EVENT = {
  where: "YC office, San Francisco, in person both days",
  times: [
    ["Hacking", "Sat Nov 7, 11:30 AM → Sun Nov 8, 11:00 AM PST (about 23.5 h)"],
    ["Judging starts", "Sun 11:00 AM"],
    ["Final presentations", "Sun 1:00 PM; winners 1:45 PM; ends 2:30 PM"],
  ],
  rules: [
    "Every team must use the Naïve SDK.",
    "Judged on how much of the company runs without you.",
    "Attend both days; solo or team.",
    "Unknown: can one company win Main plus a sponsor track? Ask the organizers.",
  ],
  tracks: [
    ["Main: best autonomous company", "1st iPhone 18 Pro / AirPods 5 + guaranteed YC interview + $50K+ sponsor credits; 2nd Mac mini / AirPods 5; 3rd Mac mini; 4th–6th Pocket AI notetaker"],
    ["Mobilerun", "1st 3 virtual iPhones ($8,520); 2nd 2 ($5,760); 3rd 1 ($3,000)"],
    ["AgentMail", "1st $500 gift card + 6 months free; 2nd and 3rd 6 months free"],
    ["Monid", "1st $1,500 credits + $1,500 Visa; 2nd $1,000 + $1,000; 3rd $500 + $500"],
  ],
  credits: ["Naïve $250", "Stripe $500 + incorporation fee waived", "OpenAI $100", "Apify $50", "Tavily 8K credits", "Mobilerun Developer plan per team"],
};

export const HERMES_EVENT = {
  where: "Online, hosted by Nous Research",
  deadline: "Wed Nov 11, 5:00 PM EST (2:00 PM PST); build window opened Wed Nov 4",
  prizes: [
    "$20,000 cash in total",
    "1st $10,000 + neon sign + merch + $1,000 credits",
    "2nd $5,000 · 3rd $2,500 (each + neon sign, merch, $1,000 credits)",
    "4th $1,500 · 5th $1,000 (+ merch, $500 credits); 5 runners-up: merch + $250 credits",
  ],
  submit: [
    "Catalog PR adding plugin-catalog/<name>.yaml to NousResearch/hermes-agent",
    "Demo video on X, under 90 s, tagging @NousResearch",
    "The submission form",
  ],
  judged: "usefulness, reliability, polish, creativity",
};

export const SCENARIOS: { id: string; name: string; idea: string; sponsors: string }[] = [
  { id: "A", name: "Self-running agency", idea: "Clone Naïve's agency blueprint for a niche; its seven-agent crew finds clients, writes proposals and keeps the site fresh overnight.", sponsors: "Main track · Stripe" },
  { id: "B", name: "Mobile app QA studio", idea: "App makers send a store link; agents run key flows on cloud phones and email a bug report with screenshots.", sponsors: "Mobilerun · Stripe" },
  { id: "C", name: "Support desk as a service", idea: "Each client gets an AgentMail support inbox; agents triage, answer from the FAQ, escalate and send a daily digest.", sponsors: "AgentMail · Stripe" },
  { id: "D", name: "Market-intel report company", idea: "Weekly competitor reports: research agents pull many data providers through Monid, a writer compiles.", sponsors: "Monid · Stripe" },
];
export const CLOCK = "The clock idea from the scenarios page: build by day, seed real work and budgets in the evening, let it run overnight, then collect proof (board, digest, approvals, spend) at 9 AM before hacking stops at 11.";

export const ARM_DESIGN: string[] = [
  "Four layers: Naïve business (cloud) → Naïve board → Hermes on the bench → RunPod model; the hardware driver belongs to neither.",
  "The bench pulls cards over Naïve's REST API through Hermes cron, so no port opens to the robot (design choice).",
  "The LLM decides what to do; the bench controls how. Context → decide → check (pre_tool_call) → execute → observe.",
  "Serverless vLLM scales to zero but cold starts take minutes; a pod avoids them overnight. Warm it before judging.",
  "Logs: post_tool_call captures every move; a short summary goes on the card; raw logs stay on the bench.",
  "Alarms skip the queue: Hermes gateway → Telegram, Slack or SMS, and the card goes to blocked with the reason.",
  "Safety: a physical e-stop, limits checked twice, a person approves each move until a job type is proven, never YOLO.",
  "HIL-SERL: 20–30 demos, a success classifier from ~200 positive and ~1,000 negative frames, human takeovers; 1–2.5 h per task.",
];

export const EVENT_SRC: Src[] = [repo("docs/hackathons.md"), repo("docs/sponsors.md"), repo("docs/scenarios.html"), local("docs/robotic-arm.html (draft)")];
