"""Bounded, single-use settings windows independent of Telegram callback fields."""

import secrets
import time
from typing import Final

from pydantic import BaseModel, ConfigDict, ValidationError

from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.messaging.ports import CachePort

SETTINGS_SESSION_TTL: Final = 30


class SettingsTarget(BaseModel):
    """Immutable selected group identity kept server-side rather than in callback values."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    entity_id: int
    generation: int
    telegram_id: int


class SettingsContext(BaseModel):
    """Minimal target, transport and operation facts; never message contents."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    target: SettingsTarget | None
    choices: list[SettingsTarget] = []
    transport_chat_id: int
    message_id: int
    actor_id: int | None
    actor_generation: int | None
    expires_at: float
    operations: list[str] = []

    @property
    def target_chat_id(self) -> int:
        """Require an explicit selection before reading or mutating group settings."""
        if self.target is None:
            msg = "No settings target is selected."
            raise ValueError(msg)
        return self.target.telegram_id


class SettingsSessionService:
    """Store opaque UI tokens in Valkey and reject stale identities and routing."""

    def __init__(self, cache: CachePort, chats: ChatStore, users: UserStore) -> None:
        self._cache = cache
        self._chats = chats
        self._users = users

    async def prepare(
        self, target_chat_id: int, actor_id: int, transport_chat_id: int, message_id: int
    ) -> SettingsContext:
        """Prepare trusted transport context, including future private selected-group menus."""
        chat = await self._chats.get_chat(target_chat_id)
        user = await self._users.get_by_telegram_id(actor_id)
        if chat is None or chat.chat_id != target_chat_id or user is None:
            msg = "Settings require an active actor and canonical target."
            raise ValueError(msg)
        if (transport_chat_id < 0 and transport_chat_id != target_chat_id) or (
            transport_chat_id > 0 and transport_chat_id != actor_id
        ):
            msg = "Settings require an active actor and canonical target."
            raise ValueError(msg)
        return SettingsContext(
            target=SettingsTarget(entity_id=chat.id, generation=chat.generation, telegram_id=target_chat_id),
            transport_chat_id=transport_chat_id,
            message_id=message_id,
            actor_id=actor_id if transport_chat_id > 0 else None,
            actor_generation=user.generation if transport_chat_id > 0 else None,
            expires_at=time.time() + SETTINGS_SESSION_TTL,
        )

    async def prepare_listing(self, actor_id: int, transport_chat_id: int, message_id: int) -> SettingsContext:
        """Bind private group discovery to an active user without inventing a group target."""
        user = await self._users.get_by_telegram_id(actor_id)
        if user is None or actor_id != transport_chat_id or transport_chat_id <= 0:
            msg = "Private group discovery requires its active human actor."
            raise ValueError(msg)
        return SettingsContext(
            target=None,
            actor_id=actor_id,
            actor_generation=user.generation,
            transport_chat_id=transport_chat_id,
            message_id=message_id,
            expires_at=time.time() + SETTINGS_SESSION_TTL,
        )

    async def issue(self, context: SettingsContext, operations: list[str]) -> str:
        """Replace the active window token without extending its absolute deadline."""
        remaining = int(context.expires_at - time.time())
        if remaining <= 0:
            msg = "Settings window expired."
            raise ValueError(msg)
        window_key = f"settings:window:{context.transport_chat_id}"
        previous = await self._cache.get(window_key)
        if isinstance(previous, str):
            await self._cache.delete(f"settings:session:{previous}")
        token = secrets.token_urlsafe(9)
        await self._cache.set(
            f"settings:session:{token}",
            context.model_copy(update={"operations": operations}).model_dump(mode="json"),
            expire=remaining,
        )
        await self._cache.set(window_key, token, expire=remaining)
        return token

    async def resolve(
        self,
        token: str,
        operation: str,
        actor_id: int,
        transport_chat_id: int,
        message_id: int,
        *,
        consume: bool = False,
    ) -> SettingsContext | None:
        """Validate one permitted operation against live identity, generation and transport."""
        key = f"settings:session:{token}"
        raw = await self._cache.pop(key) if consume else await self._cache.get(key)
        try:
            context = SettingsContext.model_validate(raw)
        except ValidationError:
            return None
        if not await self._window_matches(context, token, operation, actor_id, transport_chat_id, message_id):
            return None
        user = await self._users.get_by_telegram_id(actor_id)
        if user is None or (context.actor_generation is not None and user.generation != context.actor_generation):
            return None
        if context.target is None:
            return context if context.actor_id == actor_id == transport_chat_id and transport_chat_id > 0 else None
        chat = await self._chats.get_chat(context.target_chat_id)
        if (
            chat is None
            or chat.id != context.target.entity_id
            or chat.generation != context.target.generation
            or chat.chat_id != context.target_chat_id
        ):
            return None
        return context

    async def _window_matches(
        self,
        context: SettingsContext,
        token: str,
        operation: str,
        actor_id: int,
        transport_chat_id: int,
        message_id: int,
    ) -> bool:
        """Check expiry, operation and transport before reading the active window token."""
        if context.expires_at <= time.time() or operation not in context.operations:
            return False
        if context.transport_chat_id != transport_chat_id or context.message_id != message_id:
            return False
        if context.actor_id is not None and context.actor_id != actor_id:
            return False
        return await self._cache.get(f"settings:window:{transport_chat_id}") == token
