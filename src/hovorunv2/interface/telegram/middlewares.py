"""ALL middlewares of Hovorun application."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.dispatcher.middlewares.user_context import EVENT_CONTEXT_KEY, EventContext, UserContextMiddleware
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message, TelegramObject, Update

from hovorunv2.application.access.access_service import AccessService, CommandPolicy
from hovorunv2.application.access.permissions import DecisionReason
from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.features.registry import TriggerKind
from hovorunv2.application.messaging.message_service import MessageService
from hovorunv2.application.messaging.ports import CachePort
from hovorunv2.domain.enums import PROVIDER_SPLIT_COMMANDS, CommandName
from hovorunv2.infrastructure.logger import get_logger, set_log_context
from hovorunv2.interface.telegram.handlers.help import HelpCommand

logger = get_logger(__name__)

DENIAL_COOLDOWN_SECONDS = 60


class LogContextMiddleware(BaseMiddleware):
    """Outer middleware to bind user_id and chat_id into logging ContextVars."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Bind event user and chat IDs to logging context for the current update task."""
        user_id, chat_id = self._extract_ids(event, data)
        set_log_context(user_id=user_id, chat_id=chat_id)
        return await handler(event, data)

    def _extract_ids(self, event: TelegramObject, data: dict[str, Any]) -> tuple[int | None, int | None]:
        """Extract user_id and chat_id from Telegram events or aiogram EventContext."""
        event_context = data.get(EVENT_CONTEXT_KEY)
        if isinstance(event_context, EventContext):
            return event_context.user_id, event_context.chat_id
        if isinstance(event, Update):
            resolved = UserContextMiddleware.resolve_event_context(event)
            return resolved.user_id, resolved.chat_id
        if isinstance(event, (Message, ChatMemberUpdated)):
            from_user = getattr(event, "from_user", None)
            user_id = from_user.id if from_user else None
            chat = getattr(event, "chat", None)
            chat_id = chat.id if chat else None
            return user_id, chat_id
        if isinstance(event, CallbackQuery):
            from_user = getattr(event, "from_user", None)
            user_id = from_user.id if from_user else None
            msg = getattr(event, "message", None)
            chat = getattr(msg, "chat", None) if msg else None
            chat_id = chat.id if chat else None
            return user_id, chat_id
        return None, None


