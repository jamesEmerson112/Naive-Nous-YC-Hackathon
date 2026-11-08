"""Healer agents: what runs on the device after the supervisor records an incident.

The work area an agent sees (the benchmark builds the same layout for every task):
  device/      the robot app: the agent's working directory, a git repo whose HEAD is the deployed version
  harness/     harness.json, prompt.md, skills/, tools/ (the bundle the cloud ships and improves)
  supervisor/  monitor.py, which the health tool uses
  sim/         fake hardware; the health tool replays the recorded scenario on it
  incident.json, scenario.json, outbox/ (the report for the cloud goes here)

render_task() fills the harness prompt's placeholders: {{incident}}, {{spec}} (SPEC.md), {{guides}} (the
skills whose pattern in harness.json "guides" matches the incident, inlined), {{files}} (the deployed
files named in "show_files") and {{skills}} (a list of the other skills).

run_mini() runs mini-swe-agent with a local model served by Ollama. harness.json "finish" says how a run
ends: "report" (the default) when the harness's report tool accepts a report and prints DONE_MARKER on a
line of its own; "echo" with mini's usual `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` (harness h1).
harness.json "instance" can name the bundle's own first-message template; MINI_INSTANCE is the default.
"""
import json
import os
import re
import sys
from pathlib import Path

DONE_MARKER = "HEALER_DONE"


def render_incident(incident):
    lines = [f"tier {incident['tier']} {incident['kind']}: {incident['detail']}",
             f"recorded scenario: {incident['scenario']}"]
    if incident.get("log_tail"):
        lines.append("last log lines:")
        lines += ["    " + line for line in incident["log_tail"][-14:]]
    if incident.get("stack_dump"):
        lines.append("stack when stopped (most recent call first):")
        lines += ["    " + line for line in incident["stack_dump"].strip().splitlines()[:12]]
    return "\n".join(lines)


def pick_guides(skills, patterns, incident):
    """The skill files whose pattern (harness.json "guides") matches the incident, in harness.json order."""
    text = "\n".join([f"kind: {incident['kind']}", incident["detail"], *incident.get("log_tail", []),
                      incident.get("stack_dump", "")])
    return [skills / name for name, pattern in patterns.items()
            if (skills / name).is_file() and re.search(pattern, text)]


def skill_head(path):
    return path.read_text().splitlines()[0].lstrip("#").strip()


def show_files(device, names):
    blocks = []
    for name in names:
        path = device / name
        if path.is_file():
            lang = {".py": "python", ".toml": "toml"}.get(path.suffix, "")
            blocks.append(f"### {name}\n```{lang}\n{path.read_text().rstrip()}\n```")
    return "## The code, as deployed\n" + "\n\n".join(blocks) + "\n" if blocks else ""


def render_task(work):
    work = Path(work)
    harness = work / "harness"
    meta = json.loads((harness / "harness.json").read_text())
    incident = json.loads((work / "incident.json").read_text())
    skills = harness / meta.get("knowledge_dir", "skills/")
    guides = pick_guides(skills, meta.get("guides", {}), incident)
    others = [f for f in sorted(skills.glob("*.md")) if f not in guides] if skills.is_dir() else []
    header = ("- Other guides (read one with cat if it fits):" if guides else
              "- Guides in ../harness/skills/. Read the one that matches the incident with cat:")

    spec = (work / "device" / "SPEC.md").read_text().splitlines()
    values = {
        "incident": render_incident(incident),
        "spec": "\n".join(spec[1:] if spec and spec[0].startswith("# ") else spec).strip(),
        "guides": "".join(f"\n## Guide: {skill_head(g)}\n" + "\n".join(g.read_text().splitlines()[1:]).strip() + "\n"
                          for g in guides),
        "files": show_files(work / "device", meta.get("show_files", [])),
        "skills": "\n".join([header] + [f"  - ../harness/skills/{f.name}: "
                                        f"{skill_head(f).partition(':')[2].strip() or skill_head(f)}"
                                        for f in others]) if others else "",
    }
    prompt = (harness / meta.get("prompt", "prompt.md")).read_text()
    for key, value in values.items():
        prompt = prompt.replace("{{" + key + "}}", value)
    return re.sub(r"\n{3,}", "\n\n", prompt)


# mini-swe-agent's first user message. {{task}} is render_task()'s text.
MINI_INSTANCE = """{{task}}

## How to work
Each reply: one short thought, then exactly one bash tool call. Every command runs in a new shell in the robot app's directory.
"""

# Replaces mini's default, which tells the model to finish with an echo; with finish "report" only report.py ends a run.
FORMAT_ERROR = """{% if finish_reason is defined and (finish_reason == "length" or (finish_reason == "tool_calls" and not has_tool_calls)) -%}
Your reply was cut off before the tool call. Reply again, shorter, with exactly one bash tool call.
{%- else -%}
Tool call error: {{error}}
Every reply must call the bash tool with one command, for example {"command": "cat -n control.py"}. To finish, run the report tool.
{%- endif %}"""


def run_mini(work, model, step_limit=30, time_limit=600, think=None):
    """Run mini-swe-agent in work/device. `model` is an Ollama tag (granite4.1:3b) or a LiteLLM model name.
    think=False turns a thinking model's thinking off (Ollama think=false); None leaves the model's default."""
    import yaml
    from minisweagent import package_dir
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment
    from minisweagent.exceptions import Submitted
    from minisweagent.models.litellm_model import LitellmModel

    class HealerEnvironment(LocalEnvironment):
        """Ends the run when report.py accepts a report, instead of on mini's usual echo."""

        def execute(self, action, cwd="", *, timeout=None):
            self.command = action.get("command", "")
            return super().execute(action, cwd, timeout=timeout)

        def _check_finished(self, output):
            lines = [line.strip() for line in output.get("output", "").splitlines()]
            if "report.py" in self.command and DONE_MARKER in lines:
                raise Submitted({"role": "exit", "content": "", "extra": {"exit_status": "Submitted", "submission": ""}})

    work = Path(work)
    meta = json.loads((work / "harness" / "harness.json").read_text())
    report_finish = meta.get("finish", "report") == "report"
    instance = (work / "harness" / meta["instance"]).read_text() if meta.get("instance") else MINI_INSTANCE
    cfg = yaml.safe_load((Path(package_dir) / "config" / "mini.yaml").read_text())
    model_cfg = dict(cfg["model"])
    if report_finish:
        model_cfg["format_error_template"] = FORMAT_ERROR
    if think is not None:  # LiteLLM sends Ollama think=true for "high" and think=false for "none"
        model_cfg["model_kwargs"] = {**model_cfg.get("model_kwargs", {}), "reasoning_effort": "high" if think else "none"}
    lm = LitellmModel(model_name=model if "/" in model else f"ollama_chat/{model}", cost_tracking="ignore_errors",
                      **model_cfg)
    env_vars = dict(cfg["environment"]["env"], PATH=f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    env = (HealerEnvironment if report_finish else LocalEnvironment)(cwd=str(work / "device"), env=env_vars, timeout=60)
    agent = DefaultAgent(lm, env, system_template=cfg["agent"]["system_template"], instance_template=instance,
                         step_limit=step_limit, cost_limit=0, wall_time_limit_seconds=time_limit,
                         output_path=work / "trajectory.json")
    result = agent.run(render_task(work))
    return {"exit_status": result.get("exit_status", ""), "steps": agent.n_calls}
