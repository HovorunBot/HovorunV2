# Makefile for HovorunV2
# Focused on Docker-based workflow, development quality gates, and Commitizen releases

REPO_URL = https://github.com/HovorunBot/HovorunV2
COMPOSE_ENV ?= prod
APP_ENV_FILE ?= .env
APP_VERSION ?= $(shell sed -n 's/^version = "\([^"]*\)"/\1/p' pyproject.toml)
export APP_ENV_FILE APP_VERSION

ifneq ($(words $(COMPOSE_ENV)),1)
$(error COMPOSE_ENV must be dev or prod)
endif
ifeq ($(filter $(COMPOSE_ENV),dev prod),)
$(error COMPOSE_ENV must be dev or prod)
endif

DOCKER_COMPOSE = docker compose --env-file $(if $(wildcard $(APP_ENV_FILE)),"$(APP_ENV_FILE)",/dev/null) --file docker-compose.$(COMPOSE_ENV).yml

# Optional target tag/version/increment/message overrides
TAG ?=
VERSION ?= $(TAG)
INCREMENT ?=
MESSAGE ?=

.PHONY: all setup run stop update deploy help install-git checkout migrate dev run-dev stop-dev env-init build build-prod \
        infrastructure compose-config compose-run verify-build tag-release release-tag release-dry-run release-preview changelog \
        test test-fast test-profile test-coverage lint format type-check lint-imports check install-hooks ai agy gemini codex claude ai-check ai-next

# Each command uses one complete environment file, never an overlay.
## Run setup, then start the selected Docker stack.
all: setup run

## Fetch/check out code, create server .env if absent, and build the selected image.
setup: checkout env-init build

## Build and start the selected bot, migration service, PostgreSQL and Valkey.
run: build
	$(DOCKER_COMPOSE) up -d --no-build

## Fetch/check out code and build the selected image; leave running services unchanged.
update: checkout build

## Build and recreate changed services in the selected stack; remove orphan containers.
deploy: build
	$(DOCKER_COMPOSE) up -d --no-build --remove-orphans

## Build the image, start PostgreSQL if needed, and run Alembic upgrades.
migrate: build
	$(DOCKER_COMPOSE) run --rm migrate

## Validate the selected standalone Compose configuration without starting services.
compose-config:
	$(DOCKER_COMPOSE) config --quiet

## Run a one-off service/command in the selected stack using ARGS.
compose-run:
	$(DOCKER_COMPOSE) run --rm $(ARGS)

## Show every command description and supported options.
help:
	@printf 'Commands:\n'
	@awk '/^## / {description = substr($$0, 4); next} /^[a-zA-Z0-9_-][^:]*:/ {split($$0, target, ":"); if (description != "") printf "  %-34s %s\n", target[1], description; description = ""}' $(MAKEFILE_LIST)
	@printf '\nOptions:\n'
	@printf '  COMPOSE_ENV=dev|prod                Select one standalone Compose file (default: prod).\n'
	@printf '  APP_ENV_FILE=path                  Select private environment settings (default: .env).\n'
	@printf '  APP_VERSION=value                  Override the image/app version (default: pyproject.toml).\n'
	@printf '  ARGS="service command ..."          Arguments for compose-run, including optional run flags.\n'
	@printf '  VERSION=vX.Y.Z                     Select a setup/update version or explicit release version.\n'
	@printf '  INCREMENT=patch|minor|major         Choose the release increment instead of VERSION.\n'
	@printf '  MESSAGE="Release notes"             Set the annotated release-tag message.\n'

# 1. Check whether git is installed.
## Check that Git is installed; fail if it is unavailable.
install-git:
	@command -v git >/dev/null 2>&1 || { \
		echo "Error: Git not found. Please install Git manually."; \
		exit 1; \
	}

# 2. Checkout or update the codebase.
## Fetch origin/tags and switch to VERSION or origin/main; force checkout can discard edits.
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

# 3. Internal helper for environment initialization.
## Generate private server .env once; preserve any existing file.
env-init:
	@python3 scripts/init-server-env.py

# 4. Build verification banner
## Print the Git ref, tag, commit/date and application version without building.
verify-build:
	@echo "======================================================="
	@echo " Building HovorunV2"
	@echo " Ref:     $$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo detached)"
	@echo " Tag:     $$(git describe --tags --exact-match 2>/dev/null || git describe --tags --always 2>/dev/null || echo none)"
	@echo " Commit:  $$(git rev-parse --short HEAD 2>/dev/null || echo unknown) ($$(git log -1 --format=%cd --date=short 2>/dev/null || echo unknown))"
	@echo " Version: $(APP_VERSION)"
	@echo "======================================================="

## Create a release commit, changelog and annotated tag using VERSION or INCREMENT.
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

## Preview user release notes and Commitizen version/changelog changes without releasing.
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

## Regenerate CHANGELOG.md from Conventional Commits.
changelog:
	@echo "Generating CHANGELOG.md with Commitizen..."
	@uv run cz changelog
	@echo "CHANGELOG.md updated successfully."

## Run the full pytest suite against configured test infrastructure.
test:
	@PYTHONPATH=src uv run pytest

