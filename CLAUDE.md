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
_Last updated: Sat Nov 7, 1:48 PM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; the LLM that operates the arm runs on RunPod. The on-site arm looks like an SO-101 (LeRobot) leader/follower pair. Draft diagrams are in `docs/robotic-arm.html` (not committed yet).

**Recently done:**
- Identified the on-site arm from a photo: most likely an SO-101 follower (red) with its leader arm (white), 6 Feetech STS3215 servos; in LeRobot set `robot_type` to `so101_follower`
- Robotic-arm diagrams: who manages the arms, how logs reach people, and the RunPod model layer
- Read the HIL-SERL paper (arXiv 2410.21845): resets are scripted or done by a person; it doesn't learn its own resets
- Status refresh moved from a per-session timer to the saved task `naive-nous-team-status-sync`

**Open questions:**
- RunPod: serverless or an always-on pod for the overnight run? Which tool-calling model, and does it need to read images?
- How the bench and the board connect: the bench pulls cards (proposed), or the bench is exposed through a tunnel?
- AgentMail and Monid MCP auth (bearer token?); can one company win the Main track and a sponsor track?
- SO-100 or SO-101? Check the leader arm's servo labels or ask the organizers

**Recent commits (all authors):**
- `0452ada` Sat 13:27 · jamesEmerson112 · Update team status in CLAUDE.md
- `fe9f5b0` Sat 13:16 · jamesEmerson112 · Add shared CLAUDE.md with auto-updated team status
- `a2fa81d` Sat 13:12 · jamesEmerson112 · Add hackathon scenarios, sponsor wiring guide, and on-site updates
- `60f3b07` Sat 12:10 · jamesEmerson112 · Add hackathon prep docs for Naïve and Hermes Agent
- `fc6a3ff` Sat 10:24 · jamesEmerson112 · Initial commit

**Branches on GitHub:** `main`
<!-- team-status:end -->
