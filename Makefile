# Makefile for HovorunV2
# Focused on Docker-based workflow, development quality gates, and Commitizen releases

REPO_URL = https://github.com/HovorunBot/HovorunV2
DOCKER_PROD = docker compose --profile prod
BUILD_SENTINEL = .docker-built

# Optional target tag/version/increment/message overrides
TAG ?=
VERSION ?= $(TAG)
INCREMENT ?=
MESSAGE ?=

.PHONY: all setup run stop update help install-git checkout migrate dev run-dev env-init build-prod clean-build \
        verify-build tag-release release-tag release-dry-run release-preview changelog \
        test lint format type-check check install-hooks _all _setup _run _update _migrate _deploy

# Public targets with automatic cleanup
all setup run update migrate deploy:
	@$(MAKE) _$@
	@$(MAKE) clean-build

help:
	@echo "Available commands:"
	@echo ""
	@echo "  Docker & Deployment:"
	@echo "    make setup               - Prepare environment, verify target, and build Docker images"
	@echo "                               (Optionally: make setup VERSION=v0.2.0)"
	@echo "    make run                 - Start production environment (Bot + Valkey) in Docker"
	@echo "    make stop                - Stop all Docker services"
	@echo "    make update              - Pull latest changes/tags and rebuild"
	@echo "    make deploy              - Rebuild and restart services (stop + run)"
	@echo "    make migrate             - Run database migrations inside Docker"
	@echo ""
	@echo "  Development & Quality:"
	@echo "    make run-dev             - Run Valkey in Docker and Bot locally with auto-migrations"
	@echo "    make dev                 - Start development dependencies (Valkey only) in Docker"
	@echo "    make test                - Run test suite with pytest"
	@echo "    make lint                - Check formatting and lint rules with Ruff"
	@echo "    make format              - Format codebase and fix auto-fixable lint issues"
	@echo "    make type-check          - Run strict type checking with ty"
	@echo "    make check               - Run complete verification gate (lint + type-check + test)"
	@echo "    make install-hooks       - Install Git pre-commit and commit-msg hooks"
	@echo ""
	@echo "  Release & Versioning (Commitizen):"
	@echo "    make tag-release         - Bump version, update CHANGELOG.md, and create Git tag"
	@echo "                               (Aliases: make release-tag)"
	@echo "                               (Examples: make tag-release VERSION=v0.2.1"
	@echo "                                          make tag-release INCREMENT=patch"
	@echo "                                          make tag-release [MESSAGE=\"Release notes\"])"
	@echo "    make release-dry-run     - Preview next version and changelog changes without modifying files"
	@echo "                               (Alias: make release-preview)"
	@echo "    make changelog           - Re-generate CHANGELOG.md from Conventional Commits"
	@echo ""

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
		git fetch --tags origin main; \
		if [ -n "$(VERSION)" ]; then \
			echo "Checking out specified version: $(VERSION)..."; \
			git checkout -f $(VERSION); \
		else \
			git checkout -f -B main origin/main; \
		fi; \
	else \
		echo "Fetching latest tags and commits from origin..."; \
		git fetch --tags origin; \
		if [ -n "$(VERSION)" ]; then \
			echo "Checking out specified version: $(VERSION)..."; \
			git checkout -f $(VERSION); \
		else \
			if [ -n "$$(git status --porcelain)" ]; then \
				echo "Preserving uncommitted changes..."; \
				git stash push -m "Makefile auto-stash"; \
				git checkout -B main origin/main; \
				echo "Restoring uncommitted changes..."; \
				git stash pop; \
			else \
				git checkout -B main origin/main; \
			fi; \
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

# 4. Build verification banner
verify-build:
	@echo "======================================================="
	@echo " Building HovorunV2"
	@echo " Ref:     $$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo detached)"
	@echo " Tag:     $$(git describe --tags --exact-match 2>/dev/null || git describe --tags --always 2>/dev/null || echo none)"
	@echo " Commit:  $$(git rev-parse --short HEAD 2>/dev/null || echo unknown) ($$(git log -1 --format=%cd --date=short 2>/dev/null || echo unknown))"
	@echo " Version: $$(uv run python -c 'from hovorunv2.application.changelog import get_current_version; print(get_current_version())' 2>/dev/null || echo 0.0.0)"
	@echo "======================================================="

