FROM python:3.11-slim AS builder

WORKDIR /workspace

COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /usr/local/bin/uv

# System deps needed to build packages without prebuilt wheels
RUN apt-get update && apt-get install -y build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject.toml and uv.lock first so README edits don't invalidate the sync cache
COPY pyproject.toml uv.lock ./
# Install dependencies using uv (placeholder README satisfies pyproject.toml's readme field)
RUN touch README.md && uv sync --frozen
COPY README.md ./

FROM python:3.11-slim AS runtime

WORKDIR /workspace

# Run as a non-root user
RUN useradd --create-home --shell /bin/bash appuser

COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /usr/local/bin/uv
COPY --from=builder --chown=appuser:appuser /workspace/.venv /workspace/.venv

# Copy the rest of the application
COPY --chown=appuser:appuser . /workspace

USER appuser

EXPOSE 8005
