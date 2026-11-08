# The coding-agent sandbox (M8, D21; architecture §2 `coder`). LocalAgent starts one
# container per code patch (`docker run --rm -i romulus-coder:0.1 draft|test`), sends the
# request on stdin and reads the JSON result from stdout (cloud/coder/runner.py).
# Build context is this folder: docker build -t romulus-coder:0.1 cloud/coder
#
# Network: drafting needs the LLM endpoint, so LocalAgent runs it on CODER_NETWORK (default
# `bridge`: NOT limited to the LLM host). Replay (`test`) always runs with `--network none`.
# Limiting drafting to the LLM host needs an allow-list egress proxy on an internal network;
# not built (see the PR "what is and isn't enforced").
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# TODO(B8): the STM32 toolchain. Once the C firmware lives under device/ (contracts I0),
# install `gcc-arm-none-eabi` and `make` here and add the firmware's build (e.g.
# `make -C firmware`) to CODER_TEST_CMD, so a patch to C code is compiled before the gate.

# The agent the device team's healer uses (mini-swe-agent, D22), pinned with its LLM client.
RUN pip install --no-cache-dir \
    mini-swe-agent==2.4.6 \
    litellm==1.108.3 \
    pytest==9.0.2

# No network calls at import (litellm's price map), no banners on stdout, no stray .pyc.
ENV LITELLM_LOCAL_MODEL_COST_MAP=True \
    MSWEA_SILENT_STARTUP=1 \
    MSWEA_GLOBAL_CONFIG_DIR=/tmp/mswea \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 1000 coder
COPY runner.py /opt/romulus/runner.py
USER coder
WORKDIR /home/coder
ENTRYPOINT ["python", "/opt/romulus/runner.py"]
