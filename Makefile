# Makefile for HovorunV2
# Focused on Docker-based workflow and local development

REPO_URL = https://github.com/HovorunBot/HovorunV2

.PHONY: all setup run stop update help install-git checkout migrate dev run-dev

# Default target: setup and run the app in Docker
all: setup run

help:
	@echo "Available commands:"
	@echo "  make setup      - Prepare .env, data directory, and build Docker images"
	@echo "  make run        - Start the production environment (Bot + Valkey) in Docker"
	@echo "  make stop       - Stop all Docker services"
	@echo "  make update     - Pull latest changes and rebuild"
	@echo "  make migrate    - Manually run database migrations inside Docker"
	@echo "  make dev        - Start development dependencies (Valkey only) in Docker"
	@echo "  make run-dev    - Start Valkey in Docker and run Bot locally (no Docker for Bot)"

# 1. Check whether git is installed.
install-git:
	@command -v git >/dev/null 2>&1 || { \
		echo "Error: Git not found. Please install Git manually."; \
		exit 1; \
	}

# 2. Checkout or update the codebase.
checkout: install-git
	@if [ ! -d .git ]; then \
		echo "No git repository found. Initializing and connecting to $(REPO_URL)..."; \
		git init; \
		git remote add origin $(REPO_URL) 2>/dev/null || git remote set-url origin $(REPO_URL); \
		git fetch origin; \
		git checkout -f -B main origin/main; \
	else \
		echo "Updating project from origin main..."; \
		git checkout main 2>/dev/null || git checkout -b main; \
		git pull origin main; \
	fi

# 3. Setup environment and build images.
setup: checkout
	@if [ ! -f .env ]; then \
		echo "No .env file detected. Creating from example.env..."; \
		cp example.env .env 2>/dev/null || true; \
	fi
	@mkdir -p data
	@if [ -f bot.db ] && [ ! -f data/bot.db ]; then \
		echo "Found existing bot.db. Moving it to data/ directory for Docker persistence..."; \
		mv bot.db data/bot.db; \
	fi
	docker compose build

# 4. Run the production environment in Docker.
# Always rebuilds the bot image to ensure the latest local changes are included.
run:
	docker compose build bot
	docker compose --profile prod up -d

# 5. Stop all services.
stop:
	docker compose --profile prod down --remove-orphans

# 6. Update and rebuild.
update: checkout
	docker compose build

# 7. Apply migrations manually (though they run on startup).
migrate:
	docker compose run --rm bot uv run --no-dev alembic upgrade head

# 7a. Full deployment: stop, setup, migrate, and run as daemon.
deploy: stop setup migrate
	docker compose --profile prod up -d

# 8. Start development tools (Valkey only).
dev:
	docker compose up -d valkey

# 9. Run the bot locally (no Docker for Bot) with Valkey in Docker.
run-dev: dev
	@if [ ! -f .env ]; then \
		echo "Error: .env file missing. Run 'make setup' first."; \
		exit 1; \
	fi
	@echo "Starting HovorunV2 locally..."
	@# Ensure dependencies are synced locally
	uv sync
	@# Load .env and run the bot
	@# We use 'set -a' to export all variables from .env for the command
	@set -a; [ -f .env ] && . ./.env; set +a; uv run hovorunv2
