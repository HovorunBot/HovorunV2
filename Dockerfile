# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.15.2-slim-trixie@sha256:8c13a3f37a6a5b71498228e902c4b9f51c1f7900d49fabf71f9a650c6120e0d0
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0

# -----------------------------------------------------------------------------
# Base runtime image: creates non-root user and shared runtime directory
# -----------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS base

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Run as non-root user
RUN groupadd -g 10001 appuser && useradd -u 10001 -g appuser -d /app -s /sbin/nologin appuser

# -----------------------------------------------------------------------------
# Builder base: toolchain, uv, and cached source/lockfiles
# -----------------------------------------------------------------------------
FROM base AS builder-base

COPY --from=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 /uv /bin/uv

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get -o Acquire::Queue-Mode=access -o Acquire::Retries=3 install -y --no-install-recommends \
        gcc libc6-dev

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# -----------------------------------------------------------------------------
# Role builders: sync frozen venv for each role extra with BuildKit cache
# -----------------------------------------------------------------------------
FROM builder-base AS builder-migrate
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra migrate

FROM builder-base AS builder-bot
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra bot

FROM builder-base AS builder-worker-ops
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra worker-ops

FROM builder-base AS builder-worker-media
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra worker-media

FROM builder-base AS builder-worker-browser
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra worker-browser

# -----------------------------------------------------------------------------
# Runtime target: migrate
# -----------------------------------------------------------------------------
FROM base AS migrate

COPY --from=builder-migrate --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser alembic.ini ./alembic.ini
COPY --chown=appuser:appuser alembic ./alembic

USER appuser
CMD ["/app/.venv/bin/alembic", "upgrade", "head"]

# -----------------------------------------------------------------------------
# Runtime target: bot
# -----------------------------------------------------------------------------
FROM base AS bot

ARG APP_VERSION=""
ENV APP_VERSION=${APP_VERSION}

COPY --from=builder-bot --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser README.md CHANGES.md* CHANGELOG.md* ./

USER appuser
CMD ["/app/.venv/bin/hovorunv2"]

# -----------------------------------------------------------------------------
# Runtime target: worker-operations (operations + beat + cleanup)
# -----------------------------------------------------------------------------
FROM base AS worker-operations

ARG APP_VERSION=""
ENV APP_VERSION=${APP_VERSION} \
    WORKER_ROLE=operations

COPY --from=builder-worker-ops --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser README.md CHANGES.md* CHANGELOG.md* ./

USER appuser
CMD ["/app/.venv/bin/celery", "-A", "hovorunv2.bootstrap.worker_app:celery_app", "worker", "--loglevel=INFO", "-Q", "operations"]

# -----------------------------------------------------------------------------
# Runtime target: worker-media (yt-dlp, instaloader, bs4, native download)
# -----------------------------------------------------------------------------
FROM base AS worker-media

ARG APP_VERSION=""
ENV APP_VERSION=${APP_VERSION} \
    WORKER_ROLE=media

COPY --from=builder-worker-media --chown=appuser:appuser /app/.venv /app/.venv

USER appuser
CMD ["/app/.venv/bin/celery", "-A", "hovorunv2.bootstrap.worker_app:celery_app", "worker", "--loglevel=INFO", "-Q", "media,translation"]

# -----------------------------------------------------------------------------
# Runtime target: worker-browser (Chromium + pydoll-python fallback)
# -----------------------------------------------------------------------------
FROM base AS worker-browser

ARG APP_VERSION=""
ENV APP_VERSION=${APP_VERSION} \
    WORKER_ROLE=browser \
    CHROME_BIN=/usr/bin/chromium \
    BROWSER_PATH=/usr/bin/chromium

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get -o Acquire::Queue-Mode=access -o Acquire::Retries=3 install -y --no-install-recommends \
        chromium fonts-liberation ca-certificates

COPY --from=builder-worker-browser --chown=appuser:appuser /app/.venv /app/.venv

USER appuser
CMD ["/app/.venv/bin/celery", "-A", "hovorunv2.bootstrap.worker_app:celery_app", "worker", "--loglevel=INFO", "-Q", "browser", "--concurrency=1"]

# -----------------------------------------------------------------------------
# Default target
# -----------------------------------------------------------------------------
FROM bot AS default
