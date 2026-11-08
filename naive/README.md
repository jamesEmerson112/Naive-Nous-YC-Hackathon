# naive: the Naïve SDK for Romulus (fallback branch `full-test-3`)

Uses the official TypeScript SDK, [`@usenaive-sdk/managed-agents`](https://usenaive.ai/docs/sdk/typescript)
(pinned to 0.12.3). The SDK is TypeScript only, with no Python package (sdk/typescript), so this lives next to
the Python cloud rather than inside it. The cloud's own REST adapter (`cloud/src/romulus_cloud/adapters/naive.py`)
is unchanged.

## Set up

```bash
cd naive && npm install
```

Put the key in the repo's `.env` (gitignored; the cloud reads the same file):

```bash
NAIVE_API_KEY=sk_live_...
NAIVE_BOARD_ID=            # optional; defaults to the CEO agent's board
```

## Check the connection

```bash
npm run check     # read-only: whoami, balance, agents, company board. Writes and spends nothing
```

| File | What it does |
|---|---|
| `client.mjs` | `naiveClient()`: one SDK client from `NAIVE_API_KEY` (and `NAIVE_BASE_URL`, with or without `/v1`) |
| `check.mjs` | The read-only connection check |
