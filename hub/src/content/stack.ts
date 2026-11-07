import { hermesDoc, naiveDoc, repo, type Src } from "./meta";

export interface Term {
  term: string;
  what: string;
  use: string;
  src: Src;
}

/** Naïve: the company runs here. Entries follow docs/naive/terminology.md. */
export const NAIVE: Term[] = [
  { term: "Managed agent", what: "A versioned config (model, prompt, tools, skills, required budget, harness) that Naïve runs as sessions. Every edit makes a new immutable version.", use: "Each company role (planner, ops, outreach) is one agent. The arm itself is not run here.", src: naiveDoc("capabilities/agent") },
  { term: "Session", what: "One durable run of an agent. It can pause for hours at storage cost and resume. Statuses: queued, running, idle, completed, failed, cancelled.", use: "One session per board card. stop_reason (awaiting_approval, budget_paused) says why it stopped.", src: naiveDoc("concepts/sessions") },
  { term: "Harness", what: "The agent loop, chosen once at creation: pi (default, GA), vetta, claude_code or hermes (previews). Changing it means a new agent.", use: "Keep pi for company agents: it keeps approvals and streaming.", src: naiveDoc("concepts/harnesses") },
  { term: "Company board", what: "A shared card board per coordinator (todo, doing, blocked, done). Assigning a card starts the assignee in its own session.", use: "The company's work queue. The bench could pull arm cards over REST (inferred design, not a documented bridge).", src: naiveDoc("team/board") },
  { term: "Team and coordinator", what: "A coordinator agent plus a version-pinned roster. Delegation is one level deep.", use: "A coordinator plans on the board and assigns cards to specialist members.", src: naiveDoc("team/overview") },
  { term: "Computer (micro-VM)", what: "A disposable Linux micro-VM with filesystem, shell and optional browser; paused while the agent sleeps.", use: "Cloud-only sandbox with no route to a USB arm on the bench.", src: naiveDoc("capabilities/computer") },
  { term: "Persona", what: "An addressable identity the agent acts as. It owns email, phone, domains, connections and a credential vault.", use: "Gives the company a customer inbox and phone number.", src: naiveDoc("capabilities/identity") },
  { term: "Vault", what: "Secrets injected at the network boundary; the agent never sees raw values and the API only writes them.", use: "MCP tokens for cloud agents. Bench credentials never go into prompts.", src: naiveDoc("identity/vault") },
  { term: "Tools and policy", what: "Each tool resolves to allow, ask or deny. An ask pauses the tool and emits tool.confirm until a person decides.", use: "Keep spending, publishing and customer contact on ask.", src: naiveDoc("concepts/policies") },
  { term: "MCP servers", what: "External tool servers, named <server>.<tool>, up to 20 per agent. URLs must be public HTTPS; the default permission is ask.", use: "Cloud agents could reach the bench only through a public HTTPS endpoint or tunnel (inferred).", src: naiveDoc("capabilities/tools") },
  { term: "Apps", what: "Hosted web apps (naive app) with optional database and storage, plus their own MCP endpoint whose tools agents see as <app>.<tool>.", use: "A storefront or order page for arm jobs.", src: naiveDoc("api/apps") },
  { term: "Deployments (cron)", what: "Runs an agent on a schedule and collects the results of every fire.", use: "Daily order review, status digest, follow-ups.", src: naiveDoc("capabilities/deployments") },
  { term: "Budgets and credits", what: "Calls are priced in advance in micro-USD through the model router. Every agent needs a budget cap; credits are a real USD balance.", use: "A daily cap per agent. $250 Naïve credits per hacker.", src: naiveDoc("concepts/budgets") },
  { term: "Events, digest, audit logs", what: "An ordered event stream per session, a digest of what needs you or failed, and audit logs of control-plane actions.", use: "The proof for judges: how much ran without us, and where a person stepped in.", src: naiveDoc("concepts/events-and-streaming") },
  { term: "Webhooks", what: "Signed deliveries of session and message events. Inbound email and SMS also wake the owning agent.", use: "Push finished-job or failure events to the bench or a chat.", src: naiveDoc("capabilities/webhooks") },
  { term: "API, SDK, CLI", what: "REST at api.vetta.sh/v1 with a Bearer key, the TypeScript SDK @usenaive-sdk/managed-agents, and the naive CLI.", use: "The SDK is required by the hackathon. REST lets the bench read and move cards.", src: naiveDoc("sdk/typescript") },
  { term: "CRM", what: "Clients, timelines and meetings, through crm.read and crm.write.", use: "Arm customers and their orders.", src: naiveDoc("api/crm") },
];

