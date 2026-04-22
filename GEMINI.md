# HovorunV2 Project Context

HovorunV2 is a Telegram helper bot built with Python 3.14+, designed for efficiency and modern asynchronous patterns.

## Project Overview

- **Main Technologies:** Python 3.14, [aiogram](https://github.com/aiogram/aiogram), [Pydantic Settings](https://github.com/pydantic/pydantic-settings), [uv](https://github.com/astral-sh/uv), [diskcache-rs](https://github.com/grantjenks/python-diskcache).
- **Architecture:** 
  - **Layout:** Standard `src` layout (`src/hovorunv2/`).
  - **Services:** Modular services for caching (`CacheService`), database (`DatabaseService`), and message handling (`MessageService`).
  - **Controllers:** `src/hovorunv2/controllers/bot.py` routes incoming messages to registered commands.
  - **Commands:** Decoupled command logic using a `BaseCommand` abstraction and a `@register_command` decorator system for automatic discovery.
  - **Configuration:** Managed via Pydantic Settings, loading from `.env` files.

## Building and Running

This project uses a `Makefile` to automate most tasks.

### Commands
- **Setup:** `make setup` - Installs `uv`, Python 3.14, project dependencies, and initializes the `.env` file.
- **Run:** `make run` - Starts the bot using `uv run hovorunv2`.
- **Daemonize:** `make run-daemon` - Starts the bot in the background, logging to `hovorun.log`.
- **Update:** `make update` - Pulls the latest changes and synchronizes dependencies.
- **Test:** `PYTHONPATH=src uv run pytest` - Runs the test suite.

## Development Conventions

### Code Style & Quality
- **Linter:** Strict [Ruff](https://github.com/astral-sh/ruff) configuration (`ALL` ruleset with minimal ignores). Target version `py314`.
- **Docstrings:** Google convention for all public symbols.
- **Type Hints:** Required for all function signatures and complex variables.

### Adding New Commands
1. Create a new file in `src/hovorunv2/controllers/commands/`.
2. Define a class that inherits from `BaseCommand`.
3. Implement `is_triggered(self, message: Message)` and `handle(self, message: Message, bot: Bot)`.
4. Register the command using the `@register_command` decorator.

```python
from .base import BaseCommand, register_command

@register_command
class MyNewCommand(BaseCommand):
    async def is_triggered(self, message: Message) -> bool:
        return message.text == "/mycommand"

    async def handle(self, message: Message, bot: Bot) -> None:
        await message.answer("Hello!")
```

### Dependency Management
- Use `uv add <package>` to add new dependencies.
- Use `uv add --dev <package>` for development dependencies.
- Always commit `uv.lock` changes.

## Key Files
- `src/hovorunv2/main.py`: Entry point for the application.
- `src/hovorunv2/config.py`: Settings schema and initialization.
- `src/hovorunv2/database.py`: Core persistence logic and whitelisting.
- `src/hovorunv2/cache.py`: High-performance caching service.
- `Makefile`: Central automation script for environment and execution.
