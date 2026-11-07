# Cloud platform: decision log

Decisions on the vision, principles, values and scope of the cloud platform, recorded one question at a time. Read this before changing [`cloud-platform-prd.md`](cloud-platform-prd.md) or [`cloud-platform-apis.md`](cloud-platform-apis.md). When a decision changes, edit its entry and add a line to its **History**.

- **Owner:** Kent (cloud platform, initial build)
- **Started:** Sat Nov 7, 2026 (hackathon day 1)
- **Background:** Kent's Romulus docs in `~/git2/docs` (local, not pushed yet): `VISION.md`, `STRATEGY.md`, `ARCHITECTURE.md`, `IDEAS.md`, `1-hackathon-plan.md`
- **Status key:** ✅ decided · ⏳ pending · 🔁 revisit later

---

## Summary

| # | Question | Decision | Status |
|---|---|---|---|
| D0 | Core thesis | The agent fixes; the batch locks it in as firmware and skills | ✅ |
| D1 | Who the user is | Us (the arm company), single tenant | ✅ |
| D2 | How broad the objective is | Narrow everywhere: the cloud half of the self-healing loop; "general purpose" dropped. *Refined by D16: narrow loop, general data* | ✅ |
| D3 | What "firmware" means | Versioned bundle: code version + rules/params + policy model version | ✅ |
| D4 | What the "harness" is | Device-side teammate picks the framework; the platform stores it as an unparsed versioned folder | ✅ |
| D5 | How much the on-device agent can do alone | Can patch code or rules on the device, then report the patch to the cloud | ✅ |
| D5a | What limits on-device patches | Local overlay on top of the assigned version; reverts itself if health fails or the fault returns; the healer may patch everything, including the safety layer and the visor | ✅ |
| D6 | What checks a batch-built version | Replay, then a canary device, then automatic promotion (or automatic rollback); no human gate | ✅ |
| D7 | What the device's "validate" means | Hash check, install, health check, roll back on failure; a new version replaces any overlay | ✅ |
| D8 | What happens when the cloud is down | Keep running the last good version (plus overlay); queue reports on disk; never bricked | ✅ |
| D9 | Which value comes first | Legible, never magical: every step has a readable "why" and links back to what caused it | ✅ |
| D10 | The one screen that has to work | Incident timeline: each incident opens into its full story through to a locked-in version or a work order | ✅ |
| D11 | Where a human stays in the loop | Only the kill switch (freeze rollouts, pin last good); spend, technician booking and harness promotion are automatic. *Caps enforced in our code* | ✅ |
| D12 | Who runs the parts and technician agents | Naïve: the platform posts work orders as Naïve board cards; a Naïve agent calls through Mobilerun. *Corrected: caps live in our code, not Naïve policy* | ✅ |
| D13 | What's out of scope for v0 | Cut: digital twins/ERP, billing per heal, trust ledger, policy model retraining | ✅ |
| D14 | Who builds the device side | A teammate (TBD) owns the device side; Kent owns the cloud and the contracts | ✅ |
| D15 | The v0 build plan | Approved in principle; v2 in [`cloud-platform-v0-plan.md`](cloud-platform-v0-plan.md) | ✅ |
| D16 | Role of robot telemetry | A first-class, general-purpose asset: synced, curated, versioned datasets for models, harnesses and firmware | ✅ |
| D17 | Runtime | Local Docker containers; Python + FastAPI | ✅ |
| D18 | Architecture details | Proposed in [`cloud-platform-architecture.md`](cloud-platform-architecture.md) | ⏳ |
| D19 | Naïve integration design | Proposed in [`cloud-platform-naive-integration.md`](cloud-platform-naive-integration.md) | ⏳ |
| D19b | What happens over the spend cap | (a) block and wait for a person *(recommended)* · (b) gated agent with `ask` | ⏳ |
| D20 | Telemetry formats and capture policy | Proposed in [`cloud-platform-telemetry.md`](cloud-platform-telemetry.md) | ⏳ |

---

## Vision and identity

### D0. Core thesis ✅
**Question:** What is the one idea the cloud platform exists to serve?
**Decision:** **The agent fixes; the batch locks it in.**
- The on-device agent handles new faults with slow, expensive LLM reasoning.
- The cloud batch job turns each verified fix into firmware: a fixed rule that needs no LLM the next time.
- Fixes that nearly worked become new harness skills.
- Over time the fleet's reflexes get faster and cheaper, and the agent only sees faults nobody has seen before.

