# Sponsors: how each one plugs into Naïve

- **Snapshot:** 2026-11-07.
- **Sources:** each sponsor's own site or docs (fetched today), the event page, the on-site slides, and the Naïve docs (`docs/raw/`).
- **Labels:** *(inferred)* is my own reasoning. **Ask on site** marks something no source answers.
- **See also:** [scenarios.html](scenarios.html) puts these to work; [hackathons.md](hackathons.md) has prizes and credits.

## Four ways a sponsor connects to a Naïve agent

1. **Native Naïve tool.** Use Naïve's own feature instead: persona inboxes (`email.send`), cloud phones (the `mobile` tool, `pi` only), `web_search`, the CRM, or store payments. No sponsor involved, so this doesn't count toward a sponsor track *(inferred)*.
2. **Direct MCP + vault.** Declare the sponsor's remote MCP server on the agent and keep its token in the vault. Rules ([capabilities/tools](https://usenaive.ai/docs/capabilities/tools), [identity/vault](https://usenaive.ai/docs/identity/vault)):
   - **Address:** a public HTTPS URL, at most 20 servers per agent.
   - **Tool names:** the server's tools become `<server>.<tool>`. Every declared server must be used by at least one enabled tool.
   - **Approvals:** MCP tools **default to `ask`**. An `ask` needs a harness with approvals (`pi` or `vetta`). Set a specific tool to `allow` to let it run unattended.
   - **Auth:** only through the vault. `static_bearer` holds a bearer token; `mcp_oauth` holds an OAuth access token, with no refresh. Both are matched to the server's URL. Keys put inline on the agent are rejected.
   - **The vault can't reach the sandbox:** `env_var` credentials return 501, so the vault has no way to hand an API key to code running in the sandbox.
3. **App-wrapped MCP.** A Naïve **fullstack app** keeps the sponsor's API key as an app secret and serves its own `/mcp` endpoint. Setting `mcp: "/mcp"` on the app makes Naïve create `VETTA_MCP_TOKEN`, and every agent that can reach the app gets its tools as `<app-name>.<tool>`. This works with any auth style; the agency blueprint uses it for its CRM tools. ([api/apps](https://usenaive.ai/docs/api/apps))
4. **Key in the sandbox (last resort).** Put the sponsor's API key in a file on the agent's computer and call its REST API with `bash`. This is fastest when MCP auth doesn't fit and a wrapper app would cost the afternoon. The trade-off: the key is visible to the model and ends up in the transcript, so use a throwaway key and revoke it after the weekend *(inferred)*.

**Pattern 2 snippet.** Naming a tool in `configs` both enables it and sets its permission. Look up the real tool names in the sponsor's MCP docs.

```jsonc
{
  "mcp_servers": [
    { "type": "url", "name": "mobilerun", "url": "https://api.mobilerun.ai/v1/mcp" }
  ],
  "tools": {
    "default_config": { "permission": "ask" },
    "configs": {
      "mobilerun.<tool-name>": { "enabled": true, "permission": "ask" }
    }
  }
}
```

```bash
# The vault belongs to a persona, so give the agent that persona (inferred from "A vault maps to an identity").
naive vault create --name company-secrets --identity <persona>
naive vault set --vault vlt_... --mcp-bearer https://api.mobilerun.ai/v1/mcp   # paste the key on stdin; it is never echoed
```

---

## Mobilerun · "Best use of Mobilerun" track

- **What it is:**
  - Event page: "AI agents that operate real Android and iOS phones through natural language."
  - From mobilerun.ai: cloud Android on demand, physical cloud iPhones (an add-on), or your own phone connected; a live view of every step; receive-only SMS numbers and mailboxes for OTP flows (both paid add-ons).
- **For the hackathon:** a Developer plan for every team (slide); 72 hours of device minutes and six seats (event page). Prizes are virtual iPhones.
- **Ways to call it:**
  - REST at `https://api.mobilerun.ai/v1` (for example `POST /v1/tasks`).
  - SDKs: Python `mobilerun`, TypeScript `@mobilerun/sdk`.
  - CLI: `mobilerun run "<task>"`. Webhooks fire for each step.
  - **MCP:** `https://api.mobilerun.ai/v1/mcp`, Streamable HTTP, `Authorization: Bearer dr_sk_…`, or OAuth.

  Sources: https://mobilerun.ai, https://mobilerun.ai/llms.txt, https://docs.mobilerun.ai/mcp-server
