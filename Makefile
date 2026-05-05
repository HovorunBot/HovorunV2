# Makefile for HovorunV2
# Focused on Docker-based workflow and local development

REPO_URL = https://github.com/HovorunBot/HovorunV2
DOCKER_PROD = docker compose --profile prod
BUILD_SENTINEL = .docker-built

.PHONY: all setup run stop update help install-git checkout migrate dev run-dev env-init build-prod clean-build \
        _all _setup _run _update _migrate _deploy

# Public targets with automatic cleanup
all setup run update migrate deploy:
	@$(MAKE) _$@
	@$(MAKE) clean-build

help:
	@echo "Available commands:"
	@echo "  make setup      - Prepare .env, data directory, and build Docker images"
	@echo "  make run        - Start the production environment (Bot + Valkey) in Docker"
	@echo "  make stop       - Stop all Docker services"
	@echo "  make update     - Pull latest changes and rebuild"
	@echo "  make deploy     - Rebuild and restart services (alias for stop + run)"
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
		git fetch origin main; \
		git checkout -f -B main origin/main; \
	else \
		echo "Updating project from origin main..."; \
		if [ -n "$$(git status --porcelain)" ]; then \
			echo "Preserving uncommitted changes..."; \
			git stash push -m "Makefile auto-stash"; \
			git fetch origin main; \
			git checkout -B main origin/main; \
			echo "Restoring uncommitted changes..."; \
			git stash pop; \
		else \
			git fetch origin main; \
			git checkout -B main origin/main; \
		fi; \
	fi
	@$(MAKE) clean-build

# 3. Internal helper for environment initialization.
env-init:
	@if [ ! -f .env ]; then \
		echo "No .env file detected. Creating from example.env..."; \
		cp -n example.env .env 2>/dev/null || true; \
	fi
	@mkdir -p data
	@if [ -f bot.db ] && [ ! -f data/bot.db ]; then \
		echo "Found existing bot.db. Moving it to data/ directory for Docker persistence..."; \
		mv bot.db data/bot.db; \
	fi

# 4. Internal logic (prefixed with _)
_all: _setup _run

_setup: checkout env-init build-prod

_run: build-prod
	$(DOCKER_PROD) up -d --no-build

_update: checkout build-prod

_migrate: build-prod
	$(DOCKER_PROD) run --rm bot uv run --no-dev alembic upgrade head

_deploy: build-prod
	$(DOCKER_PROD) up -d --build --remove-orphans

# 5. Build helpers with sentinel
build-prod: $(BUILD_SENTINEL)

$(BUILD_SENTINEL):
	@echo "Building Docker images..."
	$(DOCKER_PROD) build --pull
	@touch $(BUILD_SENTINEL)

clean-build:
	@rm -f $(BUILD_SENTINEL)

# 6. Stop all services.
stop:
	$(DOCKER_PROD) down --remove-orphans

# 7. Start development tools (Valkey only).
dev:
	docker compose up -d valkey

# 8. Run the bot locally (no Docker for Bot) with Valkey in Docker.
run-dev: dev env-init
	@echo "Starting HovorunV2 locally..."
	uv sync
	@set -a; [ -f .env ] && . ./.env; set +a; uv run hovorunv2
	@$(MAKE) clean-build
