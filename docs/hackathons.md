# Hackathons — quick facts

Snapshot: 2026-11-07. Times are Pacific (PST) unless marked.

| | Naïve Autonomous Company Hackathon | Hermes Agent Plugin Hackathon |
|---|---|---|
| Host | Naïve + Y Combinator | Nous Research |
| Where | YC office, San Francisco (in person, both days) | Online |
| Build window | **Sat Nov 7 1:00 PM → Sun Nov 8 11:00 AM** | Wed Nov 4 → **Wed Nov 11 5:00 PM EST (2:00 PM PST)** |
| Must use | The Naïve SDK | Hermes Agent's plugin system |
| What you submit | A live demo of an agent-run company | A plugin: a catalog PR, a demo video on X, and a form |
| Judged on | How much of the company runs without you | Usefulness, reliability, polish, creativity |

Order for doing both: Naïve runs until Sunday afternoon, then you have Sunday through Wednesday 2 PM PST for the Hermes plugin.

---

## 1. Naïve Autonomous Company Hackathon

- Event page: https://events.ycombinator.com/naive-autonomous-companies-hackathon
- SDK docs: https://usenaive.ai/docs (terms in [naive/terminology.md](naive/terminology.md))
- Starter repos: https://github.com/usenaive. The `agency-blueprint` and `media-blueprint` repos were pushed Nov 5 and match two of the example business types; `naive-starter-template` and `app-dev-templates` are also there.

**Brief (from the page):** "Build any company that is primarily run by agents." "All teams must use the Naïve SDK to build their company." At demo time, show the company operating: what its agents actually delivered, what decisions they made, and where you had to step in. Demos are live, and "judges will focus on how much of the company runs without you."

The page suggests four kinds of business, none of them required tracks:
- **Media:** agents decide what to publish, produce it, and plan from audience results.
- **Agency:** agents find prospects, scope projects, deliver client work, and follow up.
- **Commerce:** agents choose what to sell, run campaigns, handle customers, and adjust based on sales.
- **Software:** agents build a product, bring in users, handle support, and improve from feedback.

**Schedule**

| Sat Nov 7 | | Sun Nov 8 | |
|---|---|---|---|
| 11:00 AM | Doors open | 9:00 AM | Breakfast |
| 11:30 AM | Welcome note | **11:00 AM** | **Hacking stops, judging begins** |
| 12:00 PM | Lunch | 12:00 PM | Lunch |
| **1:00 PM** | **Hacking begins** | 1:00 PM | Final presentations |
| 6:30 PM | Dinner | 1:45 PM | Winners + prizes |
| | | 2:30 PM | Event ends |

The page's metadata says the event starts and ends at 19:00 UTC (11 AM PST) on Nov 7 and Nov 8, which matches "doors open" and "hacking stops". The written schedule above runs until 2:30 PM on Nov 8. Registration closed at 11 AM on Nov 7.

**Prizes**
- 1st place: iPhone 18 Pro / AirPods 5, a **guaranteed YC interview**, and $50,000+ in sponsor credits.
- 2nd place: Mac mini / AirPods 5.
- 3rd place: Mac mini.
- Category prizes ($15k+ total, including Pocket AI notetakers). The page names them two different ways:
  - "Most Likely to Become a Unicorn · People's Choice · Best Mobile Automation"
  - "Most likely to scale to millions · People's Choice · Best Design"
- Every participant gets $1k+ in Naïve and sponsor credits.

**Best Mobile Automation track (run by Mobilerun):** agent-run businesses working on real phones and apps, for example publishing content, comparing ride quotes, placing delivery orders, or testing apps.
- Every team gets 72 hours of Android and iOS device minutes and six team seats. iPhones are first come, first served.
- Prizes are cloud iPhone packages, each lasting 12 months: 1st gets 3 ($8,520), 2nd gets 2 ($5,760), 3rd gets 1 ($3,000).
- Naïve also has its own mobile devices: `computer/mobile`, `client.mobileDevices`, `naive mobile`.

**Rules:**
- Attend both days in SF; only approved attendees may attend.
- Come solo or as a team, or find teammates at kickoff.
- Minimum age is 15; ages 15–17 need YC's parental consent form.
- Bring a laptop and charger.

**Sponsors:** OpenAI, HubSpot, Stripe, Vercel, OpenRouter, AgentMail, Luxor, Monid, Apify, Mobilerun, Render, Neon.

---

## 2. Hermes Agent Plugin Hackathon

