// One Naïve SDK client for Romulus scripts on this branch.
// Reads NAIVE_API_KEY (and optionally NAIVE_BASE_URL) from the environment; `npm run …` loads ../.env.
import { createClient, DEFAULT_BASE_URL } from "@usenaive-sdk/managed-agents";

export { ApiError } from "@usenaive-sdk/managed-agents";

export function naiveClient() {
  const apiKey = process.env.NAIVE_API_KEY;
  if (!apiKey) {
    throw new Error("NAIVE_API_KEY is not set: add it to the repo's .env (gitignored), or export it");
  }
  // The Python adapter's NAIVE_BASE_URL ends in /v1; the SDK wants the bare origin.
  const baseUrl = (process.env.NAIVE_BASE_URL || DEFAULT_BASE_URL).replace(/\/+$/, "").replace(/\/v1$/, "");
  return createClient({ baseUrl, apiKey });
}
