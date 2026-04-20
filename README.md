# HovorunV2
Second version of best helper bot.

## Getting Started

This project uses a `Makefile` to automate setup and execution.

### Prerequisites
- `make`
- `git` (The Makefile will attempt to install it on macOS via Homebrew or on Debian/Ubuntu via apt if missing).

The `Makefile` will automatically handle the installation of `uv` (package manager) and Python 3.14 if they are missing.

### Quick Start

You can get the application in two ways:

1. **Via Git Clone**:
   ```bash
   git clone https://github.com/HovorunBot/HovorunV2.git
   cd HovorunV2
   make
   ```

2. **Via Standalone Makefile**:
   If you only have the `Makefile`, place it in an empty directory and run:
   ```bash
   make
   ```
   The `Makefile` will automatically initialize the repository and download the rest of the code.

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

- `make setup`: Install tools, checkout or update the codebase from the repository, and synchronize dependencies. It also creates a `.env` file from `example.env` if it's missing.
- `make run`: Starts the application using `uv run main.py`.
- `make run-daemon`: Starts the application in the background (daemon mode) and logs to `hovorun.log`.
- `make update`: Pulls the latest changes from the `main` branch and synchronizes dependencies.
- `make all` (Default): Performs `setup` and then `run`.
- `make help`: Shows the list of available commands.

### Development

To run tests:
```bash
PYTHONPATH=. uv run pytest
```

### Tips & Tricks

#### How to kill a process on macOS
If you need to stop the bot and `Ctrl+C` doesn't work, or if it's running in the background:

1. **Find and kill by name**:
   ```bash
   pkill -f main.py
   ```
   or
   ```bash
   killall python3.14
   ```

2. **Find PID and kill**:
   ```bash
   ps aux | grep main.py
   # Find the PID in the second column
   kill -9 <PID>
   ```

3. **Using Activity Monitor**:
   Open **Activity Monitor.app**, search for `python`, select the process, and click the **X** button at the top.