class MessageCacheMiddleware(BaseMiddleware):
    """Cache authorized messages after policy checks."""

    def __init__(self, message_service: MessageService) -> None:
        """Initialize middleware with dependencies."""
        super().__init__()
        self._message_service = message_service

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Cache minimal content only after filters and access checks succeed."""
        if isinstance(event, Message):
            try:
                await self._message_service.cache_message(event)
            except Exception:
                logger.warning("Message caching failed")
        return await handler(event, data)


class AccessMiddleware(BaseMiddleware):
    """Unified middleware to handle all access control via CommandPolicy."""

    def __init__(self, access_service: AccessService, cache: CachePort, settings: AppSettings) -> None:
        """Initialize middleware with dependencies."""
        super().__init__()
        self._access_service = access_service
        self._cache = cache
        self._settings = settings

    async def _handle_private_chat(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
        user_id: int | None,
        policy: CommandPolicy,
        command_name: str | None,
    ) -> Any:
        """Authorize private features without treating trust as group management authority."""
        if not user_id or await self._access_service.is_banned(user_id):
            await self._deny_private_chat(event)
            return None

        if await self._access_service.is_admin(user_id):
            return await handler(event, data)

        settings_entry = (
            policy.requires_group_admin
            and command_name == CommandName.SETTINGS
            and not policy.is_toggleable
            and not policy.requires_admin
        )
        if policy.requires_admin or (policy.requires_group_admin and not settings_entry):
            await self._deny_private_chat(event)
            return None
        if settings_entry:
            # Only the real settings entry dispatches; the handler verifies every selected target.
            return await handler(event, data)

        if await self._access_service.is_trusted(user_id):
            return await handler(event, data)

        await self._deny_private_chat(event)
        return None

    async def _handle_group_chat(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
        user_id: int | None,
        chat_id: int,
        policy: CommandPolicy,
        command_name: str | None,
    ) -> Any:
        """Authorize group interactions: ban enforcement, direct vs automatic differ, bot admin private-only."""
        if policy.requires_admin:
            logger.info("Bot administration rejected in group chat %d", chat_id)
            return None

        if user_id and await self._access_service.is_banned(user_id) and not policy.is_automatic:
            logger.info("Direct action denied for banned user %d in group %d", user_id, chat_id)
            if isinstance(event, CallbackQuery):
                await event.answer("🚫 You are banned from using bot commands.", show_alert=True)
            return None

        bot = data.get("bot")
        is_allowed = await self._access_service.is_allowed(
            user_id=user_id,
            chat_id=chat_id,
            policy=policy,
            bot=bot,
            command_name=command_name,
        )

        if is_allowed:
            return await handler(event, data)

        logger.info("Access denied for %s in chat %d by user %s", command_name or "callback", chat_id, user_id)
        if isinstance(event, Message) and chat_id < 0:
            await self._send_group_denial(event, chat_id)
        if isinstance(event, CallbackQuery):
            await event.answer("🚫 You don't have permission to do this.", show_alert=True)
        return None

    async def _send_group_denial(self, event: Message, chat_id: int) -> None:
        """Send cooldown-limited guidance only for group or administrator bans."""
        decision = await self._access_service.evaluate_group_access(chat_id)
        if decision.reason not in {DecisionReason.GROUP_BANNED, DecisionReason.ADMIN_FULL_BANNED}:
            return
        key = f"access:denial:{chat_id}:{decision.reason}"
        if await self._cache.get(key):
            return
        await self._cache.set(key, 1, expire=DENIAL_COOLDOWN_SECONDS)
        try:
            await event.answer("🚫 Bot processing is unavailable in this group. Contact a bot administrator privately.")
        except TelegramAPIError:
            logger.warning("Could not deliver access guidance in group %d", chat_id)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Check authorization before executing handler with deny-by-default for missing policies."""
        handler_obj = data.get("handler")
        raw_policy = data.get("policy") or get_flag(data, "policy")
        if (
            not isinstance(raw_policy, CommandPolicy)
            and handler_obj
            and hasattr(handler_obj, "flags")
            and isinstance(handler_obj.flags, dict)
        ):
            raw_policy = handler_obj.flags.get("policy")

        policy = raw_policy if isinstance(raw_policy, CommandPolicy) else None
        flag_cmd = get_flag(data, "command_name")
        command_name = (
            data.get("command_name")
            or (flag_cmd if isinstance(flag_cmd, str) else None)
            or (
                handler_obj.flags.get("command_name")
                if handler_obj and hasattr(handler_obj, "flags") and isinstance(handler_obj.flags, dict)
                else None
            )
        )

        if not policy or not isinstance(policy, CommandPolicy):
            logger.warning("Event missing required command policy: denying by default")
            if isinstance(event, CallbackQuery):
                await event.answer("🚫 Access denied.", show_alert=True)
            return None

        user_id, chat_id, is_private = self._extract_event_context(event)
        if not chat_id:
            return None

        feature_allowed = await self._feature_allowed(event, chat_id, user_id, command_name, policy)
        if feature_allowed is False:
            if isinstance(event, CallbackQuery):
                await event.answer("Feature unavailable.", show_alert=True)
            return None
        if feature_allowed and not policy.requires_admin and not policy.requires_group_admin:
            return await handler(event, data)

        if is_private:
            return await self._handle_private_chat(handler, event, data, user_id, policy, command_name)

        return await self._handle_group_chat(handler, event, data, user_id, chat_id, policy, command_name)

    async def _feature_allowed(
        self,
        event: TelegramObject,
        chat_id: int,
        user_id: int | None,
        command_name: str | None,
        policy: CommandPolicy,
    ) -> bool | None:
        """Return no feature decision for public/management actions; unknown features fail closed."""
        if not isinstance(command_name, str):
            return None
        registry = self._access_service.features.registry
        keys = (command_name,) if registry.get(command_name) else PROVIDER_SPLIT_COMMANDS.get(command_name, ())
        if not keys:
            return False if policy.is_toggleable else None
        direct = isinstance(event, CallbackQuery) or (
            isinstance(event, Message) and bool(event.text and event.text.startswith("/"))
        )
        trigger = TriggerKind.DIRECT if direct else TriggerKind.AUTOMATIC
        for key in keys:
            decision = await self._access_service.features.evaluate(str(key), chat_id, user_id, trigger=trigger)
            if decision.allowed:
                return True
        return False

    def _extract_event_context(self, event: TelegramObject) -> tuple[int | None, int, bool]:
        """Extract user_id, chat_id, and private chat flag from a Telegram event."""
        if isinstance(event, Message):
            user_id = (
                event.from_user.id if event.from_user and not event.from_user.is_bot and not event.sender_chat else None
            )
            return user_id, event.chat.id, event.chat.type == ChatType.PRIVATE
        if isinstance(event, CallbackQuery):
            user_id = event.from_user.id if not event.from_user.is_bot else None
            if event.message and isinstance(event.message, Message):
                return user_id, event.message.chat.id, event.message.chat.type == ChatType.PRIVATE
            return user_id, 0, False
        return None, 0, False

    async def _deny_private_chat(self, event: TelegramObject) -> None:
        """Send landing page or access denied alert to non-admin private chat users."""
        if isinstance(event, Message):
            help_cmd = HelpCommand(self._settings, self._access_service)
            await event.answer(help_cmd.get_help_text(), parse_mode="HTML", disable_web_page_preview=True)
        elif isinstance(event, CallbackQuery):
            await event.answer("🚫 Access denied.", show_alert=True)
