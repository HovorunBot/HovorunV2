# Compile runtime dependencies in a disposable stage; ship no compiler or package cache.
FROM python:3.15-slim AS builder
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
ENV UV_NO_CACHE=1 UV_LINK_MODE=copy
RUN apt-get update \
    && apt-get -o Acquire::Queue-Mode=access -o Acquire::Retries=3 install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY README.md CHANGES.md* CHANGELOG.md* ./
RUN uv sync --frozen --no-dev --no-editable

# Lean image for the bot, media/operations workers, Beat, migrations and administration.
FROM python:3.15-slim AS lean
WORKDIR /app
RUN rm -rf /var/lib/apt/lists/* /var/cache/apt/*
COPY --from=builder /bin/uv /bin/uv
COPY --from=builder /app/.venv /app/.venv
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY README.md CHANGES.md* CHANGELOG.md* ./
ARG APP_VERSION=""
ENV APP_VERSION=${APP_VERSION} \
    UV_NO_CACHE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
CMD ["uv", "run", "--no-sync", "hovorunv2"]

# Build this target only when BROWSER_ENABLED=True selects the browser worker.
FROM lean AS browser
RUN apt-get update \
    && apt-get -o Acquire::Queue-Mode=access -o Acquire::Retries=3 install -y --no-install-recommends \
    chromium fonts-liberation \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*
RUN uv sync --frozen --no-dev --no-editable --extra browser
ENV BROWSER_PATH=/usr/bin/chromium
CMD ["uv", "run", "--no-sync", "celery", "-A", "hovorunv2.bootstrap.worker_app:app", "worker", "--pool=prefork", "--concurrency=1", "--queues=work:{work}:browser"]

# An untargeted build remains lean.
FROM lean AS default
