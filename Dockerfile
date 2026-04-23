FROM python:3.14-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy project files
COPY pyproject.toml uv.lock README.md ./

# Install dependencies (use frozen to ensure lockfile is respected)
RUN uv sync --frozen --no-dev --no-install-project

# Copy source code and migrations
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

# Install the project
RUN uv sync --frozen --no-dev

# Run migrations and then start the bot
CMD ["sh", "-c", "uv run alembic upgrade head && uv run hovorunv2"]
