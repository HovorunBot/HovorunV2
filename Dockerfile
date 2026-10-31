# Shared build base. Runtime roles select one of the final lean/browser/import targets.
FROM python:3.15-slim AS base

WORKDIR /app

# Keep downloaded .deb packages in BuildKit cache mounts across builds.
RUN rm -f /etc/apt/apt.conf.d/docker-clean \
    && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get -o Acquire::Queue-Mode=access -o Acquire::Retries=3 install -y --no-install-recommends \
    gcc \
    libc6-dev

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Install only locked runtime dependencies first so source edits reuse this layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY README.md CHANGES.md* CHANGELOG.md* ./

ARG APP_VERSION=""
ENV APP_VERSION=${APP_VERSION}

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Lean target for the bot, media/operations workers, Beat, migration and administration.
# No Chromium, DrissionPage or legacy SQLite importer dependency is installed here.
FROM base AS lean
RUN apt-get purge -y --auto-remove gcc libc6-dev
CMD ["uv", "run", "--no-sync", "hovorunv2"]

# Browser-specific target. Chromium is installed at build time and started lazily by the worker child.
FROM lean AS browser
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get -o Acquire::Queue-Mode=access -o Acquire::Retries=3 install -y --no-install-recommends \
    chromium \
    fonts-liberation
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra browser
ENV BROWSER_PATH=/usr/bin/chromium
CMD ["uv", "run", "--no-sync", "celery", "-A", "hovorunv2.bootstrap.worker_app:app", "worker", "--pool=prefork", "--concurrency=1", "--queues=work:{work}:browser"]

# One-shot SQLite importer target; keeps the historical aiosqlite tool out of live application images.
FROM lean AS legacy-import
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra legacy-import
CMD ["uv", "run", "--no-sync", "hovorun-import-sqlite"]

# Keep an untargeted build identical to the lean application image.
FROM lean AS default
