FROM python:3.11-slim

# Set workdir
WORKDIR /workspace

# Install uv
RUN pip install uv

# System deps
RUN apt-get update && apt-get install -y build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject.toml, uv.lock, and README.md for package build
COPY pyproject.toml uv.lock README.md ./
# Install dependencies using uv
RUN uv sync --frozen

# Copy the rest of the application
COPY . /workspace

# Run as a non-root user
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /workspace
USER appuser

EXPOSE 8005
