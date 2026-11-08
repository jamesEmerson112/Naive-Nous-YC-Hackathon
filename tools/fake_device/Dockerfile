# Fake arms against the Romulus API. Depends only on romulus-contracts + HTTP (architecture §3).
# Build context is the repo root: docker build -f tools/fake_device/Dockerfile .
# The `fake_device` entry module arrives in a later PR (M1).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
COPY packages/contracts/pyproject.toml packages/contracts/
COPY cloud/pyproject.toml cloud/
COPY tools/fake_device/pyproject.toml tools/fake_device/
RUN uv sync --frozen --no-dev --package fake-device --no-install-workspace

COPY packages/contracts/src packages/contracts/src
COPY tools/fake_device/src tools/fake_device/src
RUN uv sync --frozen --no-dev --package fake-device

ENTRYPOINT ["python", "-m", "fake_device"]
