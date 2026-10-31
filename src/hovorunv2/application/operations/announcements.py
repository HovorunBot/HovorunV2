"""Encrypted announcement draft composition with faithful audience previews."""

from dataclasses import dataclass
from typing import Protocol

from hovorunv2.application.access.permission_service import PermissionService
from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.jobs.feature_jobs import PayloadPort, PayloadState
from hovorunv2.application.operations.notification_ledger import (
    NotificationEvent,
    NotificationLedgerService,
    NotificationOutcome,
)
from hovorunv2.application.operations.notification_preferences import NotificationCategory
from hovorunv2.application.operations.notifications import NOTIFICATION_BODY_TTL_SECONDS
from hovorunv2.application.usage.budgets import Clock
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


CONFIRMATION_TTL_SECONDS = 300
DISPATCH_BODY_TTL_SECONDS = NOTIFICATION_BODY_TTL_SECONDS


@dataclass(frozen=True, slots=True)
class AnnouncementConfirmation:
    """Single-use authorization bound to actor, revision, snapshot and expiry."""

    id: int
    draft_id: int
    revision: int
    status: str
    expires_at: float
    recipients: tuple[PermissionTarget, ...]
    token: str
    urgent: bool = False
    urgent_reason: str | None = None


@dataclass(frozen=True, slots=True)
class AnnouncementStatus:
    """Current draft, latest confirmation and per-outcome delivery counts."""

    draft: AnnouncementDraft
    confirmation: AnnouncementConfirmation | None
    event_key: str | None
    outcomes: dict[NotificationOutcome, int]


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
        transport_kind: str = "private",
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
        transport_kind: str = "private",
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

    async def snapshot(
        self,
        category: NotificationCategory,
        audience: AnnouncementAudience,
        targets: tuple[PermissionTarget, ...],
    ) -> tuple[PermissionTarget, ...]:
        """Resolve the concrete recipients at confirmation time."""
        ...

    async def confirm(
        self,
        draft: AnnouncementDraft,
        *,
        actor: ActorContext,
        actor_user_id: int,
        recipients: tuple[PermissionTarget, ...],
        expires_at: float,
        urgent: bool = False,
        urgent_reason: str | None = None,
        transport_kind: str = "private",
    ) -> AnnouncementConfirmation:
        """Create one single-use confirmation row."""
        ...

    async def consume(
        self,
        draft_id: int,
        token: str,
        *,
        actor: ActorContext,
        actor_user_id: int,
        urgent_bypass: bool = False,
        transport_kind: str = "private",
    ) -> AnnouncementConfirmation:
        """Atomically consume a fresh pending confirmation or raise a semantic error."""
        ...

    async def cancel(self, draft_id: int, *, actor: ActorContext, transport_kind: str = "private") -> bool:
        """Cancel a pending confirmation; dispatched confirmations are cancelled at the ledger."""
        ...

    async def confirmation(self, draft_id: int) -> AnnouncementConfirmation | None:
        """Return the latest confirmation for inspect/cancel views."""
        ...


