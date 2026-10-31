"""Base classes and unified rich media command handler."""

import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

from aiogram import Bot, F
from aiogram.filters import Filter
from aiogram.types import Message
from aiogram.utils.magic_filter import MagicFilter

from hovorunv2.application.access.access_service import CommandPolicy
from hovorunv2.application.clients.base import BaseMediaClient
from hovorunv2.application.features.registry import FEATURE_REGISTRY, TriggerKind
from hovorunv2.application.jobs.admission import AdmissionState, incoming_action_key
from hovorunv2.application.jobs.admission_service import AdmissionService
from hovorunv2.application.jobs.feature_jobs import (
    MAX_JOB_LIFETIME_SECONDS,
    JobEnvelope,
    JobTarget,
    PayloadPort,
)
from hovorunv2.application.ports import ChatStore, UserStore
from hovorunv2.application.usage.budgets import Clock
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import TargetKind
from hovorunv2.infrastructure.logger import get_logger

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
        admission: AdmissionService,
        payloads: PayloadPort,
        chats: ChatStore,
        users: UserStore,
        clock: Clock,
    ) -> None:
        """Initialize command with provider client, admission service and persistence ports."""
        FEATURE_REGISTRY.capture(primary_client.name, is_video=True)
        FEATURE_REGISTRY.capture(primary_client.name, is_video=False)
        self._primary_client = primary_client
        self._admission = admission
        self._payloads = payloads
        self._chats = chats
        self._users = users
        self._clock = clock

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
        return F.text.regexp(self.pattern) & (F.sender_chat | ~F.from_user.is_bot)

    async def is_triggered(self, message: Message) -> bool:
        """Check if message contains links matching the command's pattern."""
        if not message.text:
            return False
        if not message.sender_chat and (not message.from_user or message.from_user.is_bot):
            return False
        return bool(self.pattern.search(message.text))

    async def handle(
        self,
        message: Message,
        bot: Bot,
        **kwargs: Any,
    ) -> None:
        """Standardized handling of rich media links."""
        if not message.text:
            return
        if not message.sender_chat and not message.from_user:
            return

        matches = list(self.pattern.finditer(message.text))
        if not matches:
            return

        actor_label = (
            message.from_user.id if message.from_user else (message.sender_chat.id if message.sender_chat else 0)
        )
        logger.info("Processing %d link(s) for %s from %s", len(matches), self.name, actor_label)
        direct = message.text.startswith("/")
        for match in matches:
            await self._process_match(match, message, direct=direct)

    async def _resolve_sender(self, message: Message) -> tuple[int | None, int | None, str] | None:
        """Resolve numeric actor, generation, and transient label from Telegram message."""
        if message.sender_chat:
            return None, None, message.sender_chat.title or ""
        if message.from_user is None or message.from_user.is_bot:
            return None
        user = await self._users.get_by_telegram_id(message.from_user.id)
        if user is None:
            user = await self._users.ingest_user(
                message.from_user.id,
                actor=ActorContext.system("telegram_identity_evidence"),
            )
        if user is None:
            return None
        return user.telegram_id, user.generation, message.from_user.full_name or ""

    async def _resolve_group_target(
        self, message: Message, actor_id: int | None, *, direct: bool
    ) -> tuple[JobTarget, int, TriggerKind, TargetKind, int] | None:
        """Resolve group chat target, epoch, and charging configuration."""
        chat = await self._chats.get_chat(message.chat.id)
        if chat is None:
            chat = await self._chats.get_or_create_chat(
                message.chat.id,
                actor=ActorContext.system("telegram_identity_evidence"),
            )
        if chat is None:
            return None
        target = JobTarget(
            kind=TargetKind.GROUP,
            entity_id=chat.id,
            generation=chat.generation,
            telegram_id=chat.chat_id,
        )
        if direct:
            if actor_id is None:
                return None
            return target, chat.generation, TriggerKind.DIRECT, TargetKind.USER, actor_id
        return target, chat.generation, TriggerKind.AUTOMATIC, TargetKind.GROUP, chat.chat_id

    async def _resolve_target_and_charging(
        self,
        message: Message,
        actor_id: int | None,
        actor_generation: int | None,
        *,
        direct: bool,
    ) -> tuple[JobTarget, int, TriggerKind, TargetKind, int] | None:
        """Resolve target, epoch, and charge parameters for either group or private chat."""
        if message.chat.id < 0:
            return await self._resolve_group_target(message, actor_id, direct=direct)
        if actor_id is None or actor_generation is None:
            return None
        user = await self._users.get_by_telegram_id(actor_id)
        if user is None:
            return None
        target = JobTarget(
            kind=TargetKind.USER,
            entity_id=user.id,
            generation=user.generation,
            telegram_id=user.telegram_id,
        )
        return target, user.generation, TriggerKind.DIRECT, TargetKind.USER, user.telegram_id

    async def _process_match(
        self,
        match: re.Match[str],
        message: Message,
        *,
        direct: bool,
    ) -> None:
        """Admit and enqueue a single matched media link for worker execution."""
        try:
            sender = await self._resolve_sender(message)
            if sender is None:
                return
            actor_id, actor_generation, sender_label = sender
            target_info = await self._resolve_target_and_charging(message, actor_id, actor_generation, direct=direct)
            if target_info is None:
                return
            target, epoch, trigger, charged_kind, charged_id = target_info
            await self._admit_and_enqueue(
                match.group(0),
                message,
                target,
                epoch,
                actor_id,
                actor_generation,
                sender_label,
                trigger,
                charged_kind,
                charged_id,
            )
        except Exception:
            logger.exception("Failed to admit %s link", self.name)

    async def _admit_and_enqueue(
        self,
        url: str,
        message: Message,
        target: JobTarget,
        epoch: int,
        actor_id: int | None,
        actor_generation: int | None,
        sender_label: str,
        trigger: TriggerKind,
        charged_kind: TargetKind,
        charged_id: int,
    ) -> None:
        """Construct envelope, seal payload and admit ticket to background queue."""
        now = self._clock.now()
        deadline = now + MAX_JOB_LIFETIME_SECONDS
        job_id = uuid.uuid4().hex
        sealed = await self._payloads.seal(job_id, url.encode(), deadline, sender_label=sender_label)
        dedupe_key = incoming_action_key(self.name, target.telegram_id, actor_id, message.message_id, trigger)
        envelope = JobEnvelope(
            job_id=job_id,
            payload_ref=sealed.reference,
            feature=self.name,
            target=target,
            actor_id=actor_id,
            actor_generation=actor_generation,
            trigger=trigger,
            charged_kind=charged_kind,
            charged_telegram_id=charged_id,
            accepted_at=now,
            deadline=deadline,
            dedupe_key=dedupe_key,
            feature_epoch=epoch,
            message_id=message.message_id,
        )
        result = await self._admission.admit(envelope, sealed)
        if result.state == AdmissionState.ACCEPTED:
            await self._admission.handoff(envelope.job_id)
        else:
            logger.info("Media admission denied for %s (%s)", self.name, result.state.value)
