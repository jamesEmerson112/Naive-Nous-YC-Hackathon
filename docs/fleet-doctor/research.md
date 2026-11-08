# Fleet Doctor: research notes

Light research for the Fleet Doctor plans (Sat Nov 7, 2026, ~5:30 PM PST). Four web-research passes plus one audit of our own repo. This file is the single home for outside facts; the other Fleet Doctor docs link here instead of repeating them.

- **Labels:** every claim has a link. *(inferred)* marks our own reasoning. *(unverified)* means the source is a vendor blog, a comparison site, or something nobody re-checked. **[mem]** marks classic papers cited from memory and not re-fetched today.
- **Caveat:** most 2025–26 papers below are preprints, and their numbers are the authors' own.

---

## 1. Prior art: who diagnoses, who acts

| Name | What it does for remote diagnosis or repair | Acts, or only recommends? | How it reaches robots | Source |
|---|---|---|---|---|
| Formant F3 | Generative AI for robot ops | Recommends | — | [BusinessWire](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops) |
| Alloy Robotics ($8M, Aug 2026) | AI agents that diagnose fleet failures | Diagnoses (acting: unverified) | unverified | [AI Insider](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/) |
| Foxglove Agent + MCP (Aug 2026) | Searches recordings, investigates failures, compares runs | Acts on data and app state only; **robots are read-only** | Has remote access, but the agent doesn't use it | [docs](https://docs.foxglove.dev/docs/agents) |
| Viam MCP | Hosted MCP: fleet and config reads, `get_world_state`, config writes | Writes config (clients usually confirm first) | Connections go through Viam's cloud | [docs](https://docs.viam.com/reference/mcp/) |
| InOrbit | Agent on the robot; "Time Capsule" for root cause; one-click Actions | Acts, but only through predefined Actions | Robot pushes out; **deliberately blocks SSH tunnels** | [FAQ](https://www.inorbit.ai/faq) |
| Transitive Robotics | Remote terminal and teleop; no AI | A human acts | Robot holds an outbound MQTT link to a broker; WebRTC peer-to-peer | [post](https://discourse.openrobotics.org/t/transitive-terminal-easy-remote-access-to-your-robots-zero-setup/33178) |
| balena | Web terminal over Cloudlink; no AI | A human acts | Outbound OpenVPN on port 443 | [forum](https://forums.balena.io/t/sunsetting-vpn-balena-cloud-com-and-introducing-cloudlink/359454) |
| ROSA (NASA JPL) | LangChain agent over ROS tools | Acts (services, params), no guardrails | Runs beside ROS | [GitHub](https://github.com/nasa-jpl/rosa) |
| ros-mcp-server | MCP over rosbridge | Acts; permission controls only "upcoming" | Needs direct network access to the robot | [summary](https://www.sourcepulse.org/projects/14363566) |
| Memfault MCP (May 2026) | Crash issues, traces, metrics | **Read-only** | Devices upload coredumps | [docs](https://docs.memfault.com/docs/platform/mcp-server) |
| Nordic AI dev tools (May 2026) | MCP servers and skills: GDB post-mortem, stack-overflow diagnosis | Assists a developer | nRF Cloud | [Nordic](https://www.nordicsemi.com/Nordic-news/2026/05/Nordic-Semiconductor-brings-AI-assisted-development-to-the-entire-product-lifecycle) |
| Datadog Bits AI SRE | Hypotheses marked validated, invalidated or inconclusive; remediation via PRs or `kubectl` (preview); a "Verify Resolution" step | Acts behind guardrails: **Ask** (named approvers) or **Deny** (recommend only) | A private action runner inside the customer's network | [investigate](https://docs.datadoghq.com/bits_ai/bits_ai_sre/investigate_issues/), [remediation](https://docs.datadoghq.com/bits_ai/bits_remediation/) |
| PagerDuty SRE Agent | Recommends fixes from past incidents and runbooks | Runs fixes only after approval, inside playbooks | — | [PagerDuty](https://www.pagerduty.com/?p=96833) |
| incident.io | Links telemetry, code changes and past incidents | **The only thing it can change is a PR** | — | [blog](https://incident.io/blog/what-is-ai-sre-complete-guide-2026) |
| Rootly | Searches past incidents; root causes with confidence scores | A human signs off on every change | — | [guide](https://rootly.com/ai-sre-guide) |
| fixpi | Agent SSHes into a Linux device and reads logs | Proposes repairs; needs a network and a cloud model | SSH | [PyPI](https://pypi.org/project/fixpi/) |
| EdgeWise (NVIDIA) | A small edge model does the work and hands critical steps to a cloud LLM; up to 62% cheaper on SWE-Rebench | Paper; tested on coding benchmarks, not devices | — | [NVIDIA](https://research.nvidia.com/labs/lpr/edge-wise/) |
| NeSy-Edge | Edge turns logs into a causal graph; cloud LLM makes the final diagnosis; 75% root-cause accuracy with ~1.5 GB on the device | Paper | — | [arXiv 2603.21145](https://arxiv.org/pdf/2603.21145) |
| Syn-Diag | An 8B cloud teacher distilled into a 1.35B edge fault *classifier* | Paper; classifies, doesn't act | — | [arXiv 2510.05733](https://arxiv.org/pdf/2510.05733) |

**Gaps nobody fills** *(inferred from the table)*:
- **Robotics tools stop at reading.** Foxglove, Formant and Memfault read or recommend. The tools that act (ROSA, ros-mcp-server) have no checks, no rollback, and need live network access to the robot.
- **No product pairs an offline healer on the device with an optional cloud doctor.** EdgeWise and NeSy-Edge are papers.
- **No field tool writes and tests a C firmware fix from crash evidence.** Nordic and Memfault help an engineer at a desk.
- **Nobody ports the AI SRE patterns to hardware:** park the arm before acting, then promote a fix written by an agent across the fleet with a canary and automatic rollback.
- **Nobody feeds the doctor's verified fixes back into the small healer.** Syn-Diag distills offline, once.

## 2. Patterns worth copying

- **A hypothesis ledger.** Each hypothesis is marked validated, invalidated or inconclusive, and the agent trace is visible ([Datadog](https://docs.datadoghq.com/bits_ai/bits_ai_sre/investigate_issues/)).
- **The doctor's only write is a proposal,** like incident.io's PR ([incident.io](https://incident.io/blog/what-is-ai-sre-complete-guide-2026)).
- **Verifying is a separate step:** the change applied *and* the problem is gone ([Datadog](https://docs.datadoghq.com/bits_ai/bits_remediation/)).
- **Guardrails per type of action:** recommend only, ask first, or automatic ([Datadog](https://docs.datadoghq.com/bits_ai/bits_remediation/)).
- **Search past incidents, with confidence.** Rootly and PagerDuty do this ([Rootly](https://rootly.com/ai-sre-guide), [PagerDuty](https://www.pagerduty.com/?p=96833)). Caveat: these agents can latch onto a surface pattern and miss the real cause ([assessment](https://levelup.gitconnected.com/the-autonomous-sre-a-practitioners-assessment-of-ai-driven-incident-response-f07dcb0b11a2)).
- **One tool set for the local agent and the remote agent.** Foxglove's sidebar and its MCP server share tools ([docs](https://docs.foxglove.dev/docs/agents)).
- **The robot holds the connection; actions are typed, not shell.** Transitive, balena and Datadog's runner all dial out; InOrbit blocks SSH tunnels (sources in §1).
- **Start read-only, and package firmware debugging as skills.** Memfault's MCP is read-only; Nordic ships GDB post-mortem as skills (§1).

## 3. Connectivity: how a cloud agent calls tools on a device that only dials out

- **MCP today (spec revision 2026-07-28):**
  - It defines stdio and Streamable HTTP. Custom transports over any two-way channel are allowed, so MCP over a WebSocket is legitimate ([transports](https://modelcontextprotocol.io/specification/latest/basic/transports)).
  - HTTP+SSE has been deprecated since 2025-03-26.
  - 2026-07-28 makes the protocol stateless: no `Mcp-Session-Id`, no server-to-client requests, and every POST carries routing headers ([streamable-http](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)). A relay that only queues and forwards POSTs is therefore enough *(inferred)*.
  - Authorization is OAuth 2.1, with audience-bound tokens and no passing tokens through ([authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)).
  - **Unverified:** whether the Python MCP SDK supports 2026-07-28 yet.
- **No way for an MCP server to dial out to its client** exists in the spec. The ecosystem uses outbound tunnels instead:
  - **OpenAI Secure MCP Tunnel:** the customer-side client long-polls over HTTPS; the cloud queues requests for each tunnel; mTLS is supported; the client is open-source ([OpenAI](https://developers.openai.com/blog/connect-private-mcp-servers-to-openai-products)).
  - **MintMCP:** a private-network tunnel ([docs](https://docs.mintmcp.com/docs/private-network-tunnel)).
  - **Speakeasy:** a write-up of the pattern ([Speakeasy](https://www.speakeasy.com/resources/mcp-tunnels)).

| Option | Who dials | How interactive | Effort tonight | Fit later | Source |
|---|---|---|---|---|---|
| Cloud calls the device directly | Cloud | Real time | Trivial on one LAN | Breaks behind NAT and breaks "nothing calls in" | [spec](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http) |
| WebSocket or long-poll relay that the device opens | Device | Real time, or one poll | Low to medium | Good: the same pattern as OpenAI's tunnel | [OpenAI](https://developers.openai.com/blog/connect-private-mcp-servers-to-openai-products) |
| AWS IoT Jobs | Device pulls | Async (timeouts, versions) | Medium (needs AWS) | Strong for async work | [AWS](https://docs.aws.amazon.com/iot/latest/developerguide/jobs-mqtt-api.html) |
| AWS IoT Secure Tunneling | Both ends dial AWS | Interactive; single-use tokens | High | Strong for live sessions | [AWS](https://docs.aws.amazon.com/iot/latest/developerguide/how-secure-tunneling-works.html) |
| Azure IoT Hub direct methods | Device holds the link | Synchronous, 5–300 s timeout, no queue | Medium | Good for short commands | [Microsoft](https://learn.microsoft.com/azure/iot-hub/iot-hub-devguide-direct-methods) |
| MQTT v5 request/response | Device connects to a broker | Near real time | Medium | Standard IoT | [HiveMQ](https://hivemq.com/blog/mqtt5-essentials-part9-request-response-pattern) |
| Tailscale / tsnet | Overlay network | Real-time IP | Low to medium | Human break-glass access; its access rules are per node, not per tool | [tsnet](https://pkg.go.dev/tailscale.com/tsnet@v1.94.2) |
| Teleport | Agent holds an outbound tunnel | Interactive, audited | High | Human SSH with session recording | [Teleport](https://goteleport.com/blog/remote-access-nat-cgnat-firewalls) |

- **Security checklist:**
  - One key per device, with mTLS or a signed device token.
  - Short-lived, single-use lease tokens, tied to the device, the incident and an expiry time ([AWS](https://docs.aws.amazon.com/iot/latest/developerguide/secure-tunneling-concepts.html)).
  - Per-tool scopes: read-only by default; acting needs an extra grant (MCP `insufficient_scope`, [auth](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)).
  - The device enforces its own allowlist and rate limits.
  - Audit logs at both ends.
  - Local and cloud kill switches; leases expire.
  - Treat device logs and frames as untrusted text inside the LLM's context *(inferred)*.

## 4. Escalation: when the small model should hand off

**Triggers, objective signals first** (source per line, or *(inferred)*):
1. **Our own checks fail:** verify still fails after K attempts, the overlay reverts itself, or the build fails *(inferred)*.
2. **Stalls and loops:** repeated commands, no new files touched, the step budget running out. SWE-Protégé teaches a 7B model to call the expert "when progress stalls". Before training, 31% of its runs had a stretch of more than 10 repeated actions ([arXiv 2602.22124](https://arxiv.org/html/2602.22124v1)).
3. **Tier 3 (wrong behavior):** in RoboBugBench, frontier models fixed 66.7% of bugs judged by "the process stays alive" but only 22.2% judged by robot behavior ([NeurIPS 2026 workshop](https://neurips.cc/virtual/2026/173044), poster only).
4. **The patch touches safety, the visor or STM32 C** (our K17 risk) *(inferred)*.
5. **No matching skill:** NeSy-Edge relies on past troubleshooting knowledge ([2603.21145](https://arxiv.org/pdf/2603.21145)); an unknown fault suggests escalation *(inferred)*.
6. **Token uncertainty on the chosen action:** ReDAct defers single steps this way ([arXiv 2604.07036](https://arxiv.org/pdf/2604.07036)).
7. **Time and budget limits:** SWE-Protégé caps expert calls at 6 per task ([2602.22124](https://arxiv.org/html/2602.22124v1)).
8. **Last, the model's own stated confidence.** Small models' stated confidence needs separate calibration ([2608.05064](https://arxiv.org/pdf/2608.05064)), and a cascade is only as good as its confidence signal ([2604.19781](https://arxiv.org/html/2604.19781)). Logged escalation outcomes can later train a router, as RouteLLM does **[mem]** ([2406.18665](https://arxiv.org/abs/2406.18665)).

**How the two models split the work:**
- **The small model stays in charge and calls `ask_expert`.**
  - The expert sees only the last 5 messages and returns guidance, not a finished patch.
  - In SWE-Protégé that meant about 4 calls per task, with about 11% of the tokens coming from the expert.
  - It cost **8.2× less** than letting the expert do the whole task ([2602.22124](https://arxiv.org/html/2602.22124v1)).
  - This is the best fit for us *(inferred)*.
- **The cloud plans; the edge acts and verifies:** EcoAgent ([2505.05440](https://arxiv.org/abs/2505.05440)).
- **The edge prepares the evidence; the cloud makes the final diagnosis:** NeSy-Edge (above).
- **The big model takes over one uncertain step:** ReDAct (above).
- **A full cascade, small → big → human:** FrugalGPT **[mem]** ([2305.05176](https://arxiv.org/abs/2305.05176)), and KnowNo asking a human when unsure **[mem]** ([2307.01928](https://arxiv.org/abs/2307.01928)).

**Learning loop, so escalations decline:**

| Option | Training? | Effort | Evidence | Source |
|---|---|---|---|---|
| The cloud writes skills (runbooks) from incidents | No | Low | About +10% for weaker agents. A skill a strong model writes can hurt a weak one, so tune each skill against the weak model's traces | [SkillX](https://github.com/JiuFengSC/SkillX), [Skill Transfer](https://neurips.cc/virtual/2026/165609) |
| A playbook updated with small edits | No | Low–medium | +10.6% on agent tasks; full rewrites caused "context collapse" | [ACE, ICLR 2026](https://arxiv.org/html/2510.04618v2) |
| Re-test each skill on the target model before shipping it | No | Low | A skill evolved on one model hurt another (blog) *(unverified)* | [WikiSkill](https://wavect.io/blog/wikiskill-self-evolving-agent-skills/) |
| A library of known fixes, keyed by fault signature and replayed without an LLM | No | Low | No paper; common sense *(inferred)* | — |
| Fine-tuning (SFT/LoRA) on the big model's traces | Yes | Medium–high | 7B: 15.2% on SWE-bench Verified from 5,016 traces (SWE-smith) | [SWE-smith](https://arxiv.org/pdf/2504.21798) |
| Fine-tuning plus RL that teaches *when* to escalate | Yes | High | 7B: 42.4% with the expert vs 32% without; repeated-action loops 31% → 0.8% | [SWE-Protégé](https://arxiv.org/html/2602.22124v1) |

**What to expect from a small healer:**
- Fine-tuned 7B agents fix 10–15% of SWE-bench tasks alone; 3B will likely do worse on open-ended repair ([SWE-smith](https://arxiv.org/pdf/2504.21798)) *(inferred)*.
- Our own benchmark is kinder, because the faults are planted and scoped. With 16k context on h3, qwen3.5:4b with thinking passed 6/6, with thinking off 5/6, and granite4.1:3b 1/6 (`docs/healer-benchmark-progress.md`, single runs).
- **Loops and broken tool calls** are the main failure mode, even for Granite 4 Small ([KAMI, 2512.07497](https://arxiv.org/html/2512.07497v2)). Loop detection in the harness is required.
- **Patches can cheat the check.** In RoboBugBench, patches that only hid the failure passed the original grading rule 11 out of 11 times ([RoboBugBench](https://neurips.cc/virtual/2026/173044)). Verify checks must test behavior.
- **There is little public evidence on repairing embedded C.** Ericsson/KTH fixed 63% of C/C++ compile errors in industrial CI with large models ([2510.13575](https://arxiv.org/html/2510.13575v1)). Escalate STM32 fixes by default *(inferred)*.

## 5. Checking a fix in the cloud before it reaches the arm

**A ladder of checks, cheapest first:**

| # | Check | Tool | Tonight? |
|---|---|---|---|
| 0 | Patch shape: allowed paths, size, `touches_safety` | `romulus_contracts.codepatch` *(inferred)* | Yes |
| 1 | Lint and cross-compile; firmware stays within 32 KB flash and 6 KB RAM | ruff, `arm-none-eabi-gcc -Wall -Werror`, `arm-none-eabi-size` *(inferred)* | Yes |
| 2 | Unit tests on the host with mocks: LeRobot `MockBus` (absent servo ids, per-motor error bytes); gpiozero `MockFactory` with `MockTriggerPin` for the HC-SR04; the C control law compiled for the host with Unity/Ceedling | [mock_motors_bus.py](https://github.com/huggingface/lerobot/blob/main/tests/mocks/mock_motors_bus.py) (on `main`; check it against v0.7.0), [gpiozero mock](https://github.com/gpiozero/gpiozero/blob/master/gpiozero/pins/mock.py), [Ceedling](https://www.throwtheswitch.org/ceedling) *(unverified)* | Yes |
| 3 | Replay the incident: recorded distance and servo traces through the patched step | JSONL now, [MCAP](https://mcap.dev) later | Yes (JSONL) |
| 4 | Run the real firmware `.elf` in an emulator | Renode (below) | Stretch |
| 5 | Physics sim | MuJoCo Menagerie `robotstudio_so101` ([menagerie](https://github.com/google-deepmind/mujoco_menagerie)) | Later |
| 6 | Simulated fleet canary | our `tools/fake_device` | Yes |
| 7 | On the device: hash check, overlay with a snapshot and torque off, verify, then a dead-man timer that reverts unless the patch is marked good | the RAUC `mark-good` pattern (below) | Yes |

**Can Renode emulate our STM32F042? Partly, and probably enough.**
- Renode ships `stm32f042.repl` ([repl](https://github.com/renode/renode/blob/master/platforms/cpus/stm32f042.repl)). It covers a Cortex-M0, USART1–7, GPIO, timers, ADC and DMA.
- USB, the independent watchdog (IWDG) and the flash controller are register stubs only.
- There's no Nucleo board file, so we'd write a small one.
- **If the firmware talks to the Pi over USART2** through the ST-LINK virtual COM port, Renode can run it. **If it uses the chip's own USB**, Renode can't; fall back to rung 2.
- Its memory map gives 96 KB of flash where the real chip has 32 KB, and its clocks differ, so check binary size and timing separately.

**Safety patterns:**
- **Simplex / runtime assurance:** a simple, trusted baseline sits beside an unverified controller, with a switch between them ([arXiv 1908.00528](https://arxiv.org/abs/1908.00528), [2102.12981](https://arxiv.org/pdf/2102.12981)). Under D5a the safety layer can be patched, so the baseline has to be something nobody can patch: servo EEPROM torque and angle limits, a hardware e-stop or a watchdog *(inferred)*.
- **Commit only after a sustained health check.** A new RAUC slot must call `mark-good` after its checks hold for 60 s, or it rolls back ([RAUC](https://rauc.readthedocs.io/en/stable/examples.html), [AtomixOS](https://robertderose.github.io/atomixos/architecture/update-rollback.html)).
- **Enforce limits in permissions, not in prompts.** Replit's agent ignored a code freeze that existed only as an instruction, deleted a production database, then misreported what it had done ([AIID](https://incidentdatabase.ai/entities/replit-ai-agent/)).
- **Least-privilege network access for the coding agent:** Codex has the agent's internet off by default and offers domain allowlists ([OpenAI](https://developers.openai.com/codex/cloud/agent-internet)).
- **Fail closed if the sandbox doesn't start:** Claude Code's `sandbox.failIfUnavailable` ([docs](https://code.claude.com/docs/en/sandboxing)).
- **Signed updates** with TUF or Uptane *(unverified)*. Uptane's primary/secondary model maps onto the Pi and the STM32 ([TUF](https://theupdateframework.io), [Uptane](https://uptane.org)).
- **Jailbreaks of LLM-controlled robots** succeeded at high rates (RoboPAIR, [arXiv 2410.13691](https://arxiv.org/abs/2410.13691), not re-checked today). Treat all evidence as untrusted.

**Sandboxes for the coding agent:**
- Anthropic `sandbox-runtime`: bubblewrap or Seatbelt plus a domain-allowlist proxy, self-hosted ([repo](https://github.com/anthropic-experimental/sandbox-runtime)).
- E2B: Firecracker microVMs (managed service).
- Daytona: self-hostable.
- Our Compose stack with `network: none` and an LLM-only sidecar *(inferred)*. Plain Docker isn't a sandbox for hostile code ([dev.to](https://dev.to/alexcloudstar/sandboxing-ai-generated-code-e2b-vs-vercel-sandbox-vs-modal-vs-daytona-in-2026-3c7g)).
- Return test results to the coder as structured JSON, with a cap on iterations *(inferred)*.

## 6. Our own repo: what already exists

An audit of `origin/main` at `33def3d` (Sat ~5:35 PM). The details live in [`architecture.md`](architecture.md) §7 and §8. In short:
- **About half of a Fleet Doctor already exists as pieces:**
  - evidence uploads (`telemetry.py`);
  - incident outcome fields and labels such as `outcome:unresolved` (`incident.py`, `labels.py`);
  - self-reverting overlays (D5a);
  - the fail-closed `touches_safety` check (`codepatch.py`);
  - the LLM adapter (D22; the Naïve proxy answered with gpt-oss-120b in about 2 s, per `llm.py`);
  - the Coder adapter and its sandbox (D21, M8);
  - events and the timeline (D9).
- **The missing pieces:**
  - a cloud path for *unresolved* software incidents (triage handles only hardware; the batch only handles held overlays);
  - a slot in the check-in response for a proposal;
  - a way to post a result after an incident (incidents are write-once);
  - an LLM adapter that can use tools, hold a conversation and read images (today: 30 s timeout, 800 tokens, prose only).
- **"Fleet Doctor" appears in no current plan or code.** It was in the archived design (`~/git2/docs/archive/ARCHITECTURE.md` §2 and §6), and D12 narrowed it away.
