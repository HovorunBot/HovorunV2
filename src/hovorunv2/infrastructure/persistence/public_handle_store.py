"""Implementation of PublicHandleStore using SQLAlchemy and audited unit of work."""

from functools import partial
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.ports import AuditedUnitOfWork, PublicHandleStore, ResolvedTarget
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import Platform, TargetKind
from hovorunv2.domain.handle import (
    InvalidHandleError,
    PublicHandleDB,
    normalize_handle,
    validate_handle,
)
from hovorunv2.infrastructure.repositories.chat_repository import SQLAlchemyChatRepository
from hovorunv2.infrastructure.repositories.public_handle_repository import SQLAlchemyPublicHandleRepository
from hovorunv2.infrastructure.repositories.user_repository import SQLAlchemyUserRepository


class SQLAlchemyPublicHandleStore(PublicHandleStore):
    """Concrete PublicHandleStore with transaction-level audit logging."""

    def __init__(
        self,
        session_maker: async_sessionmaker,
        audited_uow: AuditedUnitOfWork,
    ) -> None:
        """Initialize store with session maker and audited unit of work."""
        self._session_maker = session_maker
        self._audited_uow = audited_uow

    async def resolve_handle(self, handle: str, platform: Platform = Platform.TELEGRAM) -> ResolvedTarget | None:
        """Resolve public @username to stable numeric target (active only)."""
        norm = normalize_handle(handle)
        if not norm:
            return None

        async with self._session_maker() as session:
            repo = SQLAlchemyPublicHandleRepository(session)
            row = await repo.get_by_handle(norm, platform)
            if not row:
                return None

            # Verify target active status (soft-deletion awareness)
            match row.target_kind:
                case TargetKind.USER:
                    user_repo = SQLAlchemyUserRepository(session)
                    if await user_repo.get_by_telegram_id(row.target_id, str(platform)) is None:
                        return None
                case TargetKind.GROUP:
                    chat_repo = SQLAlchemyChatRepository(session)
                    if await chat_repo.get_by_id(row.target_id, str(platform)) is None:
                        return None

            return ResolvedTarget(
                target_kind=row.target_kind,
                target_id=row.target_id,
                handle_normalized=row.handle_normalized,
                handle_display=row.handle_display,
                platform=row.platform,
            )

    async def get_handle_by_target(
        self, target_kind: TargetKind, target_id: int, platform: Platform = Platform.TELEGRAM
    ) -> ResolvedTarget | None:
        """Get current public handle for a numeric target entity."""
        async with self._session_maker() as session:
            repo = SQLAlchemyPublicHandleRepository(session)
            row = await repo.get_by_target(target_kind, target_id, platform)
            if not row:
                return None
            return ResolvedTarget(
                target_kind=row.target_kind,
                target_id=row.target_id,
                handle_normalized=row.handle_normalized,
                handle_display=row.handle_display,
                platform=row.platform,
            )

    async def _execute_sync_handle(
        self,
        session: AsyncSession,
        target_kind: TargetKind,
        target_id: int,
        raw_handle: str | None,
        platform: Platform,
    ) -> tuple[ResolvedTarget | None, dict[str, Any] | None]:
        """Execute handle synchronization within an audited transaction."""
        norm = normalize_handle(raw_handle)
        display = raw_handle.strip().lstrip("@") if (raw_handle and norm) else None
        if raw_handle and not validate_handle(raw_handle):
            raise InvalidHandleError(raw_handle)

        repo = SQLAlchemyPublicHandleRepository(session)
        existing_target_handle = await repo.get_by_target(target_kind, target_id, platform)

        if not norm or not display:
            if existing_target_handle:
                old_norm = existing_target_handle.handle_normalized
                await repo.delete(existing_target_handle)
                return None, {"handle": [old_norm, None], "status": "removed"}
            return None, None

        # Check if another target holds this handle (stale mapping)
        competing = await repo.get_by_handle(norm, platform)
        if competing and (competing.target_kind != target_kind or competing.target_id != target_id):
            await repo.delete(competing)
            await session.flush()

        if existing_target_handle:
            old_norm = existing_target_handle.handle_normalized
            old_display = existing_target_handle.handle_display
            if old_norm == norm and old_display == display:
                return (
                    ResolvedTarget(
                        target_kind=target_kind,
                        target_id=target_id,
                        handle_normalized=norm,
                        handle_display=display,
                        platform=platform,
                    ),
                    None,
                )
            existing_target_handle.handle_normalized = norm
            existing_target_handle.handle_display = display
            return (
                ResolvedTarget(
                    target_kind=target_kind,
                    target_id=target_id,
                    handle_normalized=norm,
                    handle_display=display,
                    platform=platform,
                ),
                {"handle": [old_norm, norm], "display": [old_display, display]},
            )

        new_handle = PublicHandleDB(
            handle_normalized=norm,
            handle_display=display,
            target_kind=target_kind,
            target_id=target_id,
            platform=platform,
        )
        repo.add(new_handle)
        return (
            ResolvedTarget(
                target_kind=target_kind,
                target_id=target_id,
                handle_normalized=norm,
                handle_display=display,
                platform=platform,
            ),
            {"handle": [None, norm], "display": [None, display]},
        )

    async def sync_handle(
        self,
        target_kind: TargetKind,
        target_id: int,
        raw_handle: str | None,
        actor: ActorContext,
        platform: Platform = Platform.TELEGRAM,
    ) -> ResolvedTarget | None:
        """Synchronize public handle from verified Telegram evidence with audit."""
        op = partial(
            self._execute_sync_handle,
            target_kind=target_kind,
            target_id=target_id,
            raw_handle=raw_handle,
            platform=platform,
        )
        result: tuple[ResolvedTarget | None, dict[str, Any] | None] = await self._audited_uow.execute(
            actor=actor,
            target_type="public_handle",
            target_id=f"{target_kind}:{target_id}",
            action="sync",
            operation=op,
            changed_fields=lambda res: res[1] if res else None,
            audit_if=lambda res: res is not None and res[1] is not None,
        )
        return result[0]

    async def _execute_release_handle(
        self,
        session: AsyncSession,
        target_kind: TargetKind,
        target_id: int,
        platform: Platform,
    ) -> tuple[bool, dict[str, Any] | None]:
        """Execute handle release within an audited transaction."""
        repo = SQLAlchemyPublicHandleRepository(session)
        existing = await repo.get_by_target(target_kind, target_id, platform)
        if not existing:
            return False, None
        old_norm = existing.handle_normalized
        await repo.delete(existing)
        return True, {"handle": [old_norm, None], "status": "released"}

    async def release_handle(
        self,
        target_kind: TargetKind,
        target_id: int,
        actor: ActorContext,
        platform: Platform = Platform.TELEGRAM,
    ) -> bool:
        """Release public handle on target soft-deletion or removal with audit."""
        op = partial(
            self._execute_release_handle,
            target_kind=target_kind,
            target_id=target_id,
            platform=platform,
        )
        result: tuple[bool, dict[str, Any] | None] = await self._audited_uow.execute(
            actor=actor,
            target_type="public_handle",
            target_id=f"{target_kind}:{target_id}",
            action="delete",
            operation=op,
            changed_fields=lambda res: res[1] if res else None,
            audit_if=lambda res: res is not None and res[0] is True,
        )
        return result[0]
