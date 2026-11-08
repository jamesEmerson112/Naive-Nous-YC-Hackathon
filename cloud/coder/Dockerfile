# The `coder` sandbox (architecture §2; D21, D22; M8). The worker starts one container per
# code patch with a scratch copy of the device code mounted at /work and nothing else.
# Build it with `make coder-image`, or by hand from the repo root:
#   docker build --build-context fwtoolkit=firmware/toolkit -t romulus-coder cloud/coder
#
# Two Pythons on purpose:
# - /usr/local/bin/python (on PATH) is the *device's* interpreter: pytest only, so the
#   device tests and the agent's own commands see a clean environment.
# - /opt/agent is mini-swe-agent and its dependencies (litellm, ...), off PATH.
# The STM32 toolchain (blocker B8) is in /opt/fw-toolkit/opt: arm-none-eabi-gcc and the
# CMSIS headers, installed by the firmware's own toolkit/setup.sh at the versions and
# checksums in its versions.env. No OpenOCD: flashing and SWD stay on the bench.
# firmware_check.py builds a copy of firmware/ with it. Not in the image yet: the device's
# real dependencies (blocker B1).

# Without `--build-context fwtoolkit=firmware/toolkit`, this stage runs and stops the build
# with a hint. With it, BuildKit uses the firmware/toolkit folder in place of this stage.
FROM busybox:1.36 AS fwtoolkit
RUN echo "coder image: build with --build-context fwtoolkit=firmware/toolkit (make coder-image)" >&2 \
    && false

# The STM32 toolchain, in its own stage so Python-only changes never download it again.
# setup.sh refuses any download whose SHA-256 differs from versions.env. Only gcc and the
# CMSIS headers are kept.
FROM python:3.12-slim AS stm32
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=fwtoolkit setup.sh versions.env /opt/fw-toolkit/
RUN /opt/fw-toolkit/setup.sh \
    && rm -rf /opt/fw-toolkit/downloads /opt/fw-toolkit/opt/openocd /opt/fw-toolkit/opt/xpack-openocd-*

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends git make \
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

# versions.env comes along, so firmware_check.py can tell when firmware/ pins a newer toolchain.
COPY --from=stm32 /opt/fw-toolkit /opt/fw-toolkit

COPY run_agent.py firmware_check.py /opt/romulus/

WORKDIR /work
ENTRYPOINT ["/opt/agent/bin/python", "/opt/romulus/run_agent.py"]
CMD ["agent"]