**Options not chosen:** a platform built mainly around updates and telemetry; a fleet-ops dashboard with healing left to the device.
**What this means for the build** *(inferred)*:
- The batch job is the center of the platform, not a side feature.
- Registry, telemetry and dashboard exist to feed it and show its results.
- This is the "rebuilt every night" story from `~/git2/docs/VISION.md`.

**History:** decided Sat Nov 7 (Kent).

### D1. Who the user is ✅
**Question:** Who is the "manager of robot systems" in the PRD: us running our own arms, or an outside fleet owner?
**Decision:** **Us, as a single tenant.** We run our own arms for the arm company.

**Options not chosen:** external fleet owners from day one; both, with real multi-tenancy in v0.
**What this means for the build** *(inferred)*:
- No real auth and no tenant isolation in v0.
- Keep a `fleet_id` (or similar) column on the main tables so multiple tenants can be added later without a rewrite.
- Selling Romulus to other fleets stays a pitch slide, not v0 scope.

**History:** decided Sat Nov 7 (Kent).

### D2. How broad the objective is ✅
**Question:** The PRD says "general purpose platform for managing robotic systems." The Romulus vision says fleet management is a commodity and to go narrow and deep. Which wins?
**Decision:** **Narrow everywhere.** Rewrite the PRD objective itself: the cloud platform is the cloud half of a self-healing loop. It collects every incident, turns verified fixes into new firmware and harness versions, and ships them back. "General purpose" comes out of the PRD.

**Options not chosen:** a general long-term vision with a narrow v0; a general robot-management platform where healing is one feature.
**What this means for the build** *(inferred)*:
- Every feature has to serve the heal loop (D0). Generic fleet features (asset inventory, user management, generic log search) wait.
- The PRD objective needs rewriting to match.
- Consistent with "go narrow and deep" and "not another dashboard" in `~/git2/docs/VISION.md`.

**History:**
- Decided Sat Nov 7 (Kent).
- **Refined by D16 (round 2):** the *loop* stays narrow, but the *data* it produces is deliberately general-purpose. "Narrow loop, general data" (PRD principle 2).

### D3. What "firmware" means ✅
**Question:** What is the deployable "firmware" artifact, physically? (The SO-101's STS3215 servos run Feetech's own firmware.)
**Decision:** **A versioned bundle for the Python control stack on the bench:** control-code version, plus parameters and reflex rules, plus policy model version. This is the "manifest" in `~/git2/docs/ARCHITECTURE.md`. We still say "firmware" in the pitch.

Example shape (field names not final):
```json
{
  "version": 7,
  "code_rev": "a1b2c3d",
  "rules": {
    "camera_occluded": {"threshold": 0.2, "action": "safe_pose"},
    "gripper_stall":   {"current_ma": 900, "action": "backoff_retry"}
  },
  "params": {"max_speed": 0.6},
  "policy": {"name": "pick_cube", "rev": "hf:org/act@3f9e"}
}
```

**Options not chosen:** only rules and parameters versioned; a code package with rules hard-coded inside; real microcontroller firmware.
**What this means for the build** *(inferred)*:
- The registry stores small JSON manifests that point to code and model versions. No large binaries in v0.
- The batch job's main output is a change to `rules` and `params`, which is easy to check and easy to roll back.
- The servo firmware is out of scope.

**History:** decided Sat Nov 7 (Kent).

### D4. What the "harness" is ✅
**Question:** What is the "self-healing agent harness" that we version and update?
**Decision:** **The teammate building the embedded agent picks its framework.** The platform treats the harness as a versioned folder whose contents it doesn't need to understand.

**Options not chosen:** a Hermes profile (system prompt, skills, `MEMORY.md`, toolsets, plugin); our own Python agent loop.
**What this means for the build** *(inferred)*:
- The registry stores a harness version as a folder or tarball with a hash and metadata. It doesn't parse what's inside.
- For the batch job to update the harness, we still need one agreed place for learned knowledge, for example a `skills/` or `runbooks/` folder of markdown files. Settle this with the device-side owner (D14).
- Not Hermes: the device runs a small local model with its own small harness (being tested: pi and mini-swe-agent). The cloud side's big model and its harness are separate.

