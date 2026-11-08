# One image for api and worker; they differ only in their command (architecture §2).
# Build context is the repo root (the uv workspace): docker build -f cloud/Dockerfile .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so code edits don't reinstall them.
COPY pyproject.toml uv.lock .python-version ./
COPY packages/contracts/pyproject.toml packages/contracts/
COPY cloud/pyproject.toml cloud/
COPY tools/fake_device/pyproject.toml tools/fake_device/
RUN uv sync --frozen --no-dev --package romulus-cloud --no-install-workspace

COPY packages/contracts/src packages/contracts/src
COPY cloud/src cloud/src
RUN uv sync --frozen --no-dev --package romulus-cloud

EXPOSE 8000
CMD ["uvicorn", "--factory", "romulus_cloud.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
