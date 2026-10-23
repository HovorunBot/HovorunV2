"""Base classes and unified rich media command handler."""

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import aiohttp
from aiogram import Bot, F
from aiogram.filters import Filter
from aiogram.types import Message
from aiogram.utils.magic_filter import MagicFilter

from hovorunv2.application.clients.base import BaseMediaClient
from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.media.extractor import MediaExtractor
from hovorunv2.application.services.access_service import CommandPolicy
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.domain.enums import PROVIDER_SPLIT_COMMANDS, CommandName
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.delivery import TelegramMediaDelivery

if TYPE_CHECKING:
    from aiogram import Router
logger = get_logger(__name__)


@runtime_checkable
class BaseCommand(Protocol):
    """Protocol for all bot commands."""

    @property
    def name(self) -> str:
        """Command name for configuration purposes."""
        ...

    @property
    def policy(self) -> CommandPolicy:
        """Access policy for this command."""
        ...

    @property
    def trigger(self) -> Filter | MagicFilter | Callable[..., Any]:
        """Trigger filter for this command."""
        ...

    async def is_triggered(self, message: Message) -> bool:
        """Check if the command should be triggered by the given message."""
        ...

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:
        """Handle the triggered command."""
        ...

    def register_callbacks(self, router: Router, flags: dict[str, Any]) -> None:
        """Register any callback query handlers associated with this command."""
        return


class RichMediaCommand:
    """Unified command handler for all rich media providers."""

    def __init__(
        self,
        primary_client: BaseMediaClient,
        media_extractor: MediaExtractor,
        session: aiohttp.ClientSession,
        command_service: CommandService,
        media_delivery: TelegramMediaDelivery,
    ) -> None:
        """Initialize command with its provider client and shared media services."""
        self._primary_client = primary_client
        self._media_extractor = media_extractor
        self._session = session
        self._command_service = command_service
        self._delivery = media_delivery

    @property
    def name(self) -> str:
        """Command name derived from provider client."""
        return str(self._primary_client.name)

    @property
    def video_command_name(self) -> str:
        """Sub-command name for video capture configuration."""
        cmd_enum = CommandName(self.name)
        return str(PROVIDER_SPLIT_COMMANDS[cmd_enum][0])

    @property
    def post_command_name(self) -> str:
        """Sub-command name for image/text post capture configuration."""
        cmd_enum = CommandName(self.name)
        return str(PROVIDER_SPLIT_COMMANDS[cmd_enum][1])

    @property
    def policy(self) -> CommandPolicy:
        """Media commands require access and are toggleable by default."""
        return CommandPolicy(requires_admin=False, requires_approval=True, is_toggleable=True, auto_enable=True)

    @property
    def pattern(self) -> re.Pattern[str]:
        """Regex pattern to match links handled by this provider."""
        return self._primary_client.pattern

    @property
    def platform_name(self) -> str:
        """Human-readable platform name."""
        return self._primary_client.platform_label

    @property
    def trigger(self) -> MagicFilter:
        """Trigger filter for rich media links."""
        return F.text.regexp(self.pattern) & ~F.from_user.is_bot

    async def is_triggered(self, message: Message) -> bool:
        """Check if message contains links matching the command's pattern."""
        if not message.text or not message.from_user or message.from_user.is_bot:
            return False
        return bool(self.pattern.search(message.text))

    async def handle(
        self,
        message: Message,
        bot: Bot,
        **kwargs: Any,
    ) -> None:
        """Standardized handling of rich media links."""
        if not message.text or not message.from_user:
            return

        matches = list(self.pattern.finditer(message.text))
        if not matches:
            return

        logger.info("Processing %d link(s) for %s from user %s", len(matches), self.name, message.from_user.id)

        for match in matches:
            try:
                payload = await self._extract_payload(self._session, match, message.chat.id, "telegram")
                if not payload:
                    logger.warning("Extraction returned empty payload for %s (%s)", match.group(0), self.name)
                    continue

                if not await self._is_payload_mode_allowed(message.chat.id, payload):
                    logger.info(
                        "Skipping %s link %s in chat %d (capture mode disabled for is_video=%s)",
                        self.name,
                        match.group(0),
                        message.chat.id,
                        payload.is_video,
                    )
                    continue

                await self._delivery.deliver(bot, message, payload, self.platform_name)
            except Exception:
                logger.exception("Failed to process link: %s", match.group(0))

    async def _is_payload_mode_allowed(self, chat_id: int, payload: RichMediaPayload) -> bool:
        """Check whether the extracted payload's media mode (video vs image/text) is enabled."""
        allowed_commands = await self._command_service.get_allowed_commands(chat_id)
        if not allowed_commands:
            return True

        target_cmd = self.video_command_name if payload.is_video else self.post_command_name
        return await self._command_service.is_command_allowed(chat_id, target_cmd)

    async def _extract_payload(
        self, session: aiohttp.ClientSession, match: re.Match[str], chat_id: int, platform: str
    ) -> RichMediaPayload | None:
        """Generalized 4-tier extraction pipeline: Direct API -> yt-dlp -> Browser Fallback -> Metadata."""
        url = match.group(0).rstrip(".,")

        # 1. Direct API (if implemented by provider)
        if payload := await self._primary_client.extract_payload(session, url, chat_id, platform):
            return payload

        # 2. Generic MediaExtractor (yt-dlp) - skip for explicit post/text URLs
        is_post_url = bool(
            self._primary_client.config.post_url_pattern and self._primary_client.config.post_url_pattern.search(url)
        )
        if not is_post_url:
            logger.info("%s direct API returned None for %s, trying yt-dlp", self.name, url)
            if payload := await self._media_extractor.extract_payload(
                session, url, chat_id, platform, cookies=self._primary_client.get_ytdlp_cookies()
            ):
                return payload

        # 3. Browser Fallback (if configured on provider)
        if payload := await self._primary_client.extract_fallback(url, chat_id, platform, session):
            return payload

        # 4. Metadata / OpenGraph fallback (if configured on provider)
        return await self._primary_client.extract_metadata_payload(session, url, chat_id, platform)

    def register_callbacks(self, router: Router, flags: dict[str, Any]) -> None:
        """Rich media commands do not have callbacks by default."""