**History:** decided Sat Nov 7 (Kent). Sat Nov 7: the device side (Ishaan) clarified that the embedded agent is a small-model harness, not Hermes. This line used to say Hermes was the likely choice.

## Principles

### D5. How much the on-device agent can do alone ✅
**Question:** Can the embedded agent deploy fixes on its own, and can it write new code on the device or only choose from a fixed menu of actions?
**Decision:** **It can patch on the device.** The agent may write and apply code or rule changes locally, then report them to the cloud. Limits are in D5a.

**Options not chosen:** a typed menu of reversible actions, each automatic only once it has earned trust; propose only, with a human approving every fix.
**Conflicts with Romulus** *(inferred)*: `~/git2/docs/VISION.md` principle 4 says the LLM's confidence counts for nothing until a run proves it. Principle 5 says autonomy is earned per action type. The `CLAUDE.md` notes say the LLM only proposes typed, reversible actions. This decision relaxes those rules on the device. D5a and D6 are where safety gets put back.
**What this means for the build** *(inferred)*:
- A device can run something other than the version the cloud assigned. The platform has to track three things per device: the version it was assigned, the version it actually runs, and the local patch on top.
- Every incident report has to include the patch itself (a diff), so the batch job can decide whether to lock it in. This becomes the batch job's main input.
- The visor layer must not silently undo a local patch when it pulls the assigned version. The rule for that is in D7.

**History:** decided Sat Nov 7 (Kent).

### D5a. What limits on-device patches ✅
**Question:** What limits what the on-device agent can patch?
**Decision:** **Patches go in a local overlay and revert themselves.**
- A patch is applied as a local layer on top of the assigned version. The assigned version itself is never edited in place.
- If the health check fails or the fault comes back, the overlay is dropped automatically and the device goes back to the assigned version.
- The healer may patch everything on the device, including the safety layer (e-stop, speed and torque limits) and the visor. Every patch still goes in the self-reverting overlay.

**Options not chosen:** only rules and parameters patchable on the device; anything except safety, edited in place with no automatic revert; no limits in v0.
**What this means for the build** *(inferred)*:
- Each device runs the version it was assigned plus an optional overlay. The overlay has its own id and a diff that is sent to the cloud.
- An overlay's life ends one of three ways: (a) it reverts itself, (b) the batch job locks it into a new version, which replaces the overlay, or (c) the cloud tells the device to drop it.
- The overlay is the unit the batch job learns from: overlays that held up become candidates for new firmware (D0).
- This brings back most of the safety lost in D5. A bad patch can only last until its next health check.

**History:** decided Sat Nov 7 (Kent). Changed Sat Nov 7 (Ishaan, device side): the healer may patch everything, including the safety layer and the visor, which were previously off limits.

### D6. What checks a batch-built version ✅
**Question:** What has to pass before a firmware or harness version built by the batch job reaches a device?
**Decision:** **Replay, then a canary, then automatic promotion.** No human approves a version.
1. **Replay:** run the candidate version against the recorded incident (in sim, or with the recorded data).
2. **Canary:** if replay passes, roll it out to one canary device (simulated or fake).
3. **Promote:** if the canary stays healthy for N minutes, the candidate becomes the assigned version for the whole fleet. If the canary fails, roll it back automatically.

**Options not chosen:** replay plus human approval; human approval only; replay, canary and human approval.
**What this means for the build** *(inferred)*:
- We need at least two devices to make the canary meaningful: the real arm plus simulated or fake devices.
- We need a definition of "healthy for N minutes": for example, no repeat of the incident and the task-success metric not falling. Settle this with the device-side owner.
- Automatic rollback of a failed canary is the hero-demo moment from `~/git2/docs/1-hackathon-plan.md` ("a bad model rollout is caught by the canary and rolled back automatically").
- With no human gate, the platform needs a clear kill switch: freeze rollouts, and pin the fleet to the last good version. This is a good place to name a human role in D11.
- Romulus's trust ladder (suggest → approve → auto) gets shortened here: the batch job starts at auto, with the canary as the safety net.

**History:** decided Sat Nov 7 (Kent).

### D7. What the device's "validate" means ✅
**Question:** When the visor layer pulls an update, what does "validate" mean?
**Decision:** **Check the hash, check health, roll back on failure.**
1. Check that the downloaded version matches its hash.
2. Install it.
3. Run a health check.
4. If the health check fails, roll back to the last good version.

