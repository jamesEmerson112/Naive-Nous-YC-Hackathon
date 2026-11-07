# CLAUDE.md — Naive Nous YC Hackathon

Shared context for everyone working in this repo, people and Claude sessions alike. A Claude Code session rewrites the **Team status** section about every 10 minutes and pushes it to `main`. You can edit everything else by hand.

## What this repo is
Prep and build work for two hackathons running at the same time:
- **Naïve Autonomous Company Hackathon** (YC office, SF).
  - Hacking runs **Sat Nov 7 11:30 AM → Sun Nov 8 11:00 AM PST**; presentations are at 1 PM Sunday.
  - Every team must use the Naïve SDK.
- **Hermes Agent Plugin Hackathon** (Nous Research, online). Entries are due **Wed Nov 11, 5 PM EST**.

## Where things are
- `docs/hackathons.md`: rules, schedule, prize tracks, credits
- `docs/scenarios.html`: four prize-track scenarios with diagrams (open it in a browser)
- `docs/sponsors.md`: how each sponsor (Mobilerun, AgentMail, Monid, Stripe…) connects to a Naïve agent
- `docs/setup-diagrams.html`: architecture of Naïve, Hermes, and how they connect. Both pages share `docs/diagrams.css`.
- Terminology: `docs/naive/terminology.md`, `docs/hermes/terminology.md`, and `docs/cross-reference.md` for words the two share
- `docs/raw/`: full doc dumps. Refresh them with `bash docs/raw/fetch.sh`.

## Working rules
- Every fact cites a source; anything you worked out yourself is marked *(inferred)*.
- No Claude or other AI co-author lines in commits.
- Run `git pull --rebase origin main` before you push. Never force-push `main`.
- Don't hand-edit between the `team-status` markers below; the next refresh overwrites it. Put notes for the team in **Notes from the team** instead.

## Notes from the team
_Add anything others should know here (who is doing what, decisions, blockers)._

## Team status
<!-- team-status:start -->
_Last updated: Sat Nov 7, 1:16 PM PST_

**Current focus:** picking which agent-run company to build for the Naïve hackathon. The options are the four scenarios in `docs/scenarios.html` (Main, Mobilerun, AgentMail, Monid).

**Recently done:**
- Terminology for Naïve and Hermes, architecture diagrams, hackathon facts
- Scenarios page and sponsor wiring guide, updated from the on-site slides

**Open questions (ask on site):**
- AgentMail: does its MCP accept a bearer token, or only `x-api-key`?
- Monid: is there a bearer key, or only OAuth?
- Organizers: can one company win the Main track and a sponsor track?

**Recent commits (all authors):**
- `a2fa81d` Sat 13:12 · jamesEmerson112 · Add hackathon scenarios, sponsor wiring guide, and on-site updates
- `60f3b07` Sat 12:10 · jamesEmerson112 · Add hackathon prep docs for Naïve and Hermes Agent
- `fc6a3ff` Sat 10:24 · jamesEmerson112 · Initial commit

**Branches on GitHub:** `main`
<!-- team-status:end -->
