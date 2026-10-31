"""Audited feature emergency transitions with current private/CLI authority checks."""

from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.access.ports import AuthorizationPolicy
from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.application.features.emergency import (
    FeatureEmergencyState,
    FeatureEmergencyStore,
    resolve_emergency_key,
)
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import PROVIDER_SPLIT_COMMANDS, CommandName
from hovorunv2.infrastructure.persistence.admin_authorization import require_private_administrator
from hovorunv2.infrastructure.persistence.repositories.feature_emergency_repository import (
    FeatureEmergencyRepository,
)

MESSAGE = "Feature emergency changes require CLI or a current unrestricted bot administrator privately."


class SQLAlchemyFeatureEmergencyStore(FeatureEmergencyStore):
    """Keep the epoch authoritative; a transition is one audited transaction."""

    def __init__(
        self,
        session_maker: async_sessionmaker,
        audited_uow: AuditedUnitOfWork,
        policy: AuthorizationPolicy,
    ) -> None:
        self._session_maker = session_maker
        self._audited_uow = audited_uow
        self._policy = policy

    def _key(self, feature_key: str) -> str:
        """Normalize split keys to their provider family and reject unknown features."""
        key = resolve_emergency_key(feature_key)
        try:
            command = CommandName(key)
        except ValueError:
            msg = "Unknown feature for emergency state."
            raise ValueError(msg) from None
        if command not in PROVIDER_SPLIT_COMMANDS:
            msg = "Emergency state applies to media provider commands."
            raise ValueError(msg)
        return key

    async def state(self, feature_key: str) -> FeatureEmergencyState:
        """Read state through an owning session without granting execution."""
        async with self._session_maker() as session:
            return await FeatureEmergencyRepository(session).state(self._key(feature_key))

    async def _mutate(
        self,
        session: AsyncSession,
        *,
        feature_key: str,
        disabled: bool,
        reason: str,
        actor: ActorContext,
        transport_kind: str | None,
    ) -> tuple[FeatureEmergencyState, dict[str, object]]:
        await require_private_administrator(session, actor, transport_kind, self._policy, message=MESSAGE)
        return await FeatureEmergencyRepository(session).set_state(feature_key, disabled=disabled, reason=reason)

    async def set_state(
        self,
        feature_key: str,
        *,
        disabled: bool,
        reason: str,
        actor: ActorContext,
        transport_kind: str | None,
    ) -> FeatureEmergencyState:
        """Commit one authoritative transition and its audit event atomically."""
        key = self._key(feature_key)
        result: tuple[FeatureEmergencyState, dict[str, object]] = await self._audited_uow.execute(
            actor=actor,
            target_type="feature_emergency",
            target_id=key,
            action="disable" if disabled else "enable",
            operation=partial(
                self._mutate,
                feature_key=key,
                disabled=disabled,
                reason=reason,
                actor=actor,
                transport_kind=transport_kind,
            ),
            changed_fields=lambda outcome: outcome[1],
            audit_if=lambda _: True,
        )
        return result[0]
