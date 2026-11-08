import { ext, hermesDoc, local, naiveDoc, repo, type Layer, type Src } from "./meta";

export type ToolCat = "Hosts" | "Sponsor tracks" | "Sponsors" | "Compute and models" | "Robot and sim" | "Edge hardware";
export type ToolUse = "core" | "planned" | "candidate" | "not used" | "reference";

export interface Tool {
  name: string;
  cat: ToolCat;
  layer: Layer;
  use: ToolUse;
  what: string;
  role: string;
  cost: string;
  src: Src;
}

export const TOOL_CATS: ToolCat[] = ["Hosts", "Sponsor tracks", "Sponsors", "Compute and models", "Robot and sim", "Edge hardware"];

export const TOOLS: Tool[] = [
  // Hosts
  { name: "Naïve", cat: "Hosts", layer: "naive", use: "core", what: "Platform for agent-run companies: agents, board, personas, vault, apps, CRM, payments.", role: "Runs the business and the job board. The SDK is required.", cost: "$250 credits per hacker", src: naiveDoc("api/overview") },
  { name: "Hermes Agent", cat: "Hosts", layer: "hermes", use: "core", what: "Nous Research's agent with plugins, hooks, cron, gateway and custom model providers.", role: "Runs at the bench and drives the arm through our plugin; the plugin is the Nous entry.", cost: "Plugin Hackathon: $20,000 cash total", src: hermesDoc("developer-guide/plugins") },
  // Sponsor tracks
  { name: "Mobilerun", cat: "Sponsor tracks", layer: "neutral", use: "planned", what: "AI agents that operate real Android and iOS phones; cloud phones, live view, SMS and mail add-ons.", role: "Phones in parts orders or technician visits after a hardware fault (Romulus demo).", cost: "Developer plan per team · track: 1st 3 virtual iPhones ($8,520)", src: repo("docs/sponsors.md") },
  { name: "AgentMail", cat: "Sponsor tracks", layer: "neutral", use: "planned", what: "Email inbox API for agents to send, receive and act on email; MCP with 47 tools.", role: "Sends the incident report. MCP auth unconfirmed (x-api-key vs bearer).", cost: "track: 1st $500 gift card + 6 months free", src: repo("docs/sponsors.md") },
  { name: "Monid", cat: "Sponsor tracks", layer: "neutral", use: "not used", what: "\"OpenRouter for agent tools\": 2,981 tools and APIs behind one integration, pay as you go.", role: "None yet. Its MCP uses OAuth; Naïve needs a bearer key or a wrapper.", cost: "$1 free · track: 1st $1,500 credits + $1,500 Visa", src: repo("docs/sponsors.md") },
  // Sponsors
  { name: "Stripe", cat: "Sponsors", layer: "neutral", use: "planned", what: "Payments; Naïve store apps take cards on the owner's own Stripe account.", role: "Payment for arm jobs. Needs owner onboarding first.", cost: "$500 credits + incorporation fee waived (inferred)", src: repo("docs/sponsors.md") },
  { name: "OpenAI", cat: "Sponsors", layer: "compute", use: "planned", what: "GPT models through the API.", role: "Benchmark LLM and a candidate model for Hermes; Naïve agents use Naïve's own router.", cost: "$100 API credits", src: repo("docs/sponsors.md") },
  { name: "Luxor", cat: "Sponsors", layer: "compute", use: "candidate", what: "GPU compute and AI infrastructure.", role: "A sponsor alternative to RunPod for training or the bench model.", cost: "none listed", src: repo("docs/sponsors.md") },
  { name: "OpenRouter", cat: "Sponsors", layer: "neutral", use: "not used", what: "One API across hundreds of LLMs.", role: "None yet. Hermes supports it as a provider.", cost: "none listed", src: repo("docs/sponsors.md") },
  { name: "HubSpot", cat: "Sponsors", layer: "neutral", use: "not used", what: "CRM for marketing, sales and service.", role: "None yet; Naïve has its own CRM.", cost: "none listed", src: repo("docs/sponsors.md") },
  { name: "Vercel", cat: "Sponsors", layer: "neutral", use: "not used", what: "Deploys and scales web apps.", role: "None yet; Naïve hosts apps too.", cost: "none listed", src: repo("docs/sponsors.md") },
  { name: "Neon", cat: "Sponsors", layer: "neutral", use: "not used", what: "Serverless Postgres with branching and scale-to-zero.", role: "None yet; Naïve apps include a managed database.", cost: "none listed", src: repo("docs/sponsors.md") },
  { name: "Apify", cat: "Sponsors", layer: "neutral", use: "not used", what: "Web scraping platform with ready-made extractors.", role: "None yet.", cost: "$50 credits", src: repo("docs/sponsors.md") },
  { name: "Tavily", cat: "Sponsors", layer: "neutral", use: "not used", what: "Search API for agents (no event description).", role: "None yet.", cost: "8K credits", src: repo("docs/sponsors.md") },
  { name: "Render", cat: "Sponsors", layer: "neutral", use: "not used", what: "Listed on the event website only; replaced by Tavily on the on-site slide.", role: "None.", cost: "none listed", src: repo("docs/hackathons.md") },
  // Compute and models
  { name: "RunPod", cat: "Compute and models", layer: "compute", use: "core", what: "GPU cloud: pods billed hourly, or serverless endpoints.", role: "The foundation for anything heavy: model serving, Isaac Sim, training. Not a sponsor.", cost: "A6000 $0.33/h · 4090 $0.34/h · L40S $0.79/h (community)", src: ext("RunPod", "https://www.runpod.io") },
  { name: "vLLM", cat: "Compute and models", layer: "compute", use: "planned", what: "Open-source model server with an OpenAI-compatible API and tool calling.", role: "Serves the open model on a RunPod pod for Hermes and the benchmark.", cost: "free software", src: ext("vLLM tool calling", "https://docs.vllm.ai/en/latest/features/tool_calling.html") },
  { name: "Qwen3-VL-8B-Instruct", cat: "Compute and models", layer: "compute", use: "candidate", what: "Open vision-language model that reads images.", role: "The open model in the benchmark; hermes tool parser is inferred, so smoke-test it.", cost: "fits a 24 GB card at short context", src: ext("model card", "https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct") },
  { name: "SmolVLA", cat: "Compute and models", layer: "sim", use: "planned", what: "Small vision-language-action policy from Hugging Face, language-conditioned.", role: "The VLA that drives the arm; one policy can handle both bin directions.", cost: "train 10–16 GB · ~2 GB to run", src: ext("LeRobot SmolVLA", "https://huggingface.co/docs/lerobot/main/en/smolvla") },
  { name: "ACT", cat: "Compute and models", layer: "sim", use: "planned", what: "Action-chunking transformer: a small imitation policy.", role: "Cheap baseline that runs on a CPU at the bench.", cost: "train 2–6 GB", src: ext("LeRobot policies", "https://huggingface.co/docs/lerobot") },
  { name: "GR00T N1.5", cat: "Compute and models", layer: "sim", use: "reference", what: "NVIDIA's robot foundation model, with an SO-101 fine-tuning tutorial.", role: "Reference only; fine-tuning needs about 25 GB.", cost: "", src: ext("HF blog", "https://huggingface.co/blog/nvidia/gr00t-n1-5-so101-tuning") },
  { name: "Robometer-4B", cat: "Compute and models", layer: "sim", use: "candidate", what: "Checks task success from video, in LeRobot (Kent's find).", role: "Success check for the reset loop and benchmark; needs a GPU.", cost: "GPU", src: repo("CLAUDE.md", "CLAUDE.md · Romulus") },
  // Robot and sim
  { name: "LeRobot 0.7.0", cat: "Robot and sim", layer: "edge", use: "core", what: "Hugging Face's robotics library: SO-101 driver, CLIs, datasets, policies, remote inference.", role: "Drives the arm and trains its policies. Python ≥ 3.12; breaking changes in 0.7.", cost: "free software", src: ext("LeRobot SO-101", "https://huggingface.co/docs/lerobot/so101") },
  { name: "Isaac Sim + Isaac Lab", cat: "Robot and sim", layer: "sim", use: "planned", what: "NVIDIA's robot simulator (5.1) and learning framework (2.3).", role: "Canary gate, fault drills and synthetic demos, headless on RunPod.", cost: "needs RT cores (no A100/H100)", src: ext("requirements", "https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html") },
  { name: "LeIsaac", cat: "Robot and sim", layer: "sim", use: "planned", what: "SO-101 tasks for Isaac Lab, with teleop, a scripted demo generator and a LeRobot recorder.", role: "Starting point for a cube-between-bins task.", cost: "free software", src: ext("LeIsaac", "https://github.com/LightwheelAI/leisaac") },
  { name: "MuJoCo + Menagerie", cat: "Robot and sim", layer: "sim", use: "candidate", what: "Fast physics engine; Menagerie ships an SO-101 model.", role: "Fast twin that runs on a Mac; fallback if Isaac stalls. Kent pins 4.0.0.", cost: "free software", src: ext("Menagerie", "https://github.com/google-deepmind/mujoco_menagerie") },
  // Edge hardware
  { name: "SO-101 arm", cat: "Edge hardware", layer: "edge", use: "core", what: "Open-source 3D-printed arm: red follower plus white leader, 6 Feetech STS3215 servos.", role: "The robot the company operates.", cost: "on site", src: ext("SO-ARM100 repo", "https://github.com/TheRobotStudio/SO-ARM100") },
  { name: "Raspberry Pi 5 · 8 GB", cat: "Edge hardware", layer: "edge", use: "core", what: "The edge computer: robot app, healer agent, local model.", role: "Main device. LeRobot 0.7 needs Python 3.12; Pi OS ships 3.11 (fault S9).", cost: "on site", src: repo("docs/project-overview.md", "project overview §5") },
  { name: "STM32 Nucleo-F042K6", cat: "Edge hardware", layer: "edge", use: "core", what: "Cortex-M0 board, 32 KB flash, 6 KB RAM; bare-metal C, no HAL.", role: "Olir's sanity firmware on the firmware branch: LED, banner, heartbeat, make check of the build ID. The base for the STM32 faults (inferred).", cost: "on site", src: { label: "firmware branch", href: "https://github.com/jamesEmerson112/Naive-Nous-YC-Hackathon/tree/firmware/firmware/stm32f042k6-hello" } },
  { name: "HC-SR04 ultrasonic", cat: "Edge hardware", layer: "edge", use: "core", what: "Distance sensor, 2–400 cm, read with gpiozero + lgpio.", role: "Its reading drives the arm; the demo's planted bug lives here.", cost: "on site", src: repo("docs/project-overview.md", "project overview §5") },
  { name: "Jetson AGX Xavier", cat: "Edge hardware", layer: "compute", use: "candidate", what: "64 GB edge GPU board.", role: "Later: a bigger local model over the LAN. Not set up.", cost: "on site", src: repo("docs/project-overview.md", "project overview §5") },
  { name: "Ollama", cat: "Compute and models", layer: "edge", use: "core", what: "Runs small models locally (llama.cpp underneath).", role: "The device healer's model: qwen3.5:4b with thinking on, picked by Ishaan's benchmark (on the Pi; later the Jetson).", cost: "free software", src: repo("docs/healer-benchmark-progress.md", "healer benchmark") },
  { name: "Romulus v0 stack", cat: "Compute and models", layer: "heal", use: "core", what: "FastAPI + Postgres 17 + Jinja/htmx in Docker Compose on Kent's laptop.", role: "Version registry, device API, batch job, gate, incident timeline, coding-agent sandbox. M0–M8a merged.", cost: "free software", src: repo("docs/cloud-platform-planning/cloud-platform-build-plan.md", "build plan") },
  { name: "ESP32 / Arduino", cat: "Edge hardware", layer: "edge", use: "reference", what: "Microcontroller boards; Waveshare makes an ESP32 bus-servo driver.", role: "Could bridge the bus or watch servo health; can't run LeRobot.", cost: "", src: ext("Waveshare ESP32 driver", "https://github.com/waveshare/Servo-Driver-with-ESP32") },
  { name: "Robotic-arm design draft", cat: "Hosts", layer: "neutral", use: "reference", what: "The team's earlier diagram page: layers, logs, safety, HIL-SERL notes.", role: "Ported into this map; the file itself stays with its author session.", cost: "", src: local("docs/robotic-arm.html (not committed)") },
];
