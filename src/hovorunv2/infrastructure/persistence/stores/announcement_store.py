"""Encrypted announcement draft persistence and recipient preview filtering."""

import secrets
from datetime import UTC, datetime

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.application.messaging.crypto import get_fernet_for_chat
from hovorunv2.application.operations.announcements import AnnouncementDraft, AnnouncementPreview, AnnouncementStore
from hovorunv2.application.operations.notification_preferences import NotificationCategory
from hovorunv2.domain.announcement import AnnouncementAudience, AnnouncementDraftDB, AnnouncementTargetDB
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.ban import BanDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus
from hovorunv2.domain.notification import NotificationPreferenceDB
from hovorunv2.domain.user import UserDB


class SQLAlchemyAnnouncementStore(AnnouncementStore):
    """Keep the authored body encrypted and count recipients through the owning filters."""

    def __init__(self, session_maker: async_sessionmaker, audited_uow: AuditedUnitOfWork, encryption_key: str) -> None:
        self._session_maker = session_maker
        self._audited_uow = audited_uow
        self._encryption_key = encryption_key

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
        """Insert ciphertext and safe metadata; audit never receives the body."""
        salt = secrets.token_hex(16)
        ciphertext = get_fernet_for_chat(self._encryption_key, salt).encrypt(body.encode()).decode()

        async def operation(session: AsyncSession) -> tuple[AnnouncementDraftDB, dict[str, object]]:
            draft = AnnouncementDraftDB(
                author_user_id=author_user_id,
                category=category.value,
                audience=audience.value,
                body_ciphertext=ciphertext,
                body_salt=salt,
                revision=1,
                preview_count=preview.total,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(draft)
            await session.flush()
            await self._replace_targets(session, draft.id, targets)
            return draft, self._changes(category, audience, 1, preview.total, body_changed=True)

        result: tuple[AnnouncementDraftDB, dict[str, object]] = await self._audited_uow.execute(
            actor=author,
            target_type="announcement_draft",
            target_id=str(author_user_id),
            action="create",
            operation=operation,
            changed_fields=lambda outcome: outcome[1],
            audit_if=lambda _outcome: True,
        )
        return self._to_draft(result[0])

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
        """Apply one revision, replacing targets and the encrypted body only when supplied."""
        salt = secrets.token_hex(16) if body is not None else None
        ciphertext = (
            get_fernet_for_chat(self._encryption_key, salt).encrypt(body.encode()).decode()
            if body is not None and salt is not None
            else None
        )

        async def operation(session: AsyncSession) -> tuple[AnnouncementDraftDB, dict[str, object]]:
            draft = await session.get(AnnouncementDraftDB, draft_id)
            if draft is None:
                msg = "Announcement draft does not exist."
                raise ValueError(msg)
            draft.category = category.value
            draft.audience = audience.value
            draft.revision += 1
            draft.preview_count = preview.total
            draft.updated_at = datetime.now(UTC)
            if ciphertext is not None and salt is not None:
                draft.body_ciphertext = ciphertext
                draft.body_salt = salt
            await self._replace_targets(session, draft.id, targets)
            return draft, self._changes(
                category, audience, draft.revision, preview.total, body_changed=body is not None
            )

        result: tuple[AnnouncementDraftDB, dict[str, object]] = await self._audited_uow.execute(
            actor=author,
            target_type="announcement_draft",
            target_id=str(draft_id),
            action="update",
            operation=operation,
            changed_fields=lambda outcome: outcome[1],
            audit_if=lambda _outcome: True,
        )
        return self._to_draft(result[0])

    async def get(self, draft_id: int) -> AnnouncementDraft | None:
        """Read safe metadata for one active draft."""
        async with self._session_maker() as session:
            row = await session.get(AnnouncementDraftDB, draft_id)
            if row is None or row.deleted_at is not None:
                return None
            return self._to_draft(row)

    async def body(self, draft_id: int) -> str:
        """Decrypt the authored body for an authorized composer view."""
        async with self._session_maker() as session:
            row = await session.get(AnnouncementDraftDB, draft_id)
            if row is None or row.deleted_at is not None:
                msg = "Announcement draft does not exist."
                raise ValueError(msg)
            fernet = get_fernet_for_chat(self._encryption_key, row.body_salt)
            return fernet.decrypt(row.body_ciphertext.encode()).decode()

    async def targets(self, draft_id: int) -> tuple[PermissionTarget, ...]:
        """Resolve selected internal identities without profiles."""
        async with self._session_maker() as session:
            rows = (
                await session.scalars(
                    select(AnnouncementTargetDB).where(
                        AnnouncementTargetDB.draft_id == draft_id,
                        AnnouncementTargetDB.deleted_at.is_(None),
                    )
                )
            ).all()
            targets: list[PermissionTarget] = []
            for row in rows:
                target = await self._row_target(session, row)
                if target is not None:
                    targets.append(target)
            return tuple(targets)

    async def _row_target(self, session: AsyncSession, row: AnnouncementTargetDB) -> PermissionTarget | None:
        if row.user_id is not None:
            user = await session.get(UserDB, row.user_id)
            if user is None:
                return None
            return PermissionTarget("user", user.id, user.generation, user.telegram_id)
        if row.group_id is not None:
            chat = await session.get(ChatDB, row.group_id)
            if chat is not None:
                return PermissionTarget("group", chat.id, chat.generation, chat.chat_id)
        return None

    async def count(
        self,
        category: NotificationCategory,
        audience: AnnouncementAudience,
        targets: tuple[PermissionTarget, ...],
    ) -> tuple[int, int]:
        """Count users and groups after deletion, ban and category-preference filtering."""
        user_ids = [t.entity_id for t in targets if t.kind == "user" and t.entity_id is not None]
        group_ids = [t.entity_id for t in targets if t.kind == "group" and t.entity_id is not None]
        async with self._session_maker() as session:
            users = await self._count_users(session, category, audience, user_ids)
            groups = await self._count_groups(session, category, audience, group_ids)
        return users, groups

    async def _count_users(
        self,
        session: AsyncSession,
        category: NotificationCategory,
        audience: AnnouncementAudience,
        user_ids: list[int],
    ) -> int:
        if audience == AnnouncementAudience.GROUPS:
            return 0
        if audience == AnnouncementAudience.SELECTED and not user_ids:
            return 0
        statement = (
            select(func.count())
            .select_from(UserDB)
            .where(UserDB.platform == "telegram", UserDB.deleted_at.is_(None))
            .where(
                ~exists().where(
                    BanDB.target_kind == "user",
                    BanDB.platform == "telegram",
                    BanDB.telegram_id == UserDB.telegram_id,
                    BanDB.revoked_at.is_(None),
                    BanDB.deleted_at.is_(None),
                )
            )
            .where(
                ~exists().where(
                    NotificationPreferenceDB.user_id == UserDB.id,
                    NotificationPreferenceDB.category == category.value,
                    NotificationPreferenceDB.enabled.is_(False),
                    NotificationPreferenceDB.deleted_at.is_(None),
                )
            )
        )
        if audience == AnnouncementAudience.SELECTED:
            statement = statement.where(UserDB.id.in_(user_ids))
        return int(await session.scalar(statement) or 0)

    async def _count_groups(
        self,
        session: AsyncSession,
        category: NotificationCategory,
        audience: AnnouncementAudience,
        group_ids: list[int],
    ) -> int:
        if audience == AnnouncementAudience.USERS:
            return 0
        if audience == AnnouncementAudience.SELECTED and not group_ids:
            return 0
        statement = (
            select(func.count())
            .select_from(ChatDB)
            .where(
                ChatDB.status == ChatStatus.APPROVED,
                ChatDB.deleted_at.is_(None),
                ChatDB.chat_id < 0,
            )
            .where(
                ~exists().where(
                    BanDB.target_kind == "group",
                    BanDB.platform == "telegram",
                    BanDB.telegram_id == ChatDB.chat_id,
                    BanDB.revoked_at.is_(None),
                    BanDB.deleted_at.is_(None),
                )
            )
            .where(
                ~exists().where(
                    NotificationPreferenceDB.group_id == ChatDB.id,
                    NotificationPreferenceDB.category == category.value,
                    NotificationPreferenceDB.enabled.is_(False),
                    NotificationPreferenceDB.deleted_at.is_(None),
                )
            )
        )
        if audience == AnnouncementAudience.SELECTED:
            statement = statement.where(ChatDB.id.in_(group_ids))
        return int(await session.scalar(statement) or 0)

    async def _replace_targets(
        self, session: AsyncSession, draft_id: int, targets: tuple[PermissionTarget, ...]
    ) -> None:
        now = datetime.now(UTC)
        existing = (
            await session.scalars(
                select(AnnouncementTargetDB).where(
                    AnnouncementTargetDB.draft_id == draft_id,
                    AnnouncementTargetDB.deleted_at.is_(None),
                )
            )
        ).all()
        for row in existing:
            row.deleted_at = now
        for target in targets:
            session.add(
                AnnouncementTargetDB(
                    draft_id=draft_id,
                    user_id=target.entity_id if target.kind == "user" else None,
                    group_id=target.entity_id if target.kind == "group" else None,
                    deleted_at=None,
                )
            )

    def _changes(
        self,
        category: NotificationCategory,
        audience: AnnouncementAudience,
        revision: int,
        preview_count: int,
        *,
        body_changed: bool,
    ) -> dict[str, object]:
        return {
            "category": category.value,
            "audience": audience.value,
            "revision": revision,
            "preview_count": preview_count,
            "body_changed": body_changed,
        }

    def _to_draft(self, row: AnnouncementDraftDB) -> AnnouncementDraft:
        return AnnouncementDraft(
            row.id,
            row.author_user_id,
            NotificationCategory(row.category),
            AnnouncementAudience(row.audience),
            row.revision,
            row.preview_count,
        )
