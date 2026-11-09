# Small local models for the healer's worker/monitor role (report for the lead, Sun Nov 8 2026)

Vendor benchmark numbers are self-reported unless I say otherwise. My own conclusions are marked *(inferred)*. I edited no repo files.

## Bottom line
- **Keep qwen3.5:4b until something beats it on the team's own tests.** It made 0 invalid tool calls on the bench (`firmware/harness/HANDOFF.md:11-13`, `:21`). Its median turn on the laptop is 3.4 s, p90 10.2 s, over 166 turns (`firmware/harness/RESULTS.md:67-68`). It passed 10 of 12 on the h3 bench (`docs/healer-benchmark-progress.md:61`). Every challenger below has vendor numbers only. The ranking below is a test order, not a recommendation to switch tonight.
- **The local model has two jobs, and public benchmarks cover only one** *(inferred)*:
  - **Monitor:** read a window of telemetry every few seconds and return one JSON verdict (healthy, or which fault, plus a reason).
  - **Worker:** take the manager's plan and make one allowlisted tool call (`docs/healer-data-levels.md:15-19`).
  - BFCL, τ² and τ³, and ToolSandbox all measure the worker job. Nothing public measures the monitor job. The real test for both is the team's fault factory plus `evaluate.py`.
- **The Pi 5 can't run the current agent harness with any of these models, and probably can't run an LM monitor every few seconds either** *(inferred)*. Section 1 has the numbers. On the Pi, per-tick checks should stay plain code or a classifier. The LM should wake only when a check flags something, like `agent.py watch` with its 10 s debounce (`HANDOFF.md:58-59`). The laptop stays the worker's home, which matches James's call to "pick for a laptop-class machine" (`docs/healer-data-levels.md:24`).
- **New candidates worth testing:**
  - **LFM3-1.7B.** Released Oct 27 2026, about 1 GB, post-trained for Hermes Agent harnesses, vendor BFCL V4 of 71.39.
  - **Qwen3.5-2B.** Same family and template as the baseline, and it has a measured Pi number.
  - **On a GPU:** Qwen3.5-9B, and LFM2.5-24B-A2B (also post-trained in Hermes Agent).
  - **Monitor only:** d1-2B, a decision model that returns calibrated probabilities with zero output tokens.
- **Time-series pre-filters:** the team's own L2 run already answers most of the question (Section 4). Forecast models can't see a frozen sensor, and the planned L3a gradient-boosted trees are the simpler path *(inferred)*.