An active overlay is dropped when a new version installs. The cloud has already seen the overlay through the incident report, and a locked-in version replaces it.

**Options not chosen:** hash check only, leaving health to the canary; a signature check plus a smoke test on the arm.
**What this means for the build** *(inferred)*:
- Each device keeps the last good version on disk so rollback works without the cloud.
- The device reports the install result (installed, rolled back, or failed hash check) to the cloud. The canary in D6 depends on this report.
- The device side needs a health check it can run; the platform only receives the result. Settle with the device-side owner (D14).
- No signing in v0. Add it if this ever leaves the bench.

**History:** decided Sat Nov 7 (Kent).

### D8. What happens when the cloud is down ✅
**Question:** What does the device do when it can't reach the cloud?
**Decision:** **Keep running the last good version and queue reports.** The device keeps running its last good version, plus any overlay, and keeps healing locally. Incident reports queue on disk and send when the connection comes back. The device is never bricked.

**Options not chosen:** keep running but drop reports made while offline.
**What this means for the build** *(inferred)*:
- Incident reports need a unique id created on the device, so that resending one is harmless and the cloud ignores duplicates.
- Reports can arrive late and out of order, so the cloud sorts by the time the incident happened, not the time it arrived.
- This matches "local-first, no bricking" in `~/git2/docs/VISION.md`, and it protects the demo if venue Wi-Fi drops.

**History:** decided Sat Nov 7 (Kent).

## Values

### D9. Which value comes first ✅
**Question:** Which Romulus value does the cloud platform have to show first?
**Decision:** **Legible, never magical.** If you or a judge can't follow incident → fix → overlay → locked-in version → canary → fleet, it didn't happen. When values conflict, this one wins.

**Options not chosen as first:** calm competence; warmth and character; human leverage. All four still apply, in that order behind legibility *(inferred)*.
**Why it matters here** *(inferred)*: D5 lets the agent patch on the device and D6 promotes versions without a human. Legibility is what keeps that much autonomy trustworthy. It also follows `CLAUDE.md`'s L2.20: feeling lost is a disgust trigger.
**What this means for the build** *(inferred)*:
- Every step records a human-readable "why" alongside the data: the agent's reasoning, the replay result, and the canary verdict.
- Every object links to the objects that caused it and the ones it caused: incident → overlay → candidate → canary → assigned version.
- No step happens without leaving a record the dashboard can show.

**History:** decided Sat Nov 7 (Kent).

### D10. The one screen that has to work ✅
**Question:** If only one dashboard screen works at demo time, which one?
**Decision:** **The incident timeline.** A list of incidents. Each one opens into its full story: what broke, what the agent did, the overlay, whether it held, and the version that locked it in, or the work order it escalated to.

Sketch (not final):
```
INCIDENTS
● 14:02  arm-1  camera_occluded
   agent: held safe pose, raised threshold
   overlay ov-12 → held 20m
   → locked into firmware v8 → canary ✓ → fleet
● 13:41  sim-3  gripper_stall
   overlay ov-11 → reverted (fault returned)
   → escalated: work order #4 (replace servo)
```

**Options not chosen as first:** a version pipeline (candidate → replay → canary → fleet); a fleet grid (one tile per device).
**What this means for the build** *(inferred)*:
- The data model has to support a single query that rebuilds one incident's whole chain. This is D9's linking rule turned into a screen.
- Build this screen first. The version pipeline and fleet grid come later, if there's time.
- The sketch covers both endings of the loop: a fix that gets locked in, and a fault that gets escalated to a work order (D12).

**History:** decided Sat Nov 7 (Kent).

