// Read-only check that the Naïve SDK reaches our org: who the key is, the balance, the agents
// and the company board. Writes nothing and spends nothing. Run: npm run check
import { naiveClient, ApiError } from "./client.mjs";

let client;
try {
  client = naiveClient();
} catch (err) {
  console.error(err.message);
  process.exit(2);
}
let failed = 0;

async function step(label, fn) {
  try {
    const line = await fn();
    console.log(`ok    ${label}: ${line}`);
  } catch (err) {
    failed++;
    const why = err instanceof ApiError ? `${err.status} ${err.code}: ${err.message}` : err.message;
    console.log(`FAIL  ${label}: ${why}`);
  }
}

let agents = [];
await step("whoami", async () => {
  const me = await client.auth.me();
  const org = me.organization;
  return `org ${org?.name} (${org?.id}, ${org?.mode}), scopes ${me.scopes.join(",")}`;
});
await step("balance", async () => {
  const b = await client.credits.show();
  return `$${(b.balance_micro_usd / 1e6).toFixed(2)}`;
});
await step("agents", async () => {
  agents = (await client.agents.list({ limit: 50 })).data;
  return agents.map((a) => `${a.name} (${a.id})`).join(", ") || "none";
});
await step("company board", async () => {
  const ceo = agents.find((a) => a.name === "CEO");
  const ref = process.env.NAIVE_BOARD_ID || ceo?.id;
  if (!ref) throw new Error("no NAIVE_BOARD_ID and no agent named CEO");
  const board = await client.board.get(ref);
  const c = board.cards;
  return `${board.id} (owner ${board.agent_id}): ${c.todo} todo, ${c.doing} doing, ${c.blocked} blocked, ${c.done} done`;
});

process.exit(failed ? 1 : 0);