## 1. Pi 5 speed decides the design
- **Measured on a Pi 5 8 GB** (Ollama, Q4_K_M, active cooler; [bestllmfor.com](https://bestllmfor.com/guide/llm-local-raspberry-pi-edge/), updated 2026-08-27):

  | Model | Generation | Prompt eval |
  |---|---|---|
  | qwen3.5:4b | 2.3 tok/s | 3.6 tok/s |
  | qwen3.5:2b | 4.4 tok/s | 6.6 tok/s |
  | Granite 4.2 3B | 3.4 tok/s | 5.1 tok/s |
  | Gemma 4 E2B | 3.9 tok/s (4.3 GB RAM) | not given |

- **A conflicting number:** TinyLlama-1.1B Q4_0 on a Pi 5 is reported at about 108 tok/s prefill ([tinyweights.dev](https://tinyweights.dev/posts/run-llms-raspberry-pi-5/), seen only through a search snippet). That is a 15–30× gap.
  - A possible cause is that Qwen3.5's Gated DeltaNet hybrid has no fast ARM CPU prefill kernels yet *(inferred)*. Run `ollama run <tag> --verbose` on the Pi before deciding anything.
- **The arithmetic** *(inferred)*:
  - The harness prompt is about 4.8k tokens: 2.3k of tool schemas plus a 2.5k briefing (`firmware/harness/README.md:17-19`). Runs grow to 5k–16k (`docs/healer-benchmark-progress.md:106`).
  - At 3.6 to 108 tok/s, the first turn takes between 22 minutes and 45 seconds.
  - A 400-token monitor prompt takes 61–111 s at the Qwen3.5 rates. Generating a 60-token verdict adds about 14 s at 4.4 tok/s.
  - So a Pi-resident LM can manage about one check a minute at best. It needs a few hundred tokens of precomputed window stats, not raw logs. Only d1 avoids the generation cost, because it produces no output tokens.

## 2. Ranked shortlist: who each model is

| # | Model | Job | Params | Q4 size (Ollama) | License | Ollama tag / HF id | Tools via Ollama |
|---|---|---|---|---|---|---|---|
| 0 | **Qwen3.5-4B (baseline)** | both | 4B | 3.3 GB `qwen3.5:4b-q4_K_M` | Apache-2.0 | `qwen3.5:4b` / `Qwen/Qwen3.5-4B` | yes (family: tools, thinking) [tags](https://ollama.com/library/qwen3.5/tags) |
| 1 | **LFM3-1.7B** | both | 1.73B | 1.1 GB `lfm3:1.7b-q4_K_M`; 1.0 GB `-qad-q4_0` | LFM Open v1.0: commercial use only under $10M a year ([LICENSE §1, §5](https://huggingface.co/LiquidAI/LFM3-1.7B/blob/main/LICENSE)) | `lfm3:1.7b` / `LiquidAI/LFM3-1.7B` | yes (tools, thinking). The model emits XML calls ([card](https://huggingface.co/LiquidAI/LFM3-1.7B)) |
| 2 | **Qwen3.5-2B** | both | 2B | 1.9 GB `qwen3.5:2b-q4_K_M` | Apache-2.0 | `qwen3.5:2b` / `Qwen/Qwen3.5-2B` | yes |
| 3 | **Qwen3.5-9B** (GPU or 16 GB Mac) | worker | 9B | 6.6 GB `qwen3.5:9b-q4_K_M` | Apache-2.0 ([repo](https://huggingface.co/Qwen/Qwen3.5-9B)) | `qwen3.5:9b` / `Qwen/Qwen3.5-9B` | yes |
| 4 | **LFM2.5-24B-A2B** (GPU) | worker | 24B total / 2.45B active | 15 GB `lfm2.5:24b` | LFM Open v1.0 | `lfm2.5:24b` / `LiquidAI/LFM2.5-24B-A2B` | yes (tools, thinking) [page](https://ollama.com/library/lfm2.5) |
| 5 | **d1-2B** | monitor only | 1.92B | 3.9 GB `d1:2b` (note: `d1:3b` is 3.7 GB) | LFM Open v1.0 | `d1:2b` / `LiquidAI/d1-2B` | no tools. Uses its own `/v1/systemone` endpoint ([page](https://ollama.com/library/d1)) |
| 6 | MiniCPM5-2B | worker | 2.52B total / 1.98B non-embedding | 1.6 GB `openbmb/minicpm5-2b` | Apache-2.0 | `openbmb/minicpm5-2b` / `openbmb/MiniCPM5-2B` | page claims native tools ([page](https://ollama.com/openbmb/minicpm5-2b)), but search shows only a "Thinking" badge. XML calls; the card recommends SGLang's parser ([card](https://huggingface.co/openbmb/MiniCPM5-2B)) |
| 7 | LFM2.5-1.2B-Instruct | both (light) | 1.17B | 731 MB Q4_K_M (HF GGUF) | LFM Open v1.0 | `hf.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF:Q4_K_M` / `LiquidAI/LFM2.5-1.2B-Instruct` | no library tag (the library has only `lfm2.5-thinking:1.2b`). Pythonic calls by default, JSON on request ([card](https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct)) |
| 8 | Nemotron 3.5 Bolt E4B (laptop GPU) | worker | 8B total / 4B effective | 7.9 GB `nemotron-3.5-bolt:e4b-q4_K_M` | OpenMDW-1.1 | `nemotron-3.5-bolt:e4b` / `nvidia/NVIDIA-Nemotron-3.5-Bolt-E4B-BF16` (also `-4GB`) | yes ([tags](https://ollama.com/library/nemotron-3.5-bolt/tags)) |
| 9 | Granite 4.2 3B (re-test) | worker | 3B | 2.2 GB `granite4.2:3b` | Apache-2.0 | `granite4.2:3b` / collection `ibm-granite/granite-42-language-models` | yes; thinking on by default ([page](https://ollama.com/library/granite4.2)) |
| 10 | Qwen3.6-35B-A3B (24 GB GPU) | worker / manager class | 35B / 3B active | 24 GB `qwen3.6:35b-a3b-q4_K_M` | Apache-2.0 | `qwen3.6:35b-a3b` / `Qwen/Qwen3.6-35B-A3B` | yes ([tags](https://ollama.com/library/qwen3.6/tags)) |
| 11 | Gemma 4 E2B / E4B | worker | 2.3B / 4.5B effective (5.1B / 8B with embeddings) | 4.6 GB / 6.6 GB | Apache-2.0 per the HF card | `gemma4:e2b`, `gemma4:e4b` / `google/gemma-4-E2B-it` | tools is listed at family level only ([tags](https://ollama.com/library/gemma4/tags)) |

- **Tool-call format check:**
  - With an Ollama library tag that has the tools capability, the harness's `OllamaDriver` (`agent.py:543`) should receive normalized `tool_calls` whatever the model's native format (XML, Pythonic or `<tool_call>`) *(inferred)*.
  - With `hf.co/...GGUF` pulls or llama.cpp, it depends on the chat template inside the file.
  - Either way, run `server/pi_check.py --model <tag>` before any run. It already tests one tool call with thinking on (`server/README.md:22`).
- **New-release caveat:** lfm3, d1, nemotron-3.5-bolt and qwen3.6 were released only weeks ago. Check `ollama --version` on the laptop and in the container first.

## 3. Ranked shortlist: the evidence
Each cell gives the benchmark version and its source. A v3 score and a v4 score are not comparable.

| # | Tool-use and instruction-following benchmarks | Pi 5 tok/s | Mac tok/s | Fits because / doesn't because |
|---|---|---|---|---|
| 0 | Three reproductions disagree:<br>• Qwen card: BFCL-V4 50.3, TAU2 79.9, IFEval 89.8 ([card](https://huggingface.co/Qwen/Qwen3.5-4B))<br>• NVIDIA's reproduction: BFCL-v3 70.95, Tau2 75.06 ([Bolt card](https://huggingface.co/nvidia/Nemotron-3.5-Bolt-E4B))<br>• OpenBMB's reproduction: BFCL v4 56.8, τ²-Telecom 92.1 | 2.3 gen / 3.6 prompt (bestllmfor) | 65.8 gen, M5 Pro, llama.cpp ([llmcheck](https://llmcheck.net/benchmarks)) | Proven on the team's bench, so it's the bar. Too slow on the Pi (`server/README.md:3`). |
| 1 | Liquid card: BFCL V4 71.39 (its Qwen3.5-9B reference: 62.83), ToolSandbox 80.36, τ³ average 68.01, IFEval 96.66 | not found | card text: at least 1.8× faster decode and 2.5× faster prefill than its comparison models on M5 Max at 32K context; the actual numbers are only in a chart | Smallest strong tool caller; 256K context; post-trained for "Hermes Agent, Pi, and OpenClaw". Risks: 12 days old, vendor numbers only, and "not recommended for … complex multi-step reasoning" ([Ollama page](https://ollama.com/library/lfm3)). |
| 2 | Qwen card, thinking: BFCL-V4 43.6, TAU2 48.8, IFEval 78.6 (61.2 non-thinking) ([card](https://huggingface.co/Qwen/Qwen3.5-2B)) | 4.4 / 6.6 (bestllmfor) | not found | Drop-in for the harness (same template). Non-thinking by default; the card warns that thinking mode is "more prone to entering thinking loops". |
| 3 | Qwen card: BFCL-V4 66.1, TAU2 79.1, IFEval 91.5 | doesn't fit well (6.6 GB of 8 GB) *(inferred)* | not found on a laptop chip | Strongest Qwen worker that fits an 8 GB laptop GPU or a 16 GB Mac *(inferred)*. |
| 4 | Liquid card: BFCLv4 65.01 (Qwen3.6-35B-A3B 65.00), IFEval 96.75, LiveClaw 48.09 ([card](https://huggingface.co/LiquidAI/LFM2.5-24B-A2B)) | no | 184 tok/s on M5 Max (card) | Trained with RL inside Hermes Agent; the card gives `hermes config set model.provider custom`. Fast because only about 2.45B parameters are active. |
| 5 | Liquid card: DecisionBench v1 77.1, Decision Index public 51.1. Not a tool-calling model. | not found | 18.6 ms per question, M5 Pro bf16 ([card](https://huggingface.co/LiquidAI/d1-2B)) | Fits "healthy or which fault" exactly: `choice` and `noul` (yes/no) answers with probabilities, in one forward pass, no generation. The card says to calibrate thresholds on your own data. |
| 6 | OpenBMB card: BFCL v4 66.6, τ²-Telecom 97.1, IFEval 86.7; Terminal-Bench 8.6 against 25.8 for Qwen3.5-4B | not found | not found | Strong at short tool exchanges, weak at long agent runs. The tool path through Ollama is unverified. |
| 7 | Liquid card: BFCLv3 49.12, IFEval 86.23 | not found¹ | not found; 70 tok/s decode on a Galaxy S25 Ultra CPU and 239 tok/s on an AMD CPU (card) | Smallest usable option. 32K context is enough for a monitor. |
| 8 | NVIDIA card: BFCL-v3 68.51 (Q4: 67.31), ToolSandbox 68.77, Tau2 55.10, all below its own Qwen3.5-4B row. Third-party test: 60 of 60 correct tool choices, 95% exact arguments with thinking off ([HackerNoon](https://hackernoon.com/running-nvidia-nemotron-35-bolt-e4b-without-a-gpu-benchmark-results)) | no (7.9 GB) | 26.6 on M4 16 GB (llama.cpp Q4_K_M); 73.0 on M3 Max (MLX 4-bit), both community (llmcheck) | Laptop-class only. Doesn't beat the baseline on its own vendor's table. |
| 9 | OpenBMB card: BFCL v4 52.2, IFEval 93.7. Granite 4.1 3B scores BFCL v3 60.80 ([IBM card](https://huggingface.co/ibm-granite/granite-4.1-3b)) but passed only 1 of 6 here | 3.4 / 5.1 (bestllmfor) | not found | Retest only because 4.2 is new; 4.1 failed this team's bench. |
| 10 | Qwen card: TAU3 67.2, MCP-Atlas 62.8 ([card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)) | no | not found (the 3.5 version of this model is 162 on M5 Ultra, community) | Too big for the worker. Belongs in the manager tier. |
| 11 | Google card: Tau2 42.2 (E4B) / 24.5 (E2B) ([card](https://huggingface.co/google/gemma-4-E2B-it)). NVIDIA's reproduction: BFCL-v3 66.39 (E4B) | E2B: 3.9 gen, 4.3 GB RAM | E4B: 62.2, M5 Pro llama.cpp (llmcheck) | Large for what it scores. The team already skipped E4B for size (`healer-benchmark-progress.md:85-86`). |

¹ For reference only (models of this class, not this model): 1–1.5B models run at about 5–15 tok/s on a Pi 5 ([arXiv 2511.07425](https://arxiv.org/html/2511.07425v1)), and gemma3:1b at 11.5 tok/s ([Stratosphere](https://stratospherelinuxips.readthedocs.io/en/develop/immune/research_rpi_llm_performance.html)).

- **Tested here and failed:** granite4.1:3b passed 1 of 6 and qwen3:4b-instruct-2507 passed 3 of 6 (`docs/healer-benchmark-progress.md:63-64`). Don't re-propose them.
- **Also considered:**
  - **Qwen3.5-0.8B:** too weak for the worker job (BFCL-V4 25.3, TAU2 11.6, per the Qwen 2B card).
  - **FunctionGemma 270M** (`functiongemma:270m`, 301 MB): only a base for fine-tuning, "not intended for use as a direct dialogue model" ([page](https://ollama.com/library/functiongemma)).
  - **`lfm2.5:8b`** (8B-A1B, 5.2 GB): the page gives no benchmark numbers in text.

## 4. Time-series pre-filters

**The L2 run already gives the main answer.** ChatTS-14B ran on a Runpod L40S for about $0.11 in total.
- It missed a frozen trace shown alone: "The sensor appears functional … No anomalies detected." (`prototypes/arm-sim/level2/results/chatts_results.jsonl:2`)
- It caught the same trace when shown beside a healthy one (`:5`; `docs/healer-data-levels.md:40-53`).

**Why forecasters won't help with a frozen sensor** *(inferred)*:
- A forecaster fed a frozen history predicts a flat line, so the residual is zero and nothing fires. That is why `us_frozen` is plain code (`armsim/level1.py:30`).
- Offset and scale faults need the kinematic prediction, and `check_distance_residual` (`level1.py:47`) already computes that residual. So L3a's gradient-boosted trees on those features are the simpler path.
- The one place a zero-shot model adds something is the real arm, until `real_calibration.json` exists. Until then there is no kinematic prediction (`prototypes/arm-sim/README.md:41-44`).

| Model | Params | License | HF id / install | Notes for us |
|---|---|---|---|---|
| Chronos-2 | 120M | Apache-2.0 | `amazon/chronos-2`, `pip install "chronos-forecasting>=2.0"` ([card](https://huggingface.co/amazon/chronos-2)) | Multivariate, with known-future covariates. Feed the commanded pose or predicted distance as a covariate and flag readings outside the forecast quantiles *(inferred design)*. Runs on CPU or GPU. |
| TiRex-2 | 82.5M (38.4M univariate) | Apache-2.0 | `NX-AI/TiRex-2`, installed from GitHub with Pixi ([card](https://huggingface.co/NX-AI/TiRex-2)) | Covariates, streaming, runs on CPU. |
| Toto 2.0 | 4M / 22M (up to 2.5B) | Apache-2.0 | `Datadog/Toto-2.0-4m` or `-22m`, `pip install toto-models` ([card](https://huggingface.co/Datadog/Toto-2.0-22m)) | Multivariate, quantiles 0.1–0.9. The card names the 4M model for edge and CPU. |
| TSPulse-r1 | about 1M | Apache-2.0 | `ibm-granite/granite-timeseries-tspulse-r1`, `pip install granite-tsfm` ([card](https://huggingface.co/ibm-granite/granite-timeseries-tspulse-r1)) | Anomaly detection and classification without a GPU. Anomaly mode wants 1,536–2,048 points, and classification resamples to 512, against Olir's 300-reading sample. |
| ChatTS-8B | about 8B (Qwen3-8B base) | Apache-2.0 | `bytedance-research/ChatTS-8B` via transformers ([card](https://huggingface.co/bytedance-research/ChatTS-8B)) | An optional cheaper L2 rerun, always with a reference window. Roughly 16 GB in fp16 *(inferred: 8B × 2 bytes)*. |
| TimesFM 2.5 | 200M | Apache-2.0 | `google/timesfm-2.5-200m-pytorch` ([card](https://huggingface.co/google/timesfm-2.5-200m-pytorch)) | Secondary sources say TimesFM 3.0 and Moirai 2.0 are non-commercial ([presenc.ai](https://presenc.ai/research/best-open-weight-time-series-models-2026), unverified), so skip those two. |

## 5. What to benchmark (4 core, 2 optional) and how

**CPU, in the Linux container (2B or under):**
```
ollama pull lfm3:1.7b-q4_K_M
ollama pull qwen3.5:2b-q4_K_M
ollama pull qwen3.5:4b                     # baseline, gives the CPU-to-laptop scale
# optional, monitor only:
ollama pull d1:2b                          # POST /api/v1/systemone {model, state, questions}
# optional, smallest:
ollama pull hf.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF:Q4_K_M
```

**GPU, one 48 GB Runpod pod at no more than $0.79/h** (`docs/healer-data-levels.md:81`):
```
ollama pull qwen3.5:9b
ollama pull lfm2.5:24b
# optional, laptop-GPU class:
ollama pull nemotron-3.5-bolt:e4b-q4_K_M
```

**Serve everything through Ollama** (`OLLAMA_HOST=0.0.0.0 ollama serve`), so the comparison measures the model and not the server *(inferred)*. vLLM alternatives:
- `vllm serve Qwen/Qwen3.5-9B --max-model-len 32768 --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder`. The Qwen card asks for vLLM nightly, and recommends a context of 128K or more to keep thinking quality.
- `vllm serve LiquidAI/LFM2.5-24B-A2B`
- Bolt needs vLLM v0.31.0 with the Gemma4 parsers.

**Cost:** one pod with a 90-minute cap comes to about $1.20, well inside the $10 *(inferred)*.

**The monitor test** *(inferred design)*:
1. **Data:** run `python -m armsim.factory --n 2000`. The labels are none, us_frozen, us_offset, us_scale, us_noisy, joint_stuck, joint_offset and joint_reversed (`factory.py:23-24`).
2. **Prompt:** give each model a window summary of about 400 tokens or less, the level-1 findings, and a healthy reference window (the L2 lesson).
3. **Output:** use Ollama's `format` with a JSON schema (`{verdict, fault, reason}`), so the score is verdict accuracy rather than JSON validity ([Ollama](https://ollama.com/blog/structured-outputs)).
4. **Gate:** 0 false alarms on `none`, the same gate as L3a. Calibrate d1 on half the runs and test it on held-out whole runs.
5. **Record:** p50 and p90 latency, plus the prompt and generation token counts and rates the driver already logs (`agent.py:626`).

**The worker test:** run `evaluate.py --scenarios 05,01,02,03,06` and arm07 with `--model <tag>`, in thinking and non-thinking modes, in the same format as `RESULTS.md`.

**Optional extra component:** LFM3-1.7B and LFM2.5-24B-A2B are both post-trained inside Hermes Agent harnesses. A local Hermes profile on the laptop could therefore serve as an offline fallback manager *(inferred)*. The main manager model stays James's call (ChatGPT 6 "sol", `docs/healer-data-levels.md:20`).