class AnnouncementService:
    """Validate authoring input and compute the preview at the draft's current revision."""

    def __init__(
        self,
        store: AnnouncementStore,
        users: UserStore,
        chats: ChatStore,
        ledger: NotificationLedgerService,
        bodies: PayloadPort,
        clock: Clock,
        permissions: PermissionService,
    ) -> None:
        self._store = store
        self._users = users
        self._chats = chats
        self._ledger = ledger
        self._bodies = bodies
        self._clock = clock
        self._permissions = permissions

    async def create(
        self,
        *,
        author: ActorContext,
        category: NotificationCategory,
        audience: AnnouncementAudience,
        target_ids: tuple[int, ...],
        body: str,
        transport_kind: str = "private",
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
            transport_kind=transport_kind,
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
        transport_kind: str = "private",
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
            transport_kind=transport_kind,
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

    async def confirm(
        self,
        draft_id: int,
        *,
        actor: ActorContext,
        urgent: bool = False,
        reason: str | None = None,
        transport_kind: str = "private",
    ) -> AnnouncementConfirmation:
        """Bind the current revision and concrete audience snapshot to one actor."""
        if urgent:
            await self._require_urgent_authority(actor, reason)
        draft = await self._required(draft_id)
        actor_user_id = await self._author_user_id(actor)
        targets = await self._store.targets(draft_id)
        recipients = await self._store.snapshot(draft.category, draft.audience, targets)
        if not recipients:
            msg = "No eligible announcement recipients."
            raise ValueError(msg)
        return await self._store.confirm(
            draft,
            actor=actor,
            actor_user_id=actor_user_id,
            recipients=recipients,
            expires_at=self._clock.now() + CONFIRMATION_TTL_SECONDS,
            urgent=urgent,
            urgent_reason=reason.strip() if reason is not None and reason.strip() else None,
            transport_kind=transport_kind,
        )

    async def dispatch(
        self,
        draft_id: int,
        token: str,
        *,
        actor: ActorContext,
        urgent_bypass: bool = False,
        transport_kind: str = "private",
    ) -> AnnouncementStatus:
        """Consume a confirmation and queue the announcement through the delivery worker."""
        draft = await self._required(draft_id)
        actor_user_id = await self._author_user_id(actor)
        confirmation = await self._store.consume(
            draft_id,
            token,
            actor=actor,
            actor_user_id=actor_user_id,
            urgent_bypass=urgent_bypass,
            transport_kind=transport_kind,
        )
        event_key = self._event_key(draft.id, confirmation.revision)
        sealed = await self._bodies.seal(
            event_key,
            (await self._store.body(draft.id)).encode(),
            self._clock.now() + DISPATCH_BODY_TTL_SECONDS,
            retention_seconds=NOTIFICATION_BODY_TTL_SECONDS,
        )
        if await self._bodies.put(sealed) != PayloadState.READY:
            msg = "Announcement body is not available."
            raise ValueError(msg)
        await self._ledger.ensure_event(
            NotificationEvent(
                event_key,
                draft.category,
                "announcement",
                f"draft:{draft.id}",
                sealed.reference,
                bypass_preferences=confirmation.urgent,
            ),
            actor=actor,
        )
        for recipient in confirmation.recipients:
            await self._ledger.record(
                event_key,
                recipient,
                NotificationOutcome.PENDING,
                attempts=0,
                actor=actor,
            )
        return await self.status(draft.id)

    async def cancel(
        self, draft_id: int, *, actor: ActorContext, transport_kind: str = "private"
    ) -> AnnouncementStatus:
        """Cancel pending authorization or dismiss only this announcement's queued receipts."""
        draft = await self._required(draft_id)
        current = await self._store.confirmation(draft_id)
        changed = await self._store.cancel(draft_id, actor=actor, transport_kind=transport_kind)
        if current is not None and current.status == "dispatched":
            await self._ledger.cancel(self._event_key(draft.id, current.revision), actor=actor)
        _ = changed
        return await self.status(draft.id)

    async def status(self, draft_id: int) -> AnnouncementStatus:
        """Return the latest confirmation and outcome counts for inspect views."""
        draft = await self._required(draft_id)
        confirmation = await self._store.confirmation(draft_id)
        event_key = self._event_key(draft.id, confirmation.revision) if confirmation is not None else None
        outcomes = await self._ledger.outcomes(event_key) if event_key is not None else {}
        return AnnouncementStatus(draft, confirmation, event_key, outcomes)

    async def _require_urgent_authority(self, actor: ActorContext, reason: str | None) -> None:
        if reason is None or not reason.strip():
            msg = "Urgent announcements require an internal reason."
            raise ValueError(msg)
        if actor.actor_id is None or not await self._permissions.has_authority(actor.actor_id, "bot:administer"):
            msg = "Urgent announcements require a current bot administrator."
            raise PermissionError(msg)

    def _event_key(self, draft_id: int, revision: int) -> str:
        return f"announcement.{draft_id}.r{revision}"

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
