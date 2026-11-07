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

DOCKER_COMPOSE = python3 scripts/docker-stack.py --environment "$(COMPOSE_ENV)" --env-file "$(APP_ENV_FILE)" --version "$(APP_VERSION)" --

# Optional target tag/version/increment/message overrides
TAG ?=
VERSION ?= $(TAG)
INCREMENT ?=
MESSAGE ?=
INVALIDATE ?= 0
FORCE ?= 0

.PHONY: all setup run stop restart restart-dev restart-clean invalidate-runtime update deploy help install-git checkout migrate dev run-dev stop-dev env-init build build-prod \
        infrastructure compose-config compose-run verify-build tag-release release-tag release-dry-run release-preview changelog \
        test test-unit test-fast test-profile test-coverage lint format type-check lint-imports check install-hooks \
        clean backup logs status \
        ai agy gemini codex claude deepseek ai-check ai-next

# Each command uses one complete environment file, never an overlay.
## Run setup, then start the selected Docker stack.
all: setup run

## Fetch/check out code, create server .env if absent, and build the selected role images.
setup: checkout env-init build

## Build and start the selected bot, native workers, Beat, migration service, PostgreSQL and Valkey.
run: build
ifneq ($(filter $(INVALIDATE),1 true yes on TRUE YES ON),)
	@$(MAKE) invalidate-runtime
endif
	$(DOCKER_COMPOSE) up -d --no-build

## Restart the selected stack; set INVALIDATE=1 to purge stale Valkey runtime state before starting.
restart: stop
ifneq ($(filter $(INVALIDATE),1 true yes on TRUE YES ON),)
	@$(MAKE) invalidate-runtime
endif
	@$(MAKE) run

## Restart the development Docker stack; set INVALIDATE=1 to purge stale Valkey runtime state.
restart-dev:
	@$(MAKE) COMPOSE_ENV=dev restart

## Restart the selected stack and invalidate transient Valkey runtime state.
restart-clean:
	@$(MAKE) INVALIDATE=1 restart

## Invalidate transient Valkey runtime state (stale budget reservations, queues, cache); preserves database volumes.
invalidate-runtime:
	$(DOCKER_COMPOSE) invalidate-runtime

## Fetch/check out code and build the selected role images; leave running services unchanged.
update: checkout build

## Build and recreate changed services in the selected stack; remove orphan containers.
deploy: build
	$(DOCKER_COMPOSE) up -d --no-build --remove-orphans

## Build the role images, start PostgreSQL if needed, and run Alembic upgrades.
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
	@printf '  INVALIDATE=1                       Invalidate transient Valkey runtime state on restart/run (default: 0).\n'
	@printf '  FORCE=1                            Force rebuilding images without cache even if they already exist (default: 0).\n'
	@printf '  BACKUP_KEEP=n                      Number of backups to retain (default: 7).\n'
	@printf '  BROWSER_ENABLED=True               Enable the browser fallback worker (default: False).\n'
	@printf '  VALKEY_MAXMEMORY=512mb             Valkey memory ceiling, noeviction (default: 512mb).\n'

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

## Create one release commit with version metadata/changelog, then an annotated tag.
tag-release release-tag: export RELEASE_VERSION = $(VERSION)
tag-release release-tag: export RELEASE_INCREMENT = $(INCREMENT)
tag-release release-tag: export RELEASE_MESSAGE = $(value MESSAGE)
tag-release release-tag:
	@sh scripts/tag-release.sh

## Preview user release notes and Commitizen version/changelog changes without releasing.
release-dry-run release-preview:
	@echo "=== Client-Side Release Notes Preview (CHANGES.md) ==="
	@uv run python -c "from hovorunv2.application.operations.changelog import get_changelog_updates, get_current_version; curr = get_current_version(); target = '$(VERSION)'.lstrip('v'); print(get_changelog_updates(curr, target or curr) or 'No client entry found.')"
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

# Build role images sequentially with persistent BuildKit caching.
# When FORCE=1, bypasses cache and rebuilds all role targets.
## Build selected role images with persistent BuildKit caching; set FORCE=1 to rebuild without cache.
build: verify-build
ifeq ($(filter $(FORCE),1 true yes on TRUE YES ON),)
	$(DOCKER_COMPOSE) build
else
	$(DOCKER_COMPOSE) build --force
endif

## Remove stopped stack containers, unused application images and local build artifacts; preserve volumes.
clean:
	$(DOCKER_COMPOSE) clean

## Snapshot Postgres, Valkey, and Beat state with rotation; BACKUP_KEEP=n controls retention.
backup:
	@sh scripts/backup.sh $(DOCKER_COMPOSE)

## Follow service logs from the last 200 lines.
logs:
	$(DOCKER_COMPOSE) logs -f --tail=200

## Print service health, Valkey memory and queue depth.
status:
	$(DOCKER_COMPOSE) ps
	@echo "--- Valkey memory ---"
	$(DOCKER_COMPOSE) exec -T valkey valkey-cli INFO memory | grep -E "used_memory_human|maxmemory_human|maxmemory_policy"
	@echo "--- Queue depth ---"
	@for queue in media browser operations; do \
		printf 'work:{work}:%s: ' "$$queue"; \
		$(DOCKER_COMPOSE) exec -T valkey valkey-cli LLEN "work:{work}:$$queue"; \
	done

## Build the production role images regardless of COMPOSE_ENV; set FORCE=1 to rebuild.
build-prod:
	@$(MAKE) COMPOSE_ENV=prod FORCE="$(FORCE)" build

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
	@$(MAKE) COMPOSE_ENV=dev INVALIDATE="$(INVALIDATE)" FORCE="$(FORCE)" run

## Remove development containers/network and orphans; preserve data volumes.
stop-dev:
	@$(MAKE) COMPOSE_ENV=dev stop

## Install quality, commit-message and changelog Git hooks in this checkout.
install-hooks:
	@if command -v git >/dev/null 2>&1 && [ -d .git ]; then \
		echo "Installing Git quality, commit-message, and post-commit changelog hooks..."; \
		uv run pre-commit install --hook-type pre-commit --hook-type pre-push --hook-type commit-msg --hook-type post-commit; \
	fi

## Launch an assistant with project workspace settings (TOOL=<codex|agy|gemini|claude|deepseek|custom> [MODEL=...]).
ai:
	@if [ -z "$(TOOL)" ]; then \
		echo "Usage: make ai TOOL=<codex|agy|gemini|claude|deepseek|custom> [MODEL=...] [ARGS=...]"; \
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

## Launch the DeepSeek Harness web UI through npx (optional ARGS="--port 3081 --no-open").
deepseek:
	@$(MAKE) ai TOOL=deepseek MODEL="$(MODEL)" ARGS="$(ARGS)"

## Validate local ai-schemes initiatives (optional SCHEME=path/to/initiative).
ai-check:
	@if [ -n "$(SCHEME)" ]; then \
		uv run python scripts/ai_scheme.py validate "$(SCHEME)"; \
	elif [ -d ai-schemes ]; then \
		schemes=$$(find ai-schemes -mindepth 1 -maxdepth 1 -type d ! -name archive ! -name reports); \
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