- Official announcement: Nous Research on X, Nov 4 2026 21:33 UTC: https://x.com/NousResearch/status/2118094256438936062. I read it through the public `api.fxtwitter.com` mirror.
- Plugin developer guide: https://hermes-agent.nousresearch.com/docs/developer-guide/plugins
- Submission guide: https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission
- Marketplace: https://hermes-agent.nousresearch.com/marketplace
- Discord: http://discord.gg/nousresearch
- Terms: [hermes/terminology.md](hermes/terminology.md)

**Brief (from the post):**
- "Every kind of plugin is eligible."
- A plugin can change the model Hermes runs on, where it stores memory, where it runs commands, which messaging apps it works in, and what Hermes Desktop shows.
- "Your entry is the plugin itself." Judges score usefulness, reliability, polish and creativity, and "favor plugins that do more than a prompt or a skill could."
- Accepted entries are listed in the new Hermes Marketplace, where any Hermes user can install them.

**To enter, by Wed Nov 11 at 5:00 PM EST:**
1. Open a PR that adds your plugin to the Hermes plugin catalog (see the checklist below).
2. Post a demo video on X and tag `@NousResearch`. Keep it **under 90 seconds** and show the plugin working.
3. Submit both links through the form: http://form.typeform.com/to/FwJLvOln. You can do this as soon as the PR is open.

Catalog checks run only once a maintainer starts them, and entries are reviewed every few days. After submissions close, each entry gets its own thread in the Nous Research Discord.

**Prizes ($20,000 cash in total)**

| Place | Prize |
|---|---|
| 1st | $10,000 + neon sign + merch + $1,000 credits |
| 2nd | $5,000 + neon sign + merch + $1,000 credits |
| 3rd | $2,500 + neon sign + merch + $1,000 credits |
| 4th | $1,500 + merch + $500 credits |
| 5th | $1,000 + merch + $500 credits |
| Runners-up (×5) | merch + $250 credits |

### Catalog PR checklist (from the submission guide)

**Before you open the PR:**
- **Public repo:** an `https://` GitHub or GitLab URL anyone can clone.
- **A plugin that loads at the pinned commit:** a `plugin.yaml` manifest plus at least one entrypoint: `__init__.py` (Python), `desktop/plugin.js` (Desktop), `plugin.json` (portable Agent Plugin) or `dashboard/manifest.json` (web dashboard). Use `subdir` if it lives in a monorepo.
- **Public interfaces only:** hooks, middleware, the `ctx.register_*` APIs, provider plugins, and the Desktop plugin SDK. Never patch Hermes at runtime.
- **Validation passes:** `hermes plugins validate /path/to/your-plugin --install-deps`. This is the same check catalog CI runs.

**The PR itself:**
- Add **one** file, `plugin-catalog/<name>.yaml`, to `NousResearch/hermes-agent`.
- Pin `sha` to a full 40-character commit, and put `version` in quotes.
- The PR description says what the plugin does, which parts of Hermes it uses, and the risky behaviour rule 13 asks you to disclose. Add screenshots for any UI.
- Wait for catalog CI to go green; then a maintainer merges it, asks for changes, or declines it.

The admission rules most likely to trip up a hackathon entry (there are 18; full text in the submission guide):
- **3. No self-updating code.** The SHA pin is the trust model.
- **5.** Only the repo owner or a major contributor may submit it.
- **6.** The `capabilities:` you declare (tools, hooks, middleware, env vars) must match what the plugin actually registers.
- **9. No runtime overrides of Hermes core**, such as `AIAgent.<method> = ...` or `sys.modules[...]`. Rule 3 and rule 9 problems are declined outright, not sent back for fixes.
- **11.** Secrets only through Hermes (`agent.secret_scope.get_secret(NAME)`); never read `.env` or `auth.json`.
- **12.** Never route around approvals, and anything that waits for a person must fail cleanly or time out when no one is there to answer.
- **13.** Disclose network calls, reads outside the plugin's own data, shell commands, background processes, and stored credentials. Telemetry must be opt-in.
- **14.** `requires_hermes` is a SemVer floor (`">=0.21.5"`); declare the same floor in `plugin.yaml`.
- **17.** No compiled binaries or `.pyc` files.

Entries are already open: PRs such as #150840 and #150803 are titled "plugin-catalog: add <name> (Plugin Hackathon entry)" and add a single YAML file.

The earlier news article (coinfomania.com) left out "polish" from the judging criteria and didn't mention the form. The official post above takes precedence.
