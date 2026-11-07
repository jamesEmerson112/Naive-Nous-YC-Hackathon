import type { Layer } from "./meta";
import type { IconName } from "../viz";

/** The project's words in one plain line each, for readers new to the stack. */
export const PLAIN: { term: string; icon: IconName; layer: Layer; line: string }[] = [
  { term: "Naïve", icon: "building", layer: "naive", line: "The office. A hosted platform: it supplies the infrastructure (sandboxes, Board, inbox, vault, payments) and we write the AI agents that run the company on it." },
  { term: "Board", icon: "doc", layer: "naive", line: "A shared to-do list in Naïve, like Trello. One card per job: todo → doing → blocked → done." },
  { term: "Hermes", icon: "plug", layer: "hermes", line: "The agent at the bench that actually works the arm, through a plugin." },
  { term: "Romulus", icon: "shield", layer: "heal", line: "Kent's back office for the arm's health, on his laptop at the bench: logs incidents, turns fixes into new versions, rolls them out or back." },
  { term: "Healer", icon: "bug", layer: "edge", line: "A small agent on the Pi that fixes the arm on the spot: park, fix, check, or undo." },
  { term: "Canary", icon: "gate", layer: "sim", line: "Try a new version on one device first; if it stays healthy, give it to all." },
  { term: "Work order", icon: "phone", layer: "naive", line: "A hardware problem turned into a Board card, so a Naïve agent orders the part." },
  { term: "RunPod", icon: "gpu", layer: "compute", line: "The machine room: rented GPUs for big models and the simulator." },
];
