"""Base classes and unified rich media command handler."""

import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

from aiogram import Bot, F
from aiogram.filters import Filter
from aiogram.types import Message
from aiogram.utils.magic_filter import MagicFilter

from hovorunv2.application.clients.base import BaseMediaClient
from hovorunv2.application.features import FEATURE_REGISTRY, TriggerKind
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.pipeline import MediaPipeline
from hovorunv2.application.services.access_service import CommandPolicy
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.delivery import TelegramMediaDelivery

if TYPE_CHECKING:
    from aiogram import Router

logger = get_logger(__name__)


class BaseCommand(ABC):
    """Abstract base class defining interface for bot command handlers."""

    POLICY: ClassVar[CommandPolicy] = CommandPolicy()

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique command name/identifier."""

    @property
    def policy(self) -> CommandPolicy:
        """Permission and lifecycle policy enforced on this command."""
        return self.POLICY

    @property
    @abstractmethod
    def trigger(self) -> Filter | MagicFilter | Callable[..., Any]:
        """Aiogram filter triggering this command."""

    @abstractmethod
    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:
        """Handle the triggered command."""

    def register_callbacks(self, router: Router, flags: dict[str, Any]) -> None:
        """Register any callback query handlers associated with this command."""
        return


class RichMediaCommand(BaseCommand):
    """Unified command handler for all rich media providers."""

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(
        requires_admin=False,
        requires_approval=True,
        is_toggleable=True,
        is_automatic=True,
    )

    def __init__(
        self,
        primary_client: BaseMediaClient,
        pipeline: MediaPipeline,
        media_delivery: TelegramMediaDelivery,
    ) -> None:
        """Initialize command with its provider client and shared pipeline/delivery services."""
        FEATURE_REGISTRY.capture(primary_client.name, is_video=True)
        FEATURE_REGISTRY.capture(primary_client.name, is_video=False)
        self._primary_client = primary_client
        self._pipeline = pipeline
        self._delivery = media_delivery

    @property
    def name(self) -> str:
        """Command name derived from provider client."""
        return str(self._primary_client.name)

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
        direct = message.text.startswith("/")
        for match in matches:
            await self._process_match(match, message, bot, direct=direct)

    async def _process_match(
        self,
        match: re.Match[str],
        message: Message,
        bot: Bot,
        *,
        direct: bool,
    ) -> None:
        """Process and deliver a single matched media link."""
        try:
            actor_id = message.from_user.id if not message.sender_chat and message.from_user else None
            trigger = TriggerKind.DIRECT if direct else TriggerKind.AUTOMATIC
            payload = await self._pipeline.process(
                self._primary_client,
                match.group(0),
                message.chat.id,
                actor_id=actor_id,
                trigger=trigger,
            )
            if not payload:
                return

            await self._delivery.deliver(bot, message, payload, self.platform_name)
        except MediaExtractionError:
            logger.warning("Extraction strategies failed for %s", self.name)
        except Exception:
            logger.exception("Failed to process %s link", self.name)
