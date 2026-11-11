"""Commit speech mode and minimal audit metadata in one parent-locked transaction."""

from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.access.ports import AuthorizationPolicy
from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.application.speech.ports import SpeechSettingsStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import SPEECH_COMMANDS, SpeechMode
from hovorunv2.infrastructure.persistence.locking import MutationLocks, lock_async_mutation
from hovorunv2.infrastructure.persistence.repositories.speech_settings_repository import SpeechSettingsRepository


class SQLAlchemySpeechSettingsStore(SpeechSettingsStore):
    """Expose three-state configuration without changing independent media selections."""

    def __init__(
        self, session_maker: async_sessionmaker, audited_uow: AuditedUnitOfWork, policy: AuthorizationPolicy
    ) -> None:
        self._session_maker = session_maker
        self._audited_uow = audited_uow
        self._policy = policy

    async def mode(self, target: PermissionTarget) -> SpeechMode:
        """Read an exact active mode through the owning session.

        Args:
            target: Current private user or canonical group identity and generation.

        Returns:
            Stored mode; unavailable targets raise ValueError.
        """
        async with self._session_maker() as session:
            return await session.run_sync(self._read_mode, target)

    def _read_mode(self, session: Session, target: PermissionTarget) -> SpeechMode:
        """Read through the common synchronous session-bound repository."""
        return SpeechSettingsRepository(session, self._policy).mode(target)

    async def _mutate(
        self,
        session: AsyncSession,
        *,
        target: PermissionTarget,
        mode: SpeechMode,
        actor: ActorContext,
        transport_kind: str | None,
    ) -> tuple[SpeechMode, bool]:
        """Lock current subjects and emergency state before authority revalidation."""
        await lock_async_mutation(
            session,
            MutationLocks(
                chats=(target.telegram_id or 0,),
                user_ids=(target.entity_id or 0,) if target.kind == "user" else (),
                group_ids=(target.entity_id or 0,) if target.kind == "group" else (),
                actors=(actor.actor_id,) if actor.actor_id is not None else (),
                keys=tuple(f"feature:{command}" for command in SPEECH_COMMANDS),
            ),
        )
        return await session.run_sync(self._set_mode, target, mode, actor, transport_kind)

    def _set_mode(
        self,
        session: Session,
        target: PermissionTarget,
        mode: SpeechMode,
        actor: ActorContext,
        transport_kind: str | None,
    ) -> tuple[SpeechMode, bool]:
        """Apply one mode using fresh facts after acquiring transaction locks."""
        return SpeechSettingsRepository(session, self._policy).set_mode(target, mode, actor, transport_kind)

    async def set_mode(
        self, target: PermissionTarget, mode: SpeechMode, *, actor: ActorContext, transport_kind: str | None
    ) -> SpeechMode:
        """Authorize and commit a three-state mode with no private content in audit.

        Args:
            target: Exact private user or canonical group identity and generation.
            mode: Desired typed speech mode.
            actor: Authenticated human or CLI operator.
            transport_kind: Telegram ingress scope, or None for CLI.

        Returns:
            Accepted mode, including an idempotent unchanged value.

        Raises:
            TypeError: If the supplied mode is not a typed three-state value.
        """
        if not isinstance(mode, SpeechMode):
            msg = "Speech configuration requires a typed three-state mode."
            raise TypeError(msg)
        result: tuple[SpeechMode, bool] = await self._audited_uow.execute(
            actor=actor,
            target_type="speech_settings",
            target_id=f"{target.kind}:{target.entity_id}:{target.generation}",
            action="set_mode",
            operation=partial(self._mutate, target=target, mode=mode, actor=actor, transport_kind=transport_kind),
            changed_fields={"mode": mode.value},
            audit_if=lambda outcome: outcome[1],
        )
        return result[0]
