# Research: outside facts for fleet learning

_Sun Nov 8, ~8:40 AM PST. Every outside fact for these docs lives here, with its link (the Fleet Doctor rule: one fact, one place). Facts from our own repo are cited where they're used. Two facts were re-checked by hand at 8:40 AM: the Hermes trajectory format and Atropos being archived._

## 1. The data flywheel, as others run it
| Who | The loop | What we take from it |
|---|---|---|
| Tesla data engine | Models run in shadow mode across the fleet. 221 hand-written triggers flag clips worth keeping, wrong predictions become new unit tests, and the model is retrained ([CVPR 2021 keynote summary](https://dynamically-typed.netlify.app/stories/2021/karpathy-autopilot-cvpr); shadow mode since 2016, [IEEE Spectrum](https://spectrum.ieee.org/tesla-autopilot-data-deluge)) | "A failure becomes a test" is our `sync_regression_cases` |
| NVIDIA Data Flywheel Blueprint | Log ingestion → tag by workload → dedupe and curate → LoRA fine-tune of a smaller student → evaluate with an LLM judge. **Admins then promote by hand** ([NVIDIA](https://developer.nvidia.com/blog/build-efficient-ai-agents-through-model-distillation-with-nvidias-data-flywheel-blueprint)) | Teacher → student distillation is the weights roadmap. The human promote step is the one we replace with a gate |
| Physical Intelligence π\*0.6 (RECAP) | Trains on demonstrations, autonomous rollouts and expert corrections; "the intervention provides supervision for the situations that the policy actually puts the robot into" ([arXiv 2511.14759](https://arxiv.org/abs/2511.14759), [blog](https://pi.website/blog/pistar06)) | Human interventions (the Technician's case, an approve or deny) are the most valuable rows |
| Open X-Embodiment / RT-X | 22 robot types, 21 institutions, 527 skills in "standardized data formats"; RT-X "exhibits positive transfer… by leveraging experience from other platforms" ([arXiv 2310.08864](https://arxiv.org/abs/2310.08864)) | Standard formats make one fleet's data useful to others |
| Waymo | Replays each disengagement in simulation to see what would have happened, and builds changes from it ([arXiv 2011.00038](https://arxiv.org/pdf/2011.00038)) | Our replay plus simulated canary |

**The canonical loop** *(inferred from the rows above)*: trigger → mine → label → train → evaluate → deploy. A laptop demo can honestly show trigger → mine → automatic label (the gate's verdict) → evaluate (replay + canary) → deploy. "Train" is the weakest step to show live.

## 2. Who else turns robot traces into something
| Who | What it does | Does a verified fix reach the fleet without a person? |
|---|---|---|
| Foxglove | An agentic data platform for physical AI: search, curation, and an in-app agent ("Find episodes similar to this one and add them to a training dataset") ([Aug 2026](https://foxglove.dev/blog/introducing-the-agentic-data-platform-for-physical-ai)) | No: it doesn't deploy |
| Roboto AI | "Analytics engine for robotics"; one repo converts logs to LeRobot ([GitHub](https://github.com/roboto-ai)) | No |
| Alloy ($8M, Aug 2026) | Agents find anomalies and recurring failures; an MCP server for Claude Code and Codex ([RoboticsTomorrow](https://www.roboticstomorrow.com/content.php?post=26949)) | No: it diagnoses |
| Formant | Exports telemetry to a data lake; "train then push via API" appears only as an example workflow ([Robotics 24/7](https://www.robotics247.com/article/how_robotics_companies_actually_use_data_and_what_we_got_wrong)) | No |
| **Viam** | Capture → label → train → model registry → fleet rollout by fragment, with a suggested canary group ([docs](https://docs.viam.com/vision/deploy-and-maintain/retrain/)) | **Partly.** Accuracy monitoring "is not automatic on Viam today", and promotion is manual |
| Scale AI + Universal Robots | UR AI Trainer (Mar 2026): leader–follower demonstrations feed a labeling and versioning "data flywheel" ([Control Design](https://www.controldesign.com/industry-news/news/55367423/universal-robots-and-scale-ais-ur-ai-trainer-captures-data-for-robotics-ai-development)) | No: human demonstrations |
| Encord ($60M, Feb 2026) | Curation, annotation and model evaluation for physical AI ([SiliconANGLE](https://siliconangle.com/2026/02/26/physical-ai-data-infrastructure-startup-encord-lands-60m-accelerate-intelligent-robot-drone-development/)) | No |
| Metriplane v0.3.0 (Aug 2026) | Turns recorded workcell incidents into replayable regression tests ([ROS Discourse](https://discourse.openrobotics.org/t/metriplane-v0-3-0-turn-recorded-workcell-incidents-into-repeatable-regression-tests/57409)) | No: no deploy, no training. The closest to our replay step |

**The gap** *(inferred)*: nobody automatically promotes a fix found on one device to the whole fleet through a verify gate, and nobody uses that gate's verdicts as training labels.

## 3. Formats
- **Hermes Agent trajectories** ([Hermes docs](https://hermes-agent.nousresearch.com/docs/developer-guide/trajectory-format), checked 8:40 AM):
  - One JSONL line per run: `conversations`, `timestamp`, `model`, `completed`.
  - **Completed runs go to `trajectory_samples.jsonl`; failed or interrupted runs to `failed_trajectories.jsonl`.**
  - Each call is a `<tool_call>{"name", "arguments"}</tool_call>` block inside the `gpt` turn. Results are grouped into one `tool` turn as `<tool_response>` JSON.
  - Every `gpt` turn has a `<think>` block (empty if there was no reasoning). A generated system message carries a `<tools>` block.
  - Batch runs add `tool_stats` and `tool_error_counts`.
- **TRL dataset types** ([TRL](https://huggingface.co/docs/trl/main/en/dataset_formats)): `messages` + `tools` for SFT; `prompt/chosen/rejected` for DPO; `prompt/completion/label` for KTO.
- **KTO** learns "from a binary signal of whether an output is desirable" ([arXiv 2402.01306](https://arxiv.org/abs/2402.01306)). It fits a pass/fail gate verdict natively.
- **LeRobot v3.0** (in `lerobot >= 0.4.0`, so it works with our v0.7.0 pin) ([docs](https://huggingface.co/docs/lerobot/lerobot-dataset-v3)):
  - Layout: `meta/info.json`, `meta/stats.json`, `meta/tasks.parquet`, chunked `meta/episodes/`, `data/` parquet shards, `videos/` MP4.
  - Write path: `LeRobotDataset.create` → `add_frame` → `save_episode` → **`finalize()`** (skipping it corrupts the parquet) → `push_to_hub`.
  - v2.1 converts with `convert_dataset_v21_to_v30` ([porting guide](https://huggingface.co/docs/lerobot/porting_datasets_v3)).
- **RLDS:** episodes of steps, with `is_first` and `is_last` mandatory and `reward`, `is_terminal` and `discount` optional ([google-research/rlds](https://github.com/google-research/rlds)).
- **Atropos** (Nous's RL environment framework) **was archived on Jul 4, 2026** and is read-only ([GitHub](https://github.com/NousResearch/atropos), checked 8:40 AM). Its `atropos-sft-gen` and `atropos-dpo-gen` turned rollouts into SFT and DPO sets with rejection sampling. Hermes Agent's RL training still runs on Tinker-Atropos ([Hermes docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/rl-training)).
- **Hugging Face Hub** has an `rl-environment` dataset tag ([HF dataset cards](https://huggingface.co/docs/hub/datasets-cards)).
- **OpenAI fine-tuning** is winding down, and new users are closed out ([OpenAI](https://developers.openai.com/api/docs/guides/fine-tuning-best-practices)). So don't target it.

## 4. Learning from verified outcomes
- **RLVR (Tülu 3):** SFT → DPO → "Reinforcement Learning with Verifiable Rewards", where a deterministic checker replaces the reward model ([arXiv 2411.15124](https://arxiv.org/abs/2411.15124)).
- **STaR, expert iteration:** generate, keep only what checks out, fine-tune, repeat ([arXiv 2203.14465](https://arxiv.org/abs/2203.14465)).
- **SWE-Gym:** unit tests label agent trajectories. Fine-tuning on the successful runs improved SWE-Bench Lite by up to 19.7% (a 32B model, 491 trajectories), and the same labels train a verifier ([arXiv 2412.21139](https://arxiv.org/abs/2412.21139)).
- **Small, narrow models:** a 3B model fine-tuned for one narrow role, with a harness built for it, scored 94.5% against a 72B baseline's 95.5% on 200 held-out tasks (McNemar p = 0.82), at 84.8% lower cost per success ([IshaanAyaan/slm-agents](https://github.com/IshaanAyaan/slm-agents), via `docs/project-overview.md:39`).

## 5. A fix found once, rolled out safely
- **Google SRE:** a canary is "a partial and time-limited deployment of a change… and its evaluation". If error rates drift, "pause and roll back the deployment, or perhaps contact a human" ([SRE Workbook ch. 16](https://sre.google/workbook/canarying-releases/)).
- **Kayenta:** Netflix and Google's open-source automated canary judge ([Google Cloud](https://cloud.google.com/blog/products/gcp/introducing-kayenta-an-open-automated-canary-analysis-tool-from-google-and-netflix)).
- **Mender:** A/B partitions that fall back on a failed boot; phased rollouts, typically 5% → 15% → 80% ([how it works](https://mender.io/how-it-works), [phased](https://mender.io/blog/managing-fleets-of-connected-devices-with-phased-rollout)).
- **Google Play:** staged rollouts can be halted ([Play help](https://support.google.com/googleplay/android-developer/answer/6346149)).

## 6. Why only verified traces become training data
- **Collapse:** "indiscriminate use of model-generated content in training causes irreversible defects" ([Shumailov et al., Nature 2024](https://oatml.cs.ox.ac.uk/publications/202407_Shumailov_ModelCollapse.html)). "Verifiers, even imperfect ones, can indeed be harnessed to prevent model collapse" ([arXiv 2406.07515](https://arxiv.org/abs/2406.07515)).
- **Provenance:** EU AI Act Art. 10 requires data governance covering "data collection processes and the origin of data" ([Art. 10](https://artificialintelligenceact.eu/article/10/)). Hugging Face dataset cards document sources and biases ([HF](https://huggingface.co/docs/hub/datasets-cards)). DVC: "Manage data the way code is managed" ([dvc.org](https://dvc.org/)).
- **Customer sites:** Foxglove lets enterprises keep raw data in their own cloud storage ([Foxglove](https://www.foxglove.dev/blog/tag/product-release)). Strip customer details before a trace becomes a permanent test ([Arthur](https://www.arthur.ai/column/regression-test-datasets-ai-agents-production-failures), a vendor blog).

## 7. Lines we can use, each with its source
1. "Tesla's data engine turned every wrong prediction into a new unit test; Romulus turns every incident into a replay test before any fix ships." (§1, Tesla)
2. "Google defines a canary as 'a partial and time-limited deployment of a change… and its evaluation'; we run that evaluation in simulation before any real arm sees the fix." (§5)
3. "As in RLVR, the reward comes from a verifier, not a judge's opinion: the replay and canary verdict labels every heal pass or fail." (§4)
4. "Only gate-verified traces become training data, because training on unchecked model output causes collapse, and verification prevents it." (§6)
5. "Foxglove, Roboto and Alloy turn robot logs into insight; Viam ships retrained models but promotes them by hand; Romulus closes the loop with an automatic gate." (§2)
