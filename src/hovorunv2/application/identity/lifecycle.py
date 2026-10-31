"""Soft-delete user and group identities without losing durable history."""

import logging

from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import PublicHandleStore, UserStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import Platform, TargetKind

logger = logging.getLogger(__name__)


class IdentityLifecycleService:
    """Delete current identity generations through the audited stores and release handles."""

    def __init__(self, users: UserStore, chats: ChatStore, handles: PublicHandleStore) -> None:
        self._users = users
        self._chats = chats
        self._handles = handles

    async def delete_user(self, target: str, *, reason: str, actor: ActorContext) -> str:
        """Soft-delete one active user; the store enforces the protected-administrator invariant."""
        telegram_id = self._numeric(target, "user")
        self._validate_reason(reason)
        user = await self._users.get_by_telegram_id(telegram_id)
        if user is None:
            msg = "No active user exists; deleted identities require explicit recovery."
            raise ValueError(msg)
        if not await self._users.soft_delete_user(telegram_id, actor, reason):
            msg = "No active user exists; deleted identities require explicit recovery."
            raise ValueError(msg)
        await self._release_handle(TargetKind.USER, user.id, actor)
        return f"{telegram_id}\tdeleted\tgeneration={user.generation + 1}"

    async def delete_group(self, target: str, *, reason: str, actor: ActorContext) -> str:
        """Soft-delete one active group route; durable approval records remain untouched."""
        chat_id = self._numeric(target, "group")
        self._validate_reason(reason)
        chat = await self._chats.get_chat(chat_id)
        if chat is None:
            msg = "No active group exists at that identity."
            raise ValueError(msg)
        if not await self._chats.soft_delete_chat(chat_id, actor, reason):
            msg = "No active group exists at that identity."
            raise ValueError(msg)
        await self._release_handle(TargetKind.GROUP, chat.id, actor)
        return f"{chat_id}\tdeleted\tgeneration={chat.generation + 1}"

    async def _release_handle(self, kind: TargetKind, entity_id: int, actor: ActorContext) -> None:
        try:
            await self._handles.release_handle(kind, entity_id, actor, Platform.TELEGRAM)
        except Exception:
            logger.warning(
                "Handle release deferred for %s %d; inactive-target validation remains authoritative", kind, entity_id
            )

    def _numeric(self, target: str, kind: str) -> int:
        try:
            return int(target)
        except ValueError as error:
            msg = f"Deletion target must be a numeric {kind} identity."
            raise ValueError(msg) from error

    def _validate_reason(self, reason: str) -> None:
        if not reason or not reason.strip():
            msg = "Identity deletion requires an internal reason."
            raise ValueError(msg)
