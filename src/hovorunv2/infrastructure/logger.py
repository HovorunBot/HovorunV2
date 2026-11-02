"""Configure privacy-filtered console and Telegram logging."""

import asyncio
import html
import logging
import re
import sys
import traceback
from contextvars import ContextVar, Token
from copy import copy
from enum import Enum
from types import TracebackType
from typing import TYPE_CHECKING, Final

from hovorunv2.application.access.administration import AdminAction
from hovorunv2.application.access.permissions import DecisionReason
from hovorunv2.application.features.beta_prompts import BetaDecision
from hovorunv2.application.features.registry import FeatureChannel, FeatureScope, TriggerKind
from hovorunv2.application.groups.activation import ActivationAction, ActivationOutcome
from hovorunv2.application.identity.membership import MembershipStatus
from hovorunv2.application.identity.recovery import RecoveryOption
from hovorunv2.application.jobs.admission import AdmissionState, JobStage, NoticeKind, TicketState
from hovorunv2.application.jobs.feature_jobs import EnqueueState, JobOutcome, JobStatus, PayloadState
from hovorunv2.application.jobs.http_ports import HTTPFailure, TelegramRejection
from hovorunv2.application.media.backend_logging import MediaBackendFailure
from hovorunv2.application.media.errors import MediaFailure
from hovorunv2.application.operations.notification_ledger import NotificationOutcome
from hovorunv2.application.operations.notification_preferences import NotificationCategory
from hovorunv2.application.usage.budgets import ReservationState
from hovorunv2.application.usage.policies import UsageUnit
from hovorunv2.domain.announcement import AnnouncementAudience
from hovorunv2.domain.ban import BanKind
from hovorunv2.domain.enums import ActorOrigin, ChatStatus, CommandName, Platform, Role, TargetKind
from hovorunv2.domain.group_grant import ApprovalBasis, RequestStatus
from hovorunv2.utils.job_logging import current_job_id, current_job_stage

if TYPE_CHECKING:
    from aiogram import Bot

    from hovorunv2.application.access.role_service import RoleService

LOG_FORMAT: Final = (
    "%(asctime)s - %(name)s - %(levelname)s - [user=%(user_id)s chat=%(chat_id)s] "
    "[job=%(job_id)s stage=%(job_stage)s process=%(processName)s] - %(message)s"
)

MAX_TB_LINES: Final = 8
MAX_TELEGRAM_MESSAGE_LENGTH: Final = 4096
CLI_OPERATIONAL_LABELS: Final = frozenset(
    {
        "role",
        "access",
        "trust",
        "feature",
        "lifecycle",
        "audit",
        "add",
        "remove",
        "list",
        "grant",
        "revoke",
        "disable",
        "enable",
        "delete-user",
        "delete-group",
        "restore-user",
        "restore-group",
        "fresh-user",
        "fresh-group",
        "updated",
        "unchanged",
        "user",
        "admin",
        "owner",
        "start",
        "stop",
        "success",
        "failed",
        "denied",
        "allowed",
        "true",
        "false",
        "group",
        "bot",
        "Message",
        "CallbackQuery",
        "ChatMemberUpdated",
        "Update",
        "none",
        "all",
        "unknown",
    }
)
PERMISSION_ACTION_LABELS: Final = frozenset({"bot:own", "bot:administer", "group:settings"})
SAFE_LOG_LABELS: Final = (
    frozenset(
        str(value)
        for enum in (
            CommandName,
            ChatStatus,
            JobStage,
            TicketState,
            JobOutcome,
            MediaFailure,
            MediaBackendFailure,
            HTTPFailure,
            TelegramRejection,
            ReservationState,
            Role,
            Platform,
            ActorOrigin,
            TargetKind,
            BanKind,
            AnnouncementAudience,
            ApprovalBasis,
            RequestStatus,
            DecisionReason,
            AdminAction,
            ActivationAction,
            ActivationOutcome,
            BetaDecision,
            FeatureChannel,
            FeatureScope,
            TriggerKind,
            MembershipStatus,
            RecoveryOption,
            NoticeKind,
            AdmissionState,
            PayloadState,
            EnqueueState,
            JobStatus,
            NotificationOutcome,
            NotificationCategory,
            UsageUnit,
        )
        for value in enum
    )
    | {role.name.lower() for role in Role}
    | CLI_OPERATIONAL_LABELS
    | PERMISSION_ACTION_LABELS
)
BOT_TOKEN_PATTERN: Final = re.compile(r"(bot)\d+:[A-Za-z0-9_-]+")
BEARER_PATTERN: Final = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+")
URL_PATTERN: Final = re.compile(r"https?://\S+")
SAFE_IDENTIFIER_PATTERNS: Final = (
    re.compile(r"[0-9a-f]{32}"),
    re.compile(r"\d+\.\d+(?:\.\d+)*(?:[-+][A-Za-z0-9.]+)?"),
    re.compile(r"(?:feature|family):[a-z0-9_-]+"),
)

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


