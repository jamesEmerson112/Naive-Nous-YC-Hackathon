# The `coder` sandbox (architecture §2; D21, D22; M8). The worker starts one container per
# code patch with a scratch copy of the device code mounted at /work and nothing else.
# Build context is this folder: docker build -t romulus-coder cloud/coder
#
# Two Pythons on purpose:
# - /usr/local/bin/python (on PATH) is the *device's* interpreter: pytest only, so the
#   device tests and the agent's own commands see a clean environment.
# - /opt/agent is mini-swe-agent and its dependencies (litellm, ...), off PATH.
# Not in the image yet: the device's real dependencies (blocker B1) and the STM32
# toolchain, arm-none-eabi-gcc (blocker B8).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # The agent can write to $HOME=/tmp; a usercustomize.py or .pth there must not load.
    PYTHONNOUSERSITE=1 \
    MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT=2 \
    # Runs as the host user (docker run --user), who has no home in the image.
    HOME=/tmp \
    MSWEA_SILENT_STARTUP=1 \
    MSWEA_COST_TRACKING=ignore_errors \
    # Use litellm's bundled price map; don't fetch it from GitHub at startup.
    LITELLM_LOCAL_MODEL_COST_MAP=True

RUN uv pip install --system --no-cache pytest==8.4.2 \
    && uv venv /opt/agent \
    && uv pip install --python /opt/agent/bin/python --no-cache mini-swe-agent==2.4.6

COPY run_agent.py /opt/romulus/run_agent.py

WORKDIR /work
ENTRYPOINT ["/opt/agent/bin/python", "/opt/romulus/run_agent.py"]
CMD ["agent"]
