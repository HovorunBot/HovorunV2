FROM python:3.14-slim

WORKDIR /app

# 1. Install system dependencies first (Heavy & Stable)
# This layer rarely changes unless we add new system packages.
# We combine apt and chromium install to minimize layers.
RUN --mount=type=cache,target=/var/lib/apt/lists \
    --mount=type=cache,target=/var/cache/apt \
    apt-get update && apt-get install -y --no-install-recommends nala \
    && nala install -y --no-install-recommends --raw-dpkg \
    chromium \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 2. Install uv (Small & Frequent)
# Moving this after heavy deps ensures that a uv update doesn't trigger chromium re-download.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 3. Environment configuration
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# 4. Install Python dependencies (Medium Stability)
# Copy ONLY the lockfile and pyproject.toml.
# README.md is removed from here to avoid cache invalidation on text changes.
COPY pyproject.toml uv.lock ./

# Use uv cache mount to speed up dependency resolution/downloads
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# 5. Copy application code (Frequent Changes)
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY entrypoint.sh ./entrypoint.sh
COPY README.md ./README.md

# Final sync to install the project itself
RUN chmod +x entrypoint.sh && uv sync --frozen --no-dev

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uv", "run", "hovorunv2"]

