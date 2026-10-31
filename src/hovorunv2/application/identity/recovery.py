"""Authorized preview of identity recovery choices; no restoration happens here."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from hovorunv2.application.access.permission_service import PermissionService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.usage.budgets import Clock
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ActorOrigin, ChatStatus, Role
from hovorunv2.domain.user import UserDB

RECOMMENDATION_THRESHOLD_SECONDS = 24 * 60 * 60


class RecoveryOption(StrEnum):
    """Explicit recovery modes; the recommendation never selects one automatically."""

    RESTORE = "restore"
    FRESH = "fresh"


@dataclass(frozen=True, slots=True)
class UserRecoveryPreview:
    """Safe fact sheet for one deleted user identity."""

    telegram_id: int
    internal_id: int
    role: Role
    is_trusted: bool
    generation: int
    deleted_at: datetime
    age_seconds: float
    dependants: dict[str, int]
    collision: bool
    role_restore_requires_cli: bool
    recommended: RecoveryOption
    options: tuple[RecoveryOption, ...]


@dataclass(frozen=True, slots=True)
class GroupRecoveryPreview:
    """Safe fact sheet for one deleted group route."""

    telegram_id: int
    internal_id: int
    status: ChatStatus
    generation: int
    deleted_at: datetime
    age_seconds: float
    dependants: dict[str, int]
    prior_commands: tuple[str, ...]
    collision: bool
    recommended: RecoveryOption
    options: tuple[RecoveryOption, ...]


class RecoveryStore(Protocol):
    """Read-only dependant counts for a tombstoned identity."""

    async def user_dependants(self, user_id: int, telegram_id: int) -> dict[str, int]:
        """Count retained grants, memberships, admin support and preferences."""
        ...

    async def group_dependants(self, chat_id: int) -> dict[str, int]:
        """Count retained selections, memberships, preferences and approvals."""
        ...

    async def group_commands(self, chat_id: int) -> tuple[str, ...]:
        """List command selections including soft-deleted history."""
        ...


class RecoveryPreviewService:
    """Build an authorized, revision-bound fact sheet without changing state."""

    def __init__(
        self,
        users: UserStore,
        chats: ChatStore,
        store: RecoveryStore,
        permissions: PermissionService,
        clock: Clock,
    ) -> None:
        self._users = users
        self._chats = chats
        self._store = store
        self._permissions = permissions
        self._clock = clock

    async def preview_user(self, telegram_id: int, *, actor: ActorContext) -> UserRecoveryPreview:
        """Inspect the latest deleted user generation with its retained dependants."""
        await self._require_admin(actor)
        history = await self._users.get_historical_by_telegram_id(telegram_id)
        found = self._latest_deleted_user(history)
        if found is None:
            msg = "No deleted user identity exists at that ID."
            raise ValueError(msg)
        tombstone, deleted_at = found
        dependants = await self._store.user_dependants(tombstone.id, telegram_id)
        collision = await self._users.get_by_telegram_id(telegram_id) is not None
        age = self._age(deleted_at)
        return UserRecoveryPreview(
            telegram_id=telegram_id,
            internal_id=tombstone.id,
            role=tombstone.role,
            is_trusted=tombstone.is_trusted,
            generation=tombstone.generation,
            deleted_at=deleted_at,
            age_seconds=age,
            dependants=dependants,
            collision=collision,
            role_restore_requires_cli=tombstone.role >= Role.ADMIN,
            recommended=self._recommend(age),
            options=(RecoveryOption.RESTORE, RecoveryOption.FRESH),
        )

    async def preview_group(self, telegram_id: int, *, actor: ActorContext) -> GroupRecoveryPreview:
        """Inspect the latest deleted group generation with its retained dependants."""
        await self._require_admin(actor)
        history = await self._chats.get_historical_by_chat_id(telegram_id)
        found = self._latest_deleted_chat(history)
        if found is None:
            msg = "No deleted group identity exists at that ID."
            raise ValueError(msg)
        tombstone, deleted_at = found
        dependants = await self._store.group_dependants(tombstone.id)
        commands = await self._store.group_commands(tombstone.id)
        collision = await self._chats.get_chat(telegram_id) is not None
        age = self._age(deleted_at)
        return GroupRecoveryPreview(
            telegram_id=telegram_id,
            internal_id=tombstone.id,
            status=tombstone.status,
            generation=tombstone.generation,
            deleted_at=deleted_at,
            age_seconds=age,
            dependants=dependants,
            prior_commands=commands,
            collision=collision,
            recommended=self._recommend(age),
            options=(RecoveryOption.RESTORE, RecoveryOption.FRESH),
        )

    async def _require_admin(self, actor: ActorContext) -> None:
        if actor.origin == ActorOrigin.CLI:
            return
        if actor.actor_id is None or not await self._permissions.has_authority(actor.actor_id, "bot:administer"):
            msg = "Recovery inspection requires a current bot administrator."
            raise PermissionError(msg)

    def _latest_deleted_user(self, history: list[UserDB]) -> tuple[UserDB, datetime] | None:
        for row in reversed(history):
            if row.deleted_at is not None:
                return row, row.deleted_at
        return None

    def _latest_deleted_chat(self, history: list[ChatDB]) -> tuple[ChatDB, datetime] | None:
        for row in reversed(history):
            if row.deleted_at is not None:
                return row, row.deleted_at
        return None

    def _age(self, deleted_at: datetime) -> float:
        return max(0.0, self._clock.now() - deleted_at.timestamp())

    def _recommend(self, age_seconds: float) -> RecoveryOption:
        return RecoveryOption.RESTORE if age_seconds <= RECOMMENDATION_THRESHOLD_SECONDS else RecoveryOption.FRESH
