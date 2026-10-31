"""Logging configuration module."""

import asyncio
import html
import logging
import sys
import traceback
from contextvars import ContextVar, Token
from copy import copy
from types import TracebackType
from typing import TYPE_CHECKING, Final

from hovorunv2.domain.enums import ChatStatus, CommandName, Role

if TYPE_CHECKING:
    from aiogram import Bot

    from hovorunv2.application.access.role_service import RoleService

LOG_FORMAT: Final = "%(asctime)s - %(name)s - %(levelname)s - [user=%(user_id)s chat=%(chat_id)s] - %(message)s"

MAX_TB_LINES: Final = 8
MAX_TELEGRAM_MESSAGE_LENGTH: Final = 4096
SAFE_LOG_LABELS: Final = frozenset(str(value) for enum in (CommandName, ChatStatus) for value in enum)

current_user_id: ContextVar[int | None] = ContextVar("current_user_id", default=None)
current_chat_id: ContextVar[int | None] = ContextVar("current_chat_id", default=None)


def set_log_context(
    user_id: int | None = None,
    chat_id: int | None = None,
) -> tuple[Token[int | None], Token[int | None]]:
    """Set logging context variables for the current execution task."""
    return current_user_id.set(user_id), current_chat_id.set(chat_id)


def reset_log_context(tokens: tuple[Token[int | None], Token[int | None]]) -> None:
    """Reset logging context variables using saved tokens."""
    user_token, chat_token = tokens
    current_user_id.reset(user_token)
    current_chat_id.reset(chat_token)


class PrivacyFormatter(logging.Formatter):
    """Keep operation templates and numeric metadata, never exception values or payload arguments."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a copy so another handler cannot supply cached unsanitized exception text."""
        safe = copy(record)
        safe.exc_text = None
        safe.stack_info = None
        if not record.name.startswith("hovorunv2."):
            safe.msg = "External library event"
            safe.args = ()
        elif isinstance(record.args, dict):
            safe.args = {key: safe_log_argument(value) for key, value in record.args.items()}
        elif record.args:
            safe.args = tuple(safe_log_argument(value) for value in record.args)
        return super().format(safe)

    def formatException(  # noqa: N802
        self, ei: tuple[type[BaseException], BaseException, TracebackType | None] | tuple[None, None, None]
    ) -> str:
        """Use exception type and code locations without values, source text, locals, or chains."""
        exception_type, _value, tb = ei
        if exception_type is None:
            return ""
        frames = [
            f"{frame.f_globals.get('__name__', 'unknown')}:{frame.f_code.co_name}:{line}"
            for frame, line in traceback.walk_tb(tb)
        ]
        return "\n".join([exception_type.__name__, *frames[-MAX_TB_LINES:]])


def safe_log_argument(value: object) -> object:
    """Keep numeric metadata, known command/status labels and exception types."""
    if isinstance(value, type) and issubclass(value, BaseException):
        return value.__name__
    if isinstance(value, str) and value in SAFE_LOG_LABELS:
        return value
    return value if value is None or isinstance(value, (bool, int, float)) else "[omitted]"


class LogContextFilter(logging.Filter):
    """Logging filter that injects user_id and chat_id from ContextVars into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Attach user_id and chat_id attributes to the log record."""
        user_id = current_user_id.get()
        chat_id = current_chat_id.get()
        record.user_id = str(user_id) if user_id is not None else "-"
        record.chat_id = str(chat_id) if chat_id is not None else "-"
        return True


class AsyncTelegramHandler(logging.Handler):
    """Logging handler that sends messages to Telegram owners asynchronously."""

    def __init__(self, bot: Bot, roles: RoleService) -> None:
        """Initialize with bot and current owner lookup."""
        super().__init__()
        self.bot = bot
        self._roles = roles
        self._tasks: set[asyncio.Task] = set()

    def emit(self, record: logging.LogRecord) -> None:
        """Process log record."""
        msg = html.escape(PrivacyFormatter(LOG_FORMAT).format(record))

        # Condense message if it's an exception
        if record.exc_info:
            msg = self._condense_exception_log(record)

        # Create tasks in the running loop
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._send_to_owners(msg))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except RuntimeError:
            print(f"[AsyncTelegramHandler Fallback] {msg}", file=sys.stderr)  # noqa: T201

    async def _send_to_owners(self, message: str) -> None:
        """Resolve one active-owner snapshot per log event without recursive error logging."""
        try:
            for owner in await self._roles.list_roles(Role.OWNER):
                await self._send_to_telegram(message, owner.telegram_id)
        except Exception:
            sys.stderr.write("[AsyncTelegramHandler Error] Could not resolve current owners.\n")

    async def drain(self) -> None:
        """Wait for pending error deliveries while database and bot resources remain open."""
        await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def shutdown(self) -> None:
        """Stop accepting records and finish pending sends before bot/database shutdown."""
        logging.getLogger().removeHandler(self)
        await self.drain()
        self.close()

    def _condense_exception_log(self, record: logging.LogRecord) -> str:
        """Escape the same privacy-filtered record used by the console sink."""
        return f"<pre>{html.escape(PrivacyFormatter(LOG_FORMAT).format(record))}</pre>"

    async def _send_to_telegram(self, message: str, owner_id: int) -> None:
        """Actually send the message via Bot."""
        try:
            # We don't escape everything because _condense_exception_log provides HTML tags
            # but we ensure the message isn't insanely large
            safe_msg = message[:MAX_TELEGRAM_MESSAGE_LENGTH]
            await self.bot.send_message(owner_id, f"🚨 <b>Log Alert</b>\n\n{safe_msg}", parse_mode="HTML")
        except Exception:
            sys.stderr.write(f"[AsyncTelegramHandler Error] Failed to send to owner {owner_id}.\n")


def setup_logging(level: int = logging.INFO) -> None:
    """Initialize console logging; Telegram routing is attached separately."""
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear existing handlers
    root_logger.handlers = []

    context_filter = LogContextFilter()

    # Console handler
    formatter = PrivacyFormatter(LOG_FORMAT)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(context_filter)
    root_logger.addHandler(console_handler)


def setup_telegram_logging(bot: Bot, roles: RoleService) -> AsyncTelegramHandler:
    """Attach dynamic error routing even when the owner registry is initially empty."""
    handler = AsyncTelegramHandler(bot, roles)
    handler.setLevel(logging.ERROR)
    handler.setFormatter(PrivacyFormatter(LOG_FORMAT))
    handler.addFilter(LogContextFilter())
    logging.getLogger().addHandler(handler)
    return handler


def get_logger(name: str) -> logging.Logger:
    """Return a logger instance with the specified name."""
    return logging.getLogger(name)
