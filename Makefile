# Makefile for HovorunV2
# Automates installation of uv, Python 3.14, and application startup.

.PHONY: all setup run update help install-uv install-python install-git

# Default target: setup and run the app
all: setup run

help:
	@echo "Available commands:"
	@echo "  make setup    - Install tools (uv, Python 3.14) and project dependencies"
	@echo "  make update   - Pull latest changes from main branch and sync"
	@echo "  make run      - Start the application using 'uv run main.py'"
	@echo "  make all      - Perform full setup and launch the application"

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

# 4. Get last version of main branch from repository via git.
update: install-git
	@echo "Updating project from origin main..."
	@git pull origin main
	@uv sync

# 5. setup and sync dependencies.
setup: install-uv install-python
	@echo "Syncing project dependencies..."
	@uv sync
	@if [ ! -f .env ]; then \
		echo "No .env file detected. Creating from example.env..."; \
		cp example.env .env; \
	fi
	@echo "------------------------------------------------------------"
	@echo "Setup complete."
	@echo "IMPORTANT: Please verify your secrets in the .env file!"
	@echo "------------------------------------------------------------"

# 6. Run the application.
run: setup
	@echo "Starting HovorunBot..."
	@uv run main.py
