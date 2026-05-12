"""Logging configuration module."""

import asyncio
import html
import logging
import sys
import traceback
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from aiogram import Bot

MAX_TB_LINES: Final = 8
MAX_TELEGRAM_MESSAGE_LENGTH: Final = 4096


class AsyncTelegramHandler(logging.Handler):
    """Logging handler that sends messages to Telegram owners asynchronously."""

    def __init__(self, bot: Bot, owner_ids: list[int]) -> None:
        """Initialize with bot and owner IDs."""
        super().__init__()
        self.bot = bot
        self.owner_ids = owner_ids
        self._tasks: set[asyncio.Task] = set()

    def emit(self, record: logging.LogRecord) -> None:
        """Process log record."""
        msg = self.format(record)

        # Condense message if it's an exception
        if record.exc_info:
            msg = self._condense_exception_log(record)

        # Create tasks in the running loop
        try:
            loop = asyncio.get_running_loop()
            for owner_id in self.owner_ids:
                task = loop.create_task(self._send_to_telegram(msg, owner_id))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        except RuntimeError:
            print(f"[AsyncTelegramHandler Fallback] {msg}", file=sys.stderr)  # noqa: T201

    def _condense_exception_log(self, record: logging.LogRecord) -> str:
        """Extract key info from exception to fit in Telegram message."""
        # Basic record info
        header = f"{record.levelname} in {record.name}:{record.funcName}:{record.lineno}"
        main_msg = record.getMessage()

        if not record.exc_info:
            return f"<b>{html.escape(header)}</b>\n{html.escape(main_msg)}"

        # Get full traceback as list of lines
        etype, evalue, tb = record.exc_info
        tb_lines = traceback.format_exception(etype, evalue, tb)

        # Extract exception type and message
        exc_summary = f"❌ {etype.__name__ if etype else 'Exception'}: {evalue}"

        # Frames: Get first and last few frames to keep context but save space
        if len(tb_lines) > MAX_TB_LINES:
            condensed_tb = "".join(tb_lines[1:2]) + "...\n" + "".join(tb_lines[-3:])
        else:
            condensed_tb = "".join(tb_lines[1:])

        return (
            f"<b>{html.escape(header)}</b>\n"
            f"{html.escape(main_msg)}\n\n"
            f"<b>{html.escape(exc_summary)}</b>\n\n"
            f"<pre>{html.escape(condensed_tb)}</pre>"
        )

    async def _send_to_telegram(self, message: str, owner_id: int) -> None:
        """Actually send the message via Bot."""
        try:
            # We don't escape everything because _condense_exception_log provides HTML tags
            # but we ensure the message isn't insanely large
            safe_msg = message[:MAX_TELEGRAM_MESSAGE_LENGTH]
            await self.bot.send_message(owner_id, f"🚨 <b>Log Alert</b>\n\n{safe_msg}", parse_mode="HTML")
        except Exception as e:
            print(f"[AsyncTelegramHandler Error] Failed to send to {owner_id}: {e}", file=sys.stderr)  # noqa: T201


def setup_logging(level: int = logging.INFO, bot: Bot | None = None, owner_ids: list[int] | None = None) -> None:
    """Initialize logging with optional Telegram handler."""
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear existing handlers
    root_logger.handlers = []

    # Console handler
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Telegram Error handler
    if bot and owner_ids:
        tg_handler = AsyncTelegramHandler(bot, owner_ids)
        tg_handler.setLevel(logging.ERROR)
        tg_handler.setFormatter(formatter)
        root_logger.addHandler(tg_handler)


def get_logger(name: str) -> logging.Logger:
    """Return a logger instance with the specified name."""
    return logging.getLogger(name)