def redact_sensitive_text(text: str) -> str:
    """Mask tokens, bearer values and URLs even in preformatted message text."""
    redacted = BOT_TOKEN_PATTERN.sub(r"\1[redacted]", text)
    redacted = BEARER_PATTERN.sub(r"\1[redacted]", redacted)
    return URL_PATTERN.sub("[url]", redacted)


def sanitize_library_argument(value: object) -> object:
    """Sanitize third-party library log arguments without dropping technical diagnostics."""
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, type) and issubclass(value, BaseException):
        return value.__name__
    return redact_sensitive_text(str(value))


class PrivacyFormatter(logging.Formatter):
    """Keep operation templates and technical diagnostics, redacting sensitive tokens and payloads."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a copy so another handler cannot supply cached unsanitized exception text."""
        safe = copy(record)
        safe.job_id = current_job_id.get()
        safe.job_stage = current_job_stage.get()
        safe.exc_text = None
        safe.stack_info = None
        safe.msg = redact_sensitive_text(str(record.msg))
        if not record.name.startswith("hovorunv2."):
            if isinstance(record.args, dict):
                safe.args = {key: sanitize_library_argument(value) for key, value in record.args.items()}
            elif record.args:
                safe.args = tuple(sanitize_library_argument(value) for value in record.args)
        elif isinstance(record.args, dict):
            safe.args = {key: safe_log_argument(value) for key, value in record.args.items()}
        elif record.args:
            safe.args = tuple(safe_log_argument(value) for value in record.args)
        return super().format(safe)

    def formatException(  # noqa: N802
        self, ei: tuple[type[BaseException], BaseException, TracebackType | None] | tuple[None, None, None]
    ) -> str:
        """Format exception type, sanitized message and code locations."""
        exception_type, value, tb = ei
        if exception_type is None:
            return ""
        frames = [
            f"{frame.f_globals.get('__name__', 'unknown')}:{frame.f_code.co_name}:{line}"
            for frame, line in traceback.walk_tb(tb)
        ]
        detail = ""
        if value and str(value):
            clean = redact_sensitive_text(str(value))
            if not any(marker in clean.lower() for marker in ("private", "secret", "token")):
                detail = f": {clean}"
        header = f"{exception_type.__name__}{detail}" if detail else exception_type.__name__
        return "\n".join([header, *frames[-MAX_TB_LINES:]])


def safe_log_argument(value: object) -> object:
    """Keep numeric metadata, known labels, exception types and safe internal identifiers."""
    if isinstance(value, type) and issubclass(value, BaseException):
        return value.__name__
    if isinstance(value, Enum):
        candidate = str(value.value)
        if candidate in SAFE_LOG_LABELS:
            return candidate
    if isinstance(value, str) and value in SAFE_LOG_LABELS:
        return value
    if isinstance(value, str) and any(pattern.fullmatch(value) for pattern in SAFE_IDENTIFIER_PATTERNS):
        return value
    return value if value is None or isinstance(value, (bool, int, float)) else "[omitted]"


class LogContextFilter(logging.Filter):
    """Inject user and chat IDs from context variables into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Attach user_id and chat_id attributes to the log record."""
        user_id = current_user_id.get()
        chat_id = current_chat_id.get()
        record.user_id = str(user_id) if user_id is not None else "-"
        record.chat_id = str(chat_id) if chat_id is not None else "-"
        return True


class AsyncTelegramHandler(logging.Handler):
    """Send error records to Telegram owners asynchronously."""

    def __init__(self, bot: Bot, roles: RoleService) -> None:
        """Store the bot and owner lookup for later error delivery."""
        super().__init__()
        self.bot = bot
        self._roles = roles
        self._tasks: set[asyncio.Task] = set()

    def emit(self, record: logging.LogRecord) -> None:
        """Format, sanitize and schedule a log record for owner delivery."""
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
        """Send the message to one owner through the bot."""
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
