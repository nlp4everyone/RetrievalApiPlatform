FROM python:3.11-slim AS builder

WORKDIR /workspace

COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /usr/local/bin/uv

# System deps needed to build packages without prebuilt wheels
RUN apt-get update && apt-get install -y build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject.toml, uv.lock, and README.md for package build
COPY pyproject.toml uv.lock README.md ./
# Install dependencies using uv
RUN uv sync --frozen

FROM python:3.11-slim AS runtime

WORKDIR /workspace

COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /usr/local/bin/uv
COPY --from=builder /workspace/.venv /workspace/.venv

# Copy the rest of the application
COPY . /workspace

# Run as a non-root user
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /workspace
USER appuser

EXPOSE 8005