## Run pure tests without PostgreSQL or Valkey.
.PHONY: test-unit
test-unit:
	@PYTHONPATH=src uv run pytest -n 0 -m unit

## Run fast tests excluding slow, browser, and subprocess suites.
test-fast:
	@PYTHONPATH=src uv run pytest -m "not slow and not browser and not subprocess"

## Run pytest with durations aggregated by phase (setup, call, teardown).
test-profile:
	@PYTHONPATH=src uv run pytest --durations=0 --durations-min=0 -q 2>&1 | awk '$$2=="setup"||$$2=="call"||$$2=="teardown" {sub("s","",$$1); t[$$2]+=$$1; n[$$2]++; if ($$2=="setup" && $$1>0.15) slow++} END {for (k in t) printf "%s total=%.1fs n=%d\n", k, t[k], n[k]; print "setups>0.15s:", slow}'

## Run full test suite with branch coverage and per-test execution contexts.
test-coverage:
	@PYTHONPATH=src uv run pytest --cov=src/hovorunv2 --cov-branch --cov-context=test --cov-report=term-missing:skip-covered

## Check Ruff lint rules and Python formatting without editing files.
lint:
	@uv run ruff check .
	@uv run ruff format --check .

## Format Python files and apply Ruff automatic fixes.
format:
	@uv run ruff format .
	@uv run ruff check --fix .

## Run strict ty type checking on src.
type-check:
	@uv run ty check src

## Check application layer boundaries with import-linter.
lint-imports:
	@PYTHONPATH=src uv run lint-imports

## Run lint, type, import-boundary and full test checks.
check: lint type-check lint-imports test
	@echo "All quality checks passed!"

# Docker handles build caching; one application image is shared within each environment.
## Print build metadata and build the selected application image using Docker cache.
build: verify-build
	$(DOCKER_COMPOSE) build bot

## Build the production image regardless of COMPOSE_ENV.
build-prod:
	@$(MAKE) COMPOSE_ENV=prod build

## Remove selected stack containers/network and orphans; preserve data volumes.
stop:
	$(DOCKER_COMPOSE) down --remove-orphans

## Start selected PostgreSQL/Valkey containers and wait for healthy services.
infrastructure:
	$(DOCKER_COMPOSE) up -d --wait postgres valkey

## Start only development PostgreSQL/Valkey for host-side tests.
dev:
	@$(MAKE) COMPOSE_ENV=dev infrastructure

## Build and start the complete development Docker stack.
run-dev:
	@$(MAKE) COMPOSE_ENV=dev run

## Remove development containers/network and orphans; preserve data volumes.
stop-dev:
	@$(MAKE) COMPOSE_ENV=dev stop

## Install quality, commit-message and changelog Git hooks in this checkout.
install-hooks:
	@if command -v git >/dev/null 2>&1 && [ -d .git ]; then \
		echo "Installing Git quality, commit-message, and post-commit changelog hooks..."; \
		uv run pre-commit install --hook-type pre-commit --hook-type pre-push --hook-type commit-msg --hook-type post-commit; \
	fi

## Launch an assistant CLI with sandbox and workspace settings (TOOL=<codex|agy|gemini|claude|custom> [MODEL=...]).
ai:
	@if [ -z "$(TOOL)" ]; then \
		echo "Usage: make ai TOOL=<codex|agy|gemini|claude|custom> [MODEL=...] [ARGS=...]"; \
		exit 2; \
	fi; \
	AI_MODEL="$(MODEL)" ./scripts/ai-workflow $(TOOL) $(ARGS)

## Launch Antigravity (Gemini) CLI assistant with project sandbox (optional MODEL=...).
agy:
	@$(MAKE) ai TOOL=agy MODEL="$(MODEL)" ARGS="$(ARGS)"

## Alias for agy: launch Antigravity (Gemini) CLI assistant.
gemini: agy

## Launch OpenAI Codex assistant with project sandbox and approvals (optional MODEL=...).
codex:
	@$(MAKE) ai TOOL=codex MODEL="$(MODEL)" ARGS="$(ARGS)"

## Launch Claude Code assistant with project settings (optional MODEL=...).
claude:
	@$(MAKE) ai TOOL=claude MODEL="$(MODEL)" ARGS="$(ARGS)"

## Validate local ai-schemes initiatives (optional SCHEME=path/to/initiative).
ai-check:
	@if [ -n "$(SCHEME)" ]; then \
		uv run python scripts/ai_scheme.py validate "$(SCHEME)"; \
	elif [ -d ai-schemes ]; then \
		schemes=$$(find ai-schemes -mindepth 1 -maxdepth 1 -type d); \
		if [ -n "$$schemes" ]; then \
			uv run python scripts/ai_scheme.py validate $$schemes; \
		else \
			echo "No local ai-schemes initiatives found."; \
		fi; \
	else \
		echo "No local ai-schemes directory."; \
	fi

## Print the next eligible TODO block in an initiative (SCHEME=path/to/initiative).
ai-next:
	@if [ -z "$(SCHEME)" ]; then \
		echo "Usage: make ai-next SCHEME=ai-schemes/<initiative>"; \
		exit 2; \
	fi; \
	uv run python scripts/ai_scheme.py next "$(SCHEME)"
