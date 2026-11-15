# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.15.2-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490

# -----------------------------------------------------------------------------
# Base runtime image: creates non-root user and shared runtime directory
# -----------------------------------------------------------------------------
FROM ${UV_IMAGE} AS uv-binary

FROM ${PYTHON_IMAGE} AS base

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Run as non-root user
RUN groupadd -g 10001 appuser && useradd -u 10001 -g appuser -d /app -s /sbin/nologin appuser

COPY --chown=appuser:appuser LICENSE THIRD_PARTY_NOTICES.md ./

# -----------------------------------------------------------------------------
# Builder base: toolchain, uv, and cached source/lockfiles
# -----------------------------------------------------------------------------
FROM base AS builder-base

COPY --from=uv-binary /uv /bin/uv

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

FROM builder-base AS builder-worker
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
COPY --chown=appuser:appuser README.md CHANGES.md* ./

USER appuser
CMD ["/app/.venv/bin/hovorunv2"]

# -----------------------------------------------------------------------------
# Runtime target: worker (media, speech, translation, operations + embedded Beat)
# -----------------------------------------------------------------------------
FROM base AS worker

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get -o Acquire::Queue-Mode=access -o Acquire::Retries=3 install -y --no-install-recommends \
        ffmpeg ca-certificates util-linux

ARG APP_VERSION=""
ENV APP_VERSION=${APP_VERSION} \
    WORKER_ROLE=all

RUN mkdir -p /app/data/models/speech && chown -R appuser:appuser /app/data

COPY --from=builder-worker --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser README.md CHANGES.md* ./

USER appuser
CMD ["/app/.venv/bin/celery", "-A", "hovorunv2.bootstrap.worker_app:celery_app", "worker", "--loglevel=INFO", "--beat", "-s", "/tmp/celerybeat-schedule", "-Q", "work:default:media,work:default:speech,work:default:operations"]

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
        chromium fonts-liberation ca-certificates ffmpeg util-linux

RUN mkdir -p /app/data/models && chown -R appuser:appuser /app/data

COPY --from=builder-worker-browser --chown=appuser:appuser /app/.venv /app/.venv

USER appuser
CMD ["/app/.venv/bin/celery", "-A", "hovorunv2.bootstrap.worker_app:celery_app", "worker", "--loglevel=INFO", "-Q", "work:default:browser", "--concurrency=1"]

# -----------------------------------------------------------------------------
# Default target
# -----------------------------------------------------------------------------
FROM bot AS default