/** Hermes: the agent at the bench. Entries follow docs/hermes/terminology.md and the raw dump. */
export const HERMES: Term[] = [
  { term: "Plugin", what: "Extends Hermes with tools, hooks and integrations: a plugin.yaml manifest plus an __init__.py with register(ctx).", use: "The SO-101 arm plugin. It is also the Plugin Hackathon entry.", src: hermesDoc("developer-guide/plugins") },
  { term: "register(ctx) and tools", what: "ctx.register_tool(name, toolset, schema, handler) adds a tool; register_hook adds a hook. provides_tools in the manifest must match.", use: "Register arm tools (status, photo, pick, place, home, stop) as their own toolset.", src: hermesDoc("developer-guide/plugins") },
  { term: "Plugins are opt-in", what: "User-installed plugins stay disabled until listed in plugins.enabled or turned on with hermes plugins enable.", use: "An easy bench setup step to forget: without it the arm tools never load.", src: hermesDoc("developer-guide/plugins") },
  { term: "pre_tool_call hook", what: "Runs before every tool and returns block, approve or modify (rewrite args). A callback that raises or times out blocks the tool.", use: "The safety guard: block out-of-range moves, clamp speed, ask before big motions.", src: hermesDoc("user-guide/features/hooks") },
  { term: "post_tool_call hook", what: "Observer only: sees tool, args, result and duration, and cannot change the result.", use: "Log every arm command for the audit trail and the card comment.", src: hermesDoc("user-guide/features/hooks") },
  { term: "Approvals", what: "Dangerous actions ask a person. cron_mode, unattended_mode and single_query_mode default to deny; YOLO mode approves everything.", use: "Scheduled arm jobs get no human, so risky actions are denied. Never YOLO near the arm.", src: hermesDoc("user-guide/security") },
  { term: "Cron", what: "Natural-language scheduled tasks; the gateway ticks every 60 s. A pre-check script can print {\"wakeAgent\": false} to skip the model.", use: "Pull the next card each minute and spend nothing when the board is empty.", src: hermesDoc("user-guide/features/cron") },
  { term: "Webhooks", what: "Gateway HTTP listener, default port 8644, at /webhooks/<route> with HMAC-SHA256. A route can fire an existing cron job.", use: "An arm incident can wake a diagnosis job; Naïve could call in through a tunnel (inferred).", src: hermesDoc("user-guide/messaging/webhooks") },
  { term: "Gateway and messaging", what: "A long-running process with 25+ adapters (Telegram, Discord, Slack, WhatsApp, SMS, email); also runs the cron ticks.", use: "Alerts and approvals on a phone.", src: hermesDoc("user-guide/messaging") },
  { term: "hermes send", what: "CLI that pushes a message to a chat target without a model call: hermes send --to <target> \"text\".", use: "Fast fault alerts straight from the arm host.", src: hermesDoc("user-guide/messaging") },
  { term: "Profiles", what: "A separate Hermes home (config, .env, memory, sessions, skills, cron), selected with hermes -p <name>.", use: "One profile per arm.", src: hermesDoc("user-guide/profiles") },
  { term: "Custom providers", what: "Any OpenAI-compatible endpoint can be the model. RunPod is named as an example custom endpoint.", use: "Point Hermes at a vLLM server on a RunPod GPU.", src: hermesDoc("integrations/providers") },
  { term: "64K context minimum", what: "Hermes rejects models with less than 64,000 tokens of context at startup.", use: "Serve the RunPod model with --max-model-len of at least 65536.", src: hermesDoc("integrations/providers") },
  { term: "Vision", what: "Vision-capable models get real pixels; text-only endpoints go through the vision_analyze helper model.", use: "Camera frames need a multimodal model, or a helper vision model.", src: hermesDoc("user-guide/features/vision") },
  { term: "Terminal backends", what: "Where commands run: local, Docker, SSH, Daytona, Modal, Vercel Sandbox, or plugin backends.", use: "local on the bench, since the USB arm hangs off that machine.", src: hermesDoc("developer-guide/architecture") },
  { term: "Skills", what: "On-demand SKILL.md documents in ~/.hermes/skills/ that the agent loads when needed and can edit.", use: "Calibration and pick-and-place procedures.", src: hermesDoc("user-guide/features/skills") },
  { term: "MCP", what: "Hermes calls external MCP servers; the mcp-serve plugin makes Hermes an MCP server over stdio.", use: "stdio means a local client must launch it; Naïve can't call it directly (inferred).", src: hermesDoc("user-guide/features/mcp") },
  { term: "Session storage", what: "Conversations in SQLite with full-text search and lineage across compressions.", use: "Searchable history of every arm run.", src: hermesDoc("developer-guide/session-storage") },
];