- **Into Naïve:** **pattern 2 works as-is**, because the server accepts a bearer key. Put the `dr_sk_…` key in the vault with `--mcp-bearer` against that URL.
- **Naïve's own `mobile` tool:** it runs on `pi` only and asks by default. It installs apps only from Naïve's own catalogue, with no links or uploads ([computer/mobile](https://usenaive.ai/docs/computer/mobile)). Nothing says whether it runs on Mobilerun, so for this track call Mobilerun directly *(inferred)*.
- **Ask on site:** the MCP's tool names (or see docs.mobilerun.ai/mcp-server); whether iPhones are available to your team; whether Naïve's cloud phones count as "use of Mobilerun".

## AgentMail · "Best use of AgentMail" track

- **What it is:**
  - Event page: "Email inbox API that lets AI agents send, receive, and act on emails."
  - From agentmail.to: an inbox for each agent, threads, custom domains (DKIM, SPF, DMARC), webhooks and WebSockets, and IMAP/SMTP.
- **For the hackathon:** prizes only, no credits on the slide.
- **Ways to call it:**
  - REST API: https://docs.agentmail.to/api-reference
  - SDKs: Python and TypeScript, plus `agentmail-toolkit`.
  - **MCP:** `https://mcp.agentmail.to/mcp`, over HTTP. Auth is OAuth through console.agentmail.to, **or an API key in an `x-api-key` header**.
  - The MCP has 47 tools, including `create_inbox`, `list_inboxes`, `send_message`, `reply_to_message`, `list_threads`, `search_threads`, and draft tools.

  Sources: https://www.agentmail.to, https://docs.agentmail.to/integrations/mcp.md
- **Into Naïve: the auth fit is unconfirmed.** Naïve's vault sends a bearer token, but AgentMail documents `x-api-key`. Options, best first:
  1. Ask whether `mcp.agentmail.to` accepts `Authorization: Bearer <key>`. If it does, use pattern 2 with `static_bearer`.
  2. Pattern 3: wrap AgentMail's REST API behind your app's `/mcp`.
  3. Go through Monid, which lists AgentMail as a provider. That puts you on Monid's auth instead.
  4. Last resort: pattern 4, a throwaway key in the sandbox.
- **Naïve's own email:** personas have their own inboxes on a verified domain (`email.send` asks by default). For this track, use AgentMail inboxes *(inferred)*.
- **Ask on site:** is a bearer key accepted on the MCP? Which events do webhooks send? Any free-tier limits for the weekend?

## Monid · "Best use of Monid" track

- **What it is:**
  - Event page: "OpenRouter for agent tools. Connect your agent to 2,981 tools & APIs with one integration. No subscriptions, just pay as you go."
  - From monid.ai: 2,800+ tools from 90+ providers, including Exa, Firecrawl, Apollo, People Data Labs, Ahrefs, ElevenLabs, Browserbase, AgentMail, and Reddit, TikTok, Instagram and Amazon scrapers. One balance, paid per call; you start with $1 of free credit.
- **For the hackathon:** prizes are Monid credits plus Visa gift cards.
- **Ways to call it:**
  - A skill: `https://monid.ai/skill.md`.
  - A CLI.
  - **MCP:** `https://mcp.monid.ai/v1`, Streamable HTTP, **OAuth browser login**.
  - The site names `monid.discover()` and `monid.run()`.

  Sources: https://monid.ai, https://monid.ai/docs/guide/quickstart-mcp
- **Into Naïve: the auth fit is unconfirmed.** Naïve's `mcp_oauth` takes an access token you supply, and it never refreshes. Options:
  1. Ask Monid for a bearer API key for the MCP, then use pattern 2.
  2. Pattern 3: wrap Monid behind your app's `/mcp`.
  3. Get an OAuth token yourself and store it as `mcp_oauth`, replacing it when it expires.
- **Ask on site:** a bearer or API key for the MCP? Hackathon credit beyond the free $1? Which providers are the most reliable?

## Money in · Stripe

- **Credits:** "$500 credits + inc. fee waived" (slide).
- **Built into Naïve: store payments** ([api/apps](https://usenaive.ai/docs/api/apps)). A store app takes card payments on its owner's own Stripe account, and Naïve holds the only Stripe key.
  - `POST /v1/apps/{id}/payments/onboarding`: a signed-in owner gets a Stripe onboarding link.
  - `POST /v1/apps/{id}/payments/checkouts`: the app's own key opens a Stripe Checkout.
  - `GET /v1/apps/{id}/payments/checkouts/{ref}`: shows whether a checkout was paid, and how much.
  - With no store, the template agent's `payments_request` tool sends pay links and invoices, asking the owner before every call.
- **Setup before the first checkout:**
  1. The owner finishes Stripe onboarding.
  2. The app holds its own Naïve key. It gets one from an approved `apps.request_access`, from a template's apply, or by having one set on the app by hand ([api/apps](https://usenaive.ai/docs/api/apps)).
- **Why it matters:** the Main track goes to the company "most likely to scale to millions". A paid checkout is concrete evidence of that *(inferred)*.

## Other sponsors (how they connect to Naïve isn't checked)

| Sponsor | From the event page | Notes |
|---|---|---|
| OpenAI | "AI research lab and creator of the GPT models." | $100 API credits. Naïve agents use Naïve's model router and credits, so these suit your own app code or Hermes *(inferred)* |
| HubSpot | "CRM platform for marketing, sales, and customer service." | Naïve has its own CRM (`crm.read` / `crm.write`) |
| Vercel | "The platform for deploying and scaling modern web apps." | Naïve also hosts apps (`naive app`) |
| OpenRouter | "One API to route requests across hundreds of LLMs from every major provider." | |
| Luxor | "GPU compute and AI infrastructure" | |
| Apify | "Web scraping and automation platform with thousands of ready-made data extractors." | $50 credits |
| Neon | "Serverless Postgres with branching, autoscaling, and scale-to-zero." | Naïve fullstack apps come with a managed database |
| Tavily | On the slide only (8K credits), with no description at the event | A search API for AI agents *(general knowledge)* |
