// Parses the repo's features.txt (the team's claim tracker) at build time,
// so the hub never keeps a second copy of who is on what.
import raw from "../../../features.txt?raw";

export type BoxId = "naive" | "board" | "models" | "sim" | "hermes" | "romulus" | "pi" | "so101" | "demo" | "hub";
/** done = in FINISHED · building = claimed · gap = nobody · hold = "(on hold" */
export type FeatureStatus = "done" | "building" | "gap" | "hold";

export interface Feature {
  box: BoxId;
  text: string;
  owners: string[];
  status: FeatureStatus;
}

/** Section headers and FINISHED prefixes, both matched case-insensitively. */
const BOX_NAMES: Record<string, BoxId> = {
  naive: "naive",
  board: "board",
  models: "models",
  "nvidia sim": "sim",
  hermes: "hermes",
  romulus: "romulus",
  "pi 5": "pi",
  "so-101": "so101",
  "demo and faults": "demo",
  demo: "demo",
  "hub and docs": "hub",
  hub: "hub",
};

export const INITIAL: Record<string, string> = { James: "J", Kent: "K", Olir: "O", Ishaan: "I", Andre: "A" };
export const initial = (name: string) => INITIAL[name] ?? name.slice(0, 1).toUpperCase();

export function parseFeatures(src: string): Feature[] {
  const out: Feature[] = [];
  let box: BoxId | null = null;
  let finished = false;
  for (const line of src.split(/\r?\n/)) {
    const t = line.trim();
    if (!t) continue;
    const header = t.toLowerCase();
    if (header === "finished") {
      finished = true;
      box = null;
      continue;
    }
    // An all-caps line is a section header. One that isn't a diagram box
    // (e.g. SPONSORS AND CREDITS) closes the box, so its lines are skipped.
    if (/[A-Z]/.test(t) && t === t.toUpperCase()) {
      box = BOX_NAMES[header] ?? null;
      finished = false;
      continue;
    }
    const [body, claim] = t.split("<---");
    const owners = claim ? claim.split(",").map((s) => s.trim()).filter(Boolean) : [];
    let text = body.trim();
    let lineBox = box;
    if (finished) {
      const m = /^([^:]+):\s*(.+)$/.exec(text);
      lineBox = m ? BOX_NAMES[m[1].trim().toLowerCase()] ?? null : null;
      if (m) text = m[2];
    }
    if (!lineBox) continue; // the intro text, or a FINISHED line without a known box
    const status: FeatureStatus = finished ? "done" : /\(on hold/i.test(text) ? "hold" : owners.length ? "building" : "gap";
    out.push({ box: lineBox, text, owners, status });
  }
  return out;
}

export const FEATURES = parseFeatures(raw);

export const featuresIn = (box: BoxId) => FEATURES.filter((f) => f.box === box);

/** Initials of everyone with a claimed or finished line in the box, in first-seen order. */
export function ownersOf(box: BoxId): string[] {
  const seen: string[] = [];
  for (const f of featuresIn(box)) for (const o of f.owners) if (!seen.includes(initial(o))) seen.push(initial(o));
  return seen;
}

export function countsOf(box: BoxId) {
  const fs = featuresIn(box);
  return {
    done: fs.filter((f) => f.status === "done").length,
    building: fs.filter((f) => f.status === "building").length,
    gap: fs.filter((f) => f.status === "gap" || f.status === "hold").length,
  };
}

/** A box nobody has touched yet: no line claimed or finished. Drawn black. */
export const untouched = (box: BoxId) => ownersOf(box).length === 0;

/** What the bench prototype (Hermes ↔ LeRobot on a teammate's laptop) needs first. */
export const PROTOTYPE = new Set([
  "OpenAI key for bench Hermes",
  "Hermes install on the bench",
  "Hermes arm plugin tools",
  "pre_tool_call safety guard",
  "LeRobot 0.7 on Python 3.12",
  "One serial-port owner (arm host)",
  "Calibration with LeRobot",
]);