tag-release release-tag:
	@if [ -f CHANGES.md ] && [ -n "$(VERSION)" ]; then \
		CLEAN_VER=$$(echo $(VERSION) | sed 's/^v*//'); \
		if ! grep -q "## $$CLEAN_VER" CHANGES.md && ! grep -q "## v$$CLEAN_VER" CHANGES.md; then \
			echo "Notice: CHANGES.md does not yet contain an entry for $$CLEAN_VER."; \
			echo "        Consider adding user-friendly release notes to CHANGES.md before tagging."; \
		fi; \
	fi; \
	ATM_FLAG=""; \
	if [ -n "$(MESSAGE)" ]; then \
		ATM_FLAG="--annotated-tag-message \"$(MESSAGE)\""; \
	fi; \
	if [ -n "$(VERSION)" ]; then \
		CLEAN_VER=$$(echo $(VERSION) | sed 's/^v*//'); \
		echo "Bumping version to $$CLEAN_VER with Commitizen..."; \
		eval uv run cz bump --yes --no-verify $$ATM_FLAG $$CLEAN_VER; \
	elif [ -n "$(INCREMENT)" ]; then \
		echo "Bumping version ($$INCREMENT) with Commitizen..."; \
		eval uv run cz bump --yes --no-verify $$ATM_FLAG --increment $(INCREMENT); \
	else \
		echo "Bumping version automatically based on Conventional Commits..."; \
		eval uv run cz bump --yes --no-verify $$ATM_FLAG; \
	fi
	@echo ""
	@echo "Successfully created release commit and annotated tag (not pushed)."
	@echo "To push to remote when ready: git push --follow-tags origin main"

release-dry-run release-preview:
	@echo "=== Client-Side Release Notes Preview (CHANGES.md) ==="
	@uv run python -c "from hovorunv2.application.changelog import get_changelog_updates, get_current_version; curr = '$(if $(VERSION),$(VERSION),0.2.1)'; print(get_changelog_updates('0.2.0', curr) or 'No client entry found in CHANGES.md for ' + curr)"
	@echo ""
	@echo "=== Commitizen Technical Changelog Preview (CHANGELOG.md) ==="
	@if [ -n "$(VERSION)" ]; then \
		CLEAN_VER=$$(echo $(VERSION) | sed 's/^v*//'); \
		uv run cz bump --dry-run $$CLEAN_VER; \
	elif [ -n "$(INCREMENT)" ]; then \
		uv run cz bump --dry-run --increment $(INCREMENT); \
	else \
		uv run cz bump --dry-run; \
	fi

changelog:
	@echo "Generating CHANGELOG.md with Commitizen..."
	@uv run cz changelog
	@echo "CHANGELOG.md updated successfully."

test:
	@PYTHONPATH=src uv run pytest

lint:
	@uv run ruff check .
	@uv run ruff format --check .

format:
	@uv run ruff format .
	@uv run ruff check --fix .

type-check:
	@uv run ty check src

check: lint type-check test
	@echo "All quality checks passed!"

# 5. Internal logic (prefixed with _)
_all: _setup _run

_setup: checkout env-init build-prod

_run: build-prod
	$(DOCKER_PROD) up -d --no-build

_update: checkout build-prod

_migrate: build-prod
	$(DOCKER_PROD) run --rm bot uv run --no-dev alembic upgrade head

_deploy: build-prod
	$(DOCKER_PROD) up -d --build --remove-orphans

# 6. Build helpers with sentinel
build-prod: $(BUILD_SENTINEL)

$(BUILD_SENTINEL): verify-build
	@echo "Building Docker images..."
	@RESOLVED_VERSION=$$(uv run python -c 'from hovorunv2.application.changelog import get_current_version; print(get_current_version())' 2>/dev/null || echo "0.0.0"); \
	APP_VERSION=$$RESOLVED_VERSION $(DOCKER_PROD) build --build-arg APP_VERSION=$$RESOLVED_VERSION
	@touch $(BUILD_SENTINEL)

clean-build:
	@rm -f $(BUILD_SENTINEL)

# 7. Stop all services.
stop:
	$(DOCKER_PROD) down --remove-orphans

# 8. Start development tools (Valkey only).
dev:
	docker compose up -d valkey

install-hooks:
	@if command -v git >/dev/null 2>&1 && [ -d .git ]; then \
		echo "Installing Git pre-commit and commit-msg hooks..."; \
		uv run pre-commit install --hook-type pre-commit --hook-type commit-msg; \
	fi

# 9. Run the bot locally (no Docker for Bot) with Valkey in Docker.
run-dev: dev env-init install-hooks
	@echo "Starting HovorunV2 locally..."
	uv sync
	@$(MAKE) clean-build
	@set -a; [ -f .env ] && . ./.env; set +a; \
		trap 'kill -TERM 0 2>/dev/null; exit 0' INT TERM TSTP; \
		uv run alembic upgrade head && exec uv run hovorunv2
