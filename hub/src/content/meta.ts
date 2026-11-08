export const REPO = "https://github.com/jamesEmerson112/Naive-Nous-YC-Hackathon";
export const AS_OF = "Sat Nov 7, 2026 · 8:30 PM PST";
export const TEAM_AS_OF = "8:25 PM: main at 7608d69, every branch and open PR on GitHub, and each person's status block (Kent 8:20 PM, Ishaan 8:12 PM)";

/** Each layer keeps one color everywhere on the page. */
export type Layer = "naive" | "hermes" | "compute" | "edge" | "sim" | "heal" | "neutral";

/** Status is drawn by chip shape, not color. */
export type Status = "decided" | "proposed" | "open" | "tentative" | "ref";

export const LAYER_NAME: Record<Layer, string> = {
  naive: "Naïve · company",
  hermes: "Hermes · bench agent",
  compute: "RunPod · compute",
  edge: "Edge · arm host",
  sim: "Sim + control",
  heal: "Romulus · self-healing",
  neutral: "Other",
};

export const STATUS_HELP: Record<Status, string> = {
  decided: "agreed by the team",
  proposed: "a concrete proposal, not agreed yet",
  open: "nobody has decided",
  tentative: "someone is on it, details may change",
  ref: "reference only",
};

export interface Src {
  label: string;
  href?: string;
}

export const repo = (path: string, label = path): Src => ({ label, href: `${REPO}/blob/main/${path}` });
export const naiveDoc = (path: string): Src => ({ label: `Naïve docs · ${path}`, href: `https://usenaive.ai/docs/${path}` });
export const hermesDoc = (path: string): Src => ({
  label: `Hermes docs · ${path}`,
  href: `https://hermes-agent.nousresearch.com/docs/${path}`,
});
export const ext = (label: string, href: string): Src => ({ label, href });
/** A source that isn't on GitHub yet (e.g. an uncommitted draft). */
export const local = (label: string): Src => ({ label });
