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

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get -o Acquire::Queue-Mode=access -o Acquire::Retries=3 install -y --no-install-recommends \
        ffmpeg ca-certificates

ARG APP_VERSION=""
ENV APP_VERSION=${APP_VERSION} \
    WORKER_ROLE=media

RUN mkdir -p /app/data/models && chown -R appuser:appuser /app/data

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
        chromium fonts-liberation ca-certificates ffmpeg

RUN mkdir -p /app/data/models && chown -R appuser:appuser /app/data

COPY --from=builder-worker-browser --chown=appuser:appuser /app/.venv /app/.venv

USER appuser
CMD ["/app/.venv/bin/celery", "-A", "hovorunv2.bootstrap.worker_app:celery_app", "worker", "--loglevel=INFO", "-Q", "browser", "--concurrency=1"]

# Build the qualified CPU runtime from an immutable, digest-verified source archive.
FROM base AS builder-whisper
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates cmake ninja-build g++ && rm -rf /var/lib/apt/lists/*
WORKDIR /build
ARG WHISPER_CPP_VERSION=1.9.5
ADD --checksum=sha256:ff1a9053feb509ff9d7729703355541ae9690073a6b1c40eb692c962e0dc1720 \
    https://github.com/ggml-org/whisper.cpp/archive/refs/tags/v${WHISPER_CPP_VERSION}.tar.gz \
    whisper.tar.gz
RUN mkdir source && tar -xzf whisper.tar.gz --strip-components=1 -C source
RUN cmake -S source -B output -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DGGML_NATIVE=OFF -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON \
    -DGGML_CUDA=OFF -DGGML_VULKAN=OFF -DGGML_BLAS=OFF \
    -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=OFF -DWHISPER_CURL=OFF \
    -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON '-DCMAKE_INSTALL_RPATH=$ORIGIN' \
    -DBUILD_SHARED_LIBS=ON && cmake --build output --target whisper -j2
COPY src/hovorunv2/infrastructure/speech/whisper_helper.cpp ./
RUN g++ -std=c++17 -O2 -Wall -Wextra -Werror -Isource/include -Isource/ggml/include \
    whisper_helper.cpp -Loutput/bin '-Wl,-rpath,$ORIGIN/../lib/hovorun-speech' \
    -lwhisper -o hovorun-whisper-helper

FROM base AS worker-speech
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg ca-certificates util-linux libgomp1 libstdc++6 && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /app/data/models/speech && chown -R appuser:appuser /app/data
COPY --from=builder-worker-ops --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=builder-whisper /build/hovorun-whisper-helper /usr/local/bin/hovorun-whisper-helper
COPY --from=builder-whisper /build/output/bin/lib*.so* /usr/local/lib/hovorun-speech/
COPY --from=builder-whisper /build/source/LICENSE /usr/local/share/licenses/whisper.cpp/LICENSE
ENV WORKER_ROLE=speech
USER appuser
CMD ["/app/.venv/bin/celery", "-A", "hovorunv2.bootstrap.worker_app:celery_app", "worker", "--loglevel=INFO", "--concurrency=1", "--prefetch-multiplier=1", "-Q", "work:{work}:speech"]

# -----------------------------------------------------------------------------
# Default target
# -----------------------------------------------------------------------------
FROM worker-browser AS worker-compression

ENV WORKER_ROLE=compression
CMD ["/app/.venv/bin/celery", "-A", "hovorunv2.bootstrap.worker_app:celery_app", "worker", "--loglevel=INFO", "-Q", "work:{work}:compression", "--concurrency=1"]

FROM bot AS default
