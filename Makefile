# Makefile for HovorunV2
# Automates installation of uv, Python 3.14, and application startup.

export PATH := $(HOME)/.local/bin:$(HOME)/.cargo/bin:$(PATH)

REPO_URL = https://github.com/HovorunBot/HovorunV2

.PHONY: all setup run run-daemon stop update help install-uv install-python install-git checkout migrate

# Default target: setup and run the app
all: setup run

help:
	@echo "Available commands:"
	@echo "  make setup      - Install tools, checkout/update code, and sync dependencies"
	@echo "  make update     - Pull latest changes from main branch and sync"
	@echo "  make migrate    - Apply database migrations via alembic"
	@echo "  make run        - Start the application using 'uv run hovorunv2'"
	@echo "  make run-daemon - Start the application in the background (daemon)"
	@echo "  make stop       - Stop the daemonized application"
	@echo "  make all        - Perform full setup and launch the application"

# 1. Check whether or not uv is installed. If not, install it.
install-uv:
	@command -v uv >/dev/null 2>&1 || { \
		echo "uv not found. Installing via curl..."; \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
	}
	@echo "uv is ready."

# 2. Check whether or not python 3.14 is installed with uv. If not, install.
install-python: install-uv
	@echo "Checking Python 3.14 installation..."
	@uv python install 3.14

# 3. Check whether git is installed, if not, install.
install-git:
	@command -v git >/dev/null 2>&1 || { \
		echo "Git not found. Attempting to install..."; \
		if [ "$$(uname)" = "Darwin" ]; then \
			if command -v brew >/dev/null 2>&1; then \
				brew install git; \
			else \
				echo "Error: Homebrew not found. Please install Git manually."; \
				exit 1; \
			fi; \
		elif [ -f /etc/debian_version ]; then \
			sudo apt-get update && sudo apt-get install -y git; \
		else \
			echo "Error: Please install Git manually for your operating system."; \
			exit 1; \
		fi; \
	}
	@echo "Git is ready."

# 4. Checkout or update the codebase from repository.
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

# 5. Get last version of main branch and sync.
update: checkout
	@uv sync
	@$(MAKE) migrate

# 5.1 Apply migrations.
migrate:
	@echo "Applying database migrations..."
	@PYTHONPATH=src uv run alembic upgrade head

# 6. Setup and sync dependencies.
setup: install-python checkout
	@echo "Syncing project dependencies..."
	@uv sync
	@if [ ! -f .env ]; then \
		echo "No .env file detected. Creating from example.env..."; \
		cp example.env .env 2>/dev/null || true; \
	fi
	@$(MAKE) migrate
	@echo "------------------------------------------------------------"
	@echo "Setup complete."
	@echo "IMPORTANT: Please verify your secrets in the .env file!"
	@echo "------------------------------------------------------------"

# 7. Run the application in foreground.
run: setup
	@echo "Starting HovorunBot..."
	@uv run hovorunv2

# 8. Run the application as a daemon (in background).
run-daemon: setup
	@echo "Starting HovorunBot in background..."
	@nohup uv run hovorunv2 > hovorun.log 2>&1 & echo "Daemon started! Logs are written to hovorun.log. PID: $$!"

# 9. Stop the application.
stop:
	@echo "Stopping HovorunBot..."
	@pkill -f "uv run hovorunv2" || echo "Process not found."
