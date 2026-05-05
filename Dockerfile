FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1

# Use nala for parallel package downloads to speed up build
RUN --mount=type=cache,target=/var/lib/apt/lists \
    --mount=type=cache,target=/var/cache/apt \
    apt-get update && apt-get install -y nala --no-install-recommends

RUN --mount=type=cache,target=/var/lib/apt/lists \
    --mount=type=cache,target=/var/cache/apt \
    nala install -y \
    chromium \
    --no-install-recommends

COPY pyproject.toml uv.lock README.md ./

# Install dependencies without the project itself to leverage layer caching
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY entrypoint.sh ./entrypoint.sh

RUN chmod +x entrypoint.sh && uv sync --frozen --no-dev

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uv", "run", "hovorunv2"]