### D11. Where a human stays in the loop ✅
**Question:** In v0, which actions keep a human on purpose?
**Decision:** **Only the kill switch.** A human can freeze all rollouts and pin the fleet to the last good version. Everything else runs automatically:
- promoting firmware and harness versions (through D6's replay and canary);
- ordering parts, at any amount;
- booking technicians.

**Options not chosen:** human approval for spend over a cap; human approval for booking a technician; human approval for harness changes.
**What this means for the build** *(inferred)*:
- The kill switch has to be obvious, tested, and recorded every time it's used. It's the one human control, so it must work on stage.
- Harness versions go through the same replay and canary as firmware.
- No human approves spending, so limits have to live outside the LLM: a hard spend cap and an order-rate limit. That's the Project Vend lesson in `~/git2/docs/VISION.md`. Even with no approval step, an agent shouldn't be able to order 50 servos. **The caps live in our code** (see History).
- This gives a strong autonomy-ledger number for judging: one human control, and every use of it is recorded.

**History:**
- Decided Sat Nov 7 (Kent).
- **Correction (round 2, research):** the first version said caps could live "in code or in Naïve's policy file". Naïve has no policy that can cap spend: its policy is allow, ask or deny per tool ([Naïve integration](cloud-platform-naive-integration.md) §3.4, §8). The caps move into our code (`ORDER_MAX_USD`, `ORDERS_PER_DAY_MAX`, `ORDERS_PER_ROBOT_DAY_MAX`). What happens over a cap is D19b; option (a) keeps this decision intact.

## Scope and boundaries

### D12. Who runs the parts and technician agents ✅
**Question:** Does the cloud platform run its own agents for ordering parts and booking technicians, or hand that work to Naïve?
**Decision:** **Naïve runs them.**
1. The platform raises a work order, for example "replace servo 3 on arm-1".
2. It posts the work order as a card on the Naïve board.
3. A Naïve agent with the Mobilerun MCP makes the call.
4. ~~Spend caps live in Naïve's policy file.~~ Spend caps live in our code (see History).
5. The dashboard shows the work order's status.

**Options not chosen:** the platform runs its own agents and calls Mobilerun directly; the platform's agent decides and Naïve only carries out the call.
**What this means for the build** *(inferred)*:
- The platform doesn't orchestrate agents. It creates work orders and tracks their status, which avoids building a second agent system next to the mandatory one.
- The platform needs to create Naïve board cards (through the Naïve API or SDK) and read their status back. Confirm the exact calls in `docs/naive/terminology.md` and the raw docs.
- Sponsor fit: Mobilerun ("the robot phones in a parts order"). AgentMail could send the incident report from the same card.
- A work order is created when an overlay reverts itself and the fault looks like hardware (see the D10 sketch). The rule for "looks like hardware" is still open.

**History:**
- Decided Sat Nov 7 (Kent).
- **Correction (round 2, research):** spend caps can't live in Naïve's policy and move into our code (D11 History). The integration design is outbound only: cards are posted with an `Idempotency-Key`, status comes back by polling, and the crew is declared in `naive/naive.config.json`. It's proposed as D19 in [Naïve integration](cloud-platform-naive-integration.md). The core decision, that Naïve runs the agents, stands.

### D13. What's out of scope for v0 ✅
**Question:** What's cut from the first build?
**Decision:** **All four are cut from v0:**
- **Digital twins and ERP** (PRD user story 5). The MuJoCo sim stays, but only for replay and canary.
- **Billing per heal.** The platform still records verified heals as events that Naïve can read later. It just doesn't bill.
- **Trust ledger.** D6's canary is the safety net instead.
- **Policy model retraining.** The batch job changes rules, parameters and the harness only. The policy model version stays pinned.

**What this means for the build** *(inferred)*:
- The PRD loses user story 5.
- Write heal events so billing and a trust ledger can be added later by reading them, with no schema change.
- The batch job's output is only changes to rules, parameters and the harness, which keeps it small and easy to check.

**History:** decided Sat Nov 7 (Kent).

### D14. Who builds the device side ✅
**Question:** Who builds the embedded agent and the firmware stack on the bench?
**Decision:** **A teammate owns the whole device side:** the embedded agent, the firmware stack and the visor layer. Kent builds only the cloud platform and the contracts between the two. *(Teammate: name TBD.)*

**Options not chosen:** a split where Kent builds fake devices and a reference visor; Kent builds both sides.
**What this means for the build** *(inferred)*:
- The contracts are Kent's most important early deliverable, because the teammate builds against them. They are:
  - the incident report, including the overlay diff;
  - the firmware manifest (D3);
  - the harness package (D4);
  - the endpoints for check-in, pulling the assigned version, and reporting install results;
  - the health and canary signals (D6, D7).
- Kent still needs a small test client that acts like a device, to exercise the cloud before the real device works. It's a test fixture, not a second device stack.
- These questions are now the teammate's to answer, and they go on their list: what's in the harness's skills or runbooks folder (D4), the health-check definition (D7), what counts as "healthy for N minutes" (D6), and how a fault is judged to be hardware (D12).

**History:** decided Sat Nov 7 (Kent).

### D15. The v0 build plan ✅
**Question:** Does the proposed local v0 build match the plan?
**Decision:** **Approved in principle.** Kent: "This plan looks great", followed by a round of refinement. [`cloud-platform-v0-plan.md`](cloud-platform-v0-plan.md) v2 carries that refinement: telemetry and datasets, Docker/FastAPI, and the Naïve research. The build order is in [`cloud-platform-build-plan.md`](cloud-platform-build-plan.md).

**History:**
- Proposed Sat Nov 7.
- Approved in principle Sat Nov 7 (Kent), with round-2 refinements (D16–D20).

---

## Round 2: telemetry, runtime, integration (Sat Nov 7, ~3:30 PM)

From Kent's round-2 feedback: reconcile with the PRD; lean more on telemetry and datasets; make Naïve integration robust; plan the build and its risks; set the code architecture, run in Docker, Python/FastAPI.

### D16. Telemetry and datasets are a first-class asset ✅
**Question:** What role does robot telemetry play in the platform?
**Decision:** **A first-class, general-purpose asset.** Traces, sensor data, software logs and agent sessions from the robots are synced to the platform. They become curated, versioned datasets for training models, improving harnesses and developing firmware, beyond healing (Kent, round 2).

**What this means for the build** *(inferred)*:
- New PRD stories US6 (sync), US7 (datasets) and US9 (regression replay); new requirements R4 and R9.
- The regression corpus links the data to the gate: every past incident becomes a replay case.
- Priority stays below the hero loop: incident evidence is P0, episodes and datasets are P1, the LeRobot export is P2 (build plan §1, §6).
- Refines D2: narrow loop, general data.

**History:** decided Sat Nov 7 (Kent, round-2 feedback).

### D17. Runtime: local Docker, Python and FastAPI ✅
**Question:** How does the platform run, and in what?
**Decision:** **Locally, in Docker containers, with Python and FastAPI as the main frameworks** (Kent, round 2).
**What this means for the build:** see [`cloud-platform-architecture.md`](cloud-platform-architecture.md). Postgres replaces the SQLite in the v1 plan, because two containers write at the same time (D18).
**History:** decided Sat Nov 7 (Kent, round-2 feedback).

### D18. Architecture details ⏳
**Question:** Approve the proposed architecture?
**Proposal:**
- one image for `api` and `worker`;
- Postgres 17;
- a uv workspace with a shared `romulus-contracts` package;
- a pure `domain/` core;
- adapters with fakes, and fake modes as the defaults;
- a filesystem blob store;
- Jinja + htmx pages.

Details in [`cloud-platform-architecture.md`](cloud-platform-architecture.md).
**Decision:** _pending._

### D19. Naïve integration design ⏳
**Question:** Approve the proposed Naïve integration?
**Proposal:**
- outbound only;
- self-contained cards with an `Idempotency-Key`;
- status by polling;
- spend caps in our code;
- `fleet-ops` declared in `naive/naive.config.json` on the `pi` harness;
- the Mobilerun bearer in the vault, bound to fleet-ops;
- `actor_class` on every event for the autonomy ledger.

Details in [`cloud-platform-naive-integration.md`](cloud-platform-naive-integration.md).
**Decision:** _pending._

### D19b. What happens over the spend cap ⏳
**Question:** When a work order is over a spend cap, what happens?
**Options:**
- **(a) recommended, keeps D11:** it isn't posted; it shows as `blocked_over_cap` and waits for a person.
- **(b)** It's routed to `fleet-ops-gated`, whose Mobilerun tool is set to `ask`, so a person approves it inside Naïve. This adds a second human control.

**Decision:** _pending._

### D20. Telemetry formats and capture policy ⏳
**Question:** Approve the proposed formats and capture policy?
**Proposal:**
- incident windows and episodes as LeRobotDataset v3.0, one small dataset per incident, tarred;
- log windows as MCAP;
- agent traces as JSONL from the agent's session export;
- metrics as rows;
- every incident uploads its evidence (P0), and normal episodes are sampled (P1);
- fleet dataset schema v0.1 frozen tonight;
- filesystem blobs with a budget that never deletes evidence.

Details in [`cloud-platform-telemetry.md`](cloud-platform-telemetry.md).
**Decision:** _pending._
