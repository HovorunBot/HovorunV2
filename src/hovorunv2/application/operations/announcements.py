"""Encrypted announcement draft composition with faithful audience previews."""

from dataclasses import dataclass
from typing import Protocol

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.operations.notification_preferences import NotificationCategory
from hovorunv2.domain.announcement import AnnouncementAudience
from hovorunv2.domain.audit import ActorContext


@dataclass(frozen=True, slots=True)
class AnnouncementDraft:
    """Safe draft metadata; the authored body is only available through explicit decryption."""

    id: int
    author_user_id: int
    category: NotificationCategory
    audience: AnnouncementAudience
    revision: int
    preview_count: int


@dataclass(frozen=True, slots=True)
class AnnouncementPreview:
    """Recipient counts computed for the current draft revision."""

    revision: int
    users: int
    groups: int

    @property
    def total(self) -> int:
        """Return the combined recipient count."""
        return self.users + self.groups


class AnnouncementStore(Protocol):
    """Persist ciphertext and safe metadata; never accept a plaintext column value."""

    async def create(
        self,
        *,
        author: ActorContext,
        author_user_id: int,
        category: NotificationCategory,
        audience: AnnouncementAudience,
        targets: tuple[PermissionTarget, ...],
        body: str,
        preview: AnnouncementPreview,
    ) -> AnnouncementDraft:
        """Insert one draft and audit safe metadata only."""
        ...

    async def update(
        self,
        draft_id: int,
        *,
        author: ActorContext,
        category: NotificationCategory,
        audience: AnnouncementAudience,
        targets: tuple[PermissionTarget, ...],
        body: str | None,
        preview: AnnouncementPreview,
    ) -> AnnouncementDraft:
        """Apply one authoring revision, replace targets, and audit the body-change marker."""
        ...

    async def get(self, draft_id: int) -> AnnouncementDraft | None:
        """Read safe draft metadata."""
        ...

    async def body(self, draft_id: int) -> str:
        """Decrypt the authored body for an authorized composer view."""
        ...

    async def targets(self, draft_id: int) -> tuple[PermissionTarget, ...]:
        """Read the selected internal targets of a draft."""
        ...

    async def count(
        self,
        category: NotificationCategory,
        audience: AnnouncementAudience,
        targets: tuple[PermissionTarget, ...],
    ) -> tuple[int, int]:
        """Count eligible users and groups after deletion, ban and preference filters."""
        ...


class AnnouncementService:
    """Validate authoring input and compute the preview at the draft's current revision."""

    def __init__(
        self,
        store: AnnouncementStore,
        users: UserStore,
        chats: ChatStore,
    ) -> None:
        self._store = store
        self._users = users
        self._chats = chats

    async def create(
        self,
        *,
        author: ActorContext,
        category: NotificationCategory,
        audience: AnnouncementAudience,
        target_ids: tuple[int, ...],
        body: str,
    ) -> AnnouncementDraft:
        """Resolve selected identities, count eligible recipients and persist revision one."""
        self._validate_body(body)
        author_user_id = await self._author_user_id(author)
        targets = await self._resolve(audience, target_ids)
        counts = await self._store.count(category, audience, targets)
        return await self._store.create(
            author=author,
            author_user_id=author_user_id,
            category=category,
            audience=audience,
            targets=targets,
            body=body,
            preview=AnnouncementPreview(1, *counts),
        )

    async def update(
        self,
        draft_id: int,
        *,
        author: ActorContext,
        category: NotificationCategory,
        audience: AnnouncementAudience,
        target_ids: tuple[int, ...],
        body: str | None,
    ) -> AnnouncementDraft:
        """Apply a new immutable revision and recompute the preview from the same filters."""
        draft = await self._required(draft_id)
        if body is not None:
            self._validate_body(body)
        targets = await self._resolve(audience, target_ids)
        counts = await self._store.count(category, audience, targets)
        return await self._store.update(
            draft.id,
            author=author,
            category=category,
            audience=audience,
            targets=targets,
            body=body,
            preview=AnnouncementPreview(draft.revision + 1, *counts),
        )

    async def preview(self, draft_id: int) -> AnnouncementPreview:
        """Recompute the current revision's count without changing the draft."""
        draft = await self._required(draft_id)
        targets = await self._store.targets(draft_id)
        counts = await self._store.count(draft.category, draft.audience, targets)
        return AnnouncementPreview(draft.revision, *counts)

    async def show(self, draft_id: int) -> tuple[AnnouncementDraft, str]:
        """Return safe metadata and the decrypted body for an authorized composer view."""
        draft = await self._required(draft_id)
        return draft, await self._store.body(draft_id)

    async def _resolve(
        self, audience: AnnouncementAudience, target_ids: tuple[int, ...]
    ) -> tuple[PermissionTarget, ...]:
        if audience != AnnouncementAudience.SELECTED:
            return ()
        resolved: dict[tuple[str, int], PermissionTarget] = {}
        for telegram_id in target_ids:
            target = await self._target(telegram_id)
            resolved[(target.kind, target.entity_id or 0)] = target
        return tuple(resolved.values())

    async def _target(self, telegram_id: int) -> PermissionTarget:
        if telegram_id > 0:
            user = await self._users.get_by_telegram_id(telegram_id)
            if user is None:
                msg = "Announcement target user does not exist."
                raise ValueError(msg)
            return PermissionTarget("user", user.id, user.generation, telegram_id)
        chat = await self._chats.get_chat(telegram_id)
        if chat is None:
            msg = "Announcement target group does not exist."
            raise ValueError(msg)
        return PermissionTarget("group", chat.id, chat.generation, telegram_id)

    async def _author_user_id(self, author: ActorContext) -> int:
        if author.actor_id is None:
            msg = "Announcement drafts require an identified author."
            raise ValueError(msg)
        user = await self._users.get_by_telegram_id(author.actor_id)
        if user is None:
            msg = "Announcement author does not exist."
            raise ValueError(msg)
        return user.id

    async def _required(self, draft_id: int) -> AnnouncementDraft:
        draft = await self._store.get(draft_id)
        if draft is None:
            msg = "Announcement draft does not exist."
            raise ValueError(msg)
        return draft

    def _validate_body(self, body: str) -> None:
        if not body or not body.strip():
            msg = "An announcement body is required."
            raise ValueError(msg)
