# HovorunV2
Second version of best helper bot.

## Getting Started

This project uses a `Makefile` to automate setup and execution.

### Prerequisites
- `make`
- `git` (The Makefile will attempt to install it on macOS via Homebrew or on Debian/Ubuntu via apt if missing).

### Quick Start

To set up the environment and launch the bot in one command:

```bash
make
```

### Manual Setup

1. **Initialize Environment**: Install `uv`, Python 3.14, and project dependencies.
   ```bash
   make setup
   ```

2. **Configure Secrets**: Open the newly created `.env` file and provide your `BOT_TOKEN`.
   ```bash
   # .env
   BOT_TOKEN=your_telegram_bot_token_here
   ```

3. **Start the Bot**:
   ```bash
   make run
   ```

### Available Commands

- `make setup`: Install `uv`, Python 3.14, and project dependencies. It also creates a `.env` file from `example.env` if it's missing.
- `make run`: Starts the application using `uv run main.py`.
- `make update`: Pulls the latest changes from the `main` branch and synchronizes dependencies.
- `make all` (Default): Performs `setup` and then `run`.
- `make help`: Shows the list of available commands.

### Development

To run tests:
```bash
PYTHONPATH=. uv run pytest
```