/** "Hermes inside Naïve" ownership split, from docs/cross-reference.md. */
export const HARNESS_SPLIT = {
  hermesOwns: [
    "The turn loop and its own toolset (file, shell and search tools) inside the machine.",
    "The docs call it \"an external coding agent, run as a process\".",
  ],
  naiveOwns: [
    "The durable loop (wake → turn → commit → sleep), the budget gate, tool policy, event log and ledger.",
    "The micro-VM: sandbox execution; an idle session holds the machine.",
    "Personas, connections, MCP servers, apps and team tools, which reach Hermes through a session-scoped tool endpoint. \"You configure nothing.\"",
  ],
  status: "preview. Runnable only where a base image exists; check GET /v1/harnesses (runnable) first. No approvals, typed output or streaming; ask tools are refused.",
  src: [repo("docs/cross-reference.md"), naiveDoc("concepts/harnesses"), naiveDoc("concepts/harness-capabilities")] as Src[],
};

export interface Point {
  point: string;
  detail: string;
  src: Src[];
}

export const CONNECT: Point[] = [
  { point: "Same words, different things", detail: "Agent, session, skill, MCP, approval, board, sandbox and webhooks exist in both. A Naïve agent is a config record; a Hermes agent is a running program (inferred).", src: [repo("docs/cross-reference.md")] },
  { point: "Two Hermes, two jobs", detail: "Hermes inside Naïve is a cloud preview harness. Our arm Hermes runs on the bench computer, where the USB arm is, and is not hosted by Naïve.", src: [repo("docs/setup-diagrams.html"), naiveDoc("concepts/harness-capabilities")] },
  { point: "Why cloud agents can't touch the arm", detail: "Naïve agents run in cloud micro-VMs with outbound access only. The SO-101's serial port is on the bench machine.", src: [naiveDoc("capabilities/computer")] },
  { point: "Bench pulls cards (proposed)", detail: "Hermes cron polls the Naïve REST API with a Bearer key, runs the card, posts the result. No inbound port to the robot (inferred design).", src: [naiveDoc("api/overview")] },
  { point: "Or Naïve pushes (open)", detail: "Expose a bench endpoint (a Hermes webhook on 8644, or an MCP server) through a tunnel. Naïve MCP URLs must be public HTTPS, so the robot becomes reachable from the internet.", src: [naiveDoc("capabilities/tools"), hermesDoc("user-guide/messaging/webhooks")] },
  { point: "Keep credentials apart", detail: "Naïve injects vault secrets server-side. Hermes plugins read secrets only through agent.secret_scope.get_secret (catalog rule 11).", src: [naiveDoc("identity/vault"), hermesDoc("developer-guide/plugins/catalog-submission")] },
];

export const HOST_RULES: Point[] = [
  { point: "Naïve SDK is required", detail: "\"All teams must use the Naïve SDK to build their company.\" Judged on how much of the company runs without you.", src: [repo("docs/hackathons.md")] },
  { point: "Hermes entry: three things by Wed Nov 11, 2:00 PM PST", detail: "A catalog PR adding plugin-catalog/<name>.yaml (sha pinned to a full commit), a demo video on X under 90 s tagging @NousResearch, and the form.", src: [repo("docs/hackathons.md")] },
  { point: "Catalog rules that bite a hardware plugin", detail: "11: secrets only via get_secret. 12: never route around approvals; fail cleanly when nobody answers. 13: disclose network, shell, background processes, credentials. 17: no compiled binaries or .pyc.", src: [hermesDoc("developer-guide/plugins/catalog-submission")] },
  { point: "Validate before the PR", detail: "hermes plugins validate <path> --install-deps must pass; it is the same check CI runs.", src: [repo("docs/hackathons.md")] },
];
