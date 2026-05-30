# syntax=docker/dockerfile:1
FROM python:3.12-slim

# uv for fast, reproducible dependency installation.
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (better layer caching), then the package itself.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN uv pip install --system --no-cache .

# Run as an unprivileged user; the backup dir is mounted read-only anyway.
RUN useradd --system --uid 10001 appuser
USER appuser

ENTRYPOINT ["python", "-m", "backup_r2"]
