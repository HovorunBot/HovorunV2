"""Atomic PostgreSQL accounting transactions with replay-safe receipts and one batch audit."""

from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.features.registry import FeatureRegistry
from hovorunv2.application.ports import AuditedUnitOfWork
from hovorunv2.application.usage.policies import UsageUnit, UsageWindow
from hovorunv2.application.usage.usage_ledger import (
    AccountingSubject,
    AppliedUsage,
    UsageEvent,
    UsageLedgerStore,
    usage_audit_changes,
    usage_batch_id,
    validate_usage_batch,
)
from hovorunv2.domain.audit import ActorContext
from hovorunv2.infrastructure.repositories.usage_ledger_repository import SQLAlchemyUsageLedgerRepository


class SQLAlchemyUsageLedgerStore(UsageLedgerStore):
    """Retain accounting independently of entity recovery and runtime queue state."""

    def __init__(
        self, session_maker: async_sessionmaker, audited_uow: AuditedUnitOfWork, registry: FeatureRegistry
    ) -> None:
        self._session_maker = session_maker
        self._audited_uow = audited_uow
        self._registry = registry

    async def _apply(self, session: AsyncSession, events: tuple[UsageEvent, ...]) -> AppliedUsage:
        return await SQLAlchemyUsageLedgerRepository(session).apply(events)

    async def apply_batch(self, events: tuple[UsageEvent, ...], *, actor: ActorContext) -> AppliedUsage:
        """Commit receipts, counters and one payload-free audit summary or roll back all three."""
        validate_usage_batch(self._registry, events, actor)
        batch_id = usage_batch_id(events)
        return await self._audited_uow.execute(
            actor=actor,
            target_type="usage_batch",
            target_id=batch_id,
            action="apply",
            operation=partial(self._apply, events=events),
            changed_fields=usage_audit_changes,
            audit_if=lambda result: result.events_applied > 0,
        )

    async def total(
        self,
        subject: AccountingSubject,
        bucket: str,
        unit: UsageUnit,
        window: UsageWindow,
        timestamp: int,
    ) -> int:
        """Read accounting for active or historical subjects without granting execution access."""
        async with self._session_maker() as session:
            repository = SQLAlchemyUsageLedgerRepository(session)
            await repository.require_known_subject(subject)
            row = await repository.aggregate(subject, bucket, unit, window, timestamp)
            return row.amount if row is not None else 0
