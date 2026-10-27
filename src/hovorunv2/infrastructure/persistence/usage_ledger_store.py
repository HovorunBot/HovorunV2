"""Serialized accounting transactions with replay-safe receipts and one batch audit."""

import hashlib
from functools import partial

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.features import FeatureRegistry
from hovorunv2.application.ports import AuditedUnitOfWork
from hovorunv2.application.usage import UsageUnit, UsageWindow
from hovorunv2.application.usage_ledger import (
    MAX_USAGE_BATCH,
    AccountingSubject,
    AppliedUsage,
    UsageEvent,
    UsageLedgerStore,
)
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ActorOrigin
from hovorunv2.infrastructure.repositories.usage_ledger_repository import SQLAlchemyUsageLedgerRepository


class SQLAlchemyUsageLedgerStore(UsageLedgerStore):
    """Retain accounting independently of entity recovery and runtime queue state."""

    def __init__(
        self, session_maker: async_sessionmaker, audited_uow: AuditedUnitOfWork, registry: FeatureRegistry
    ) -> None:
        self._session_maker = session_maker
        self._audited_uow = audited_uow
        self._registry = registry

    def _validate(self, events: tuple[UsageEvent, ...], actor: ActorContext) -> None:
        if actor.origin not in {ActorOrigin.SYSTEM, ActorOrigin.CLI}:
            msg = "Accounting mutations require trusted system or CLI origin."
            raise PermissionError(msg)
        if len(events) > MAX_USAGE_BATCH:
            msg = "Accounting batch exceeds its bound."
            raise ValueError(msg)
        for event in events:
            for charge in event.charges:
                scope, _, key = charge.bucket.partition(":")
                definitions = (
                    (self._registry.require(key),)
                    if scope == "feature"
                    else tuple(definition for definition in self._registry.all() if definition.usage_policy == key)
                )
                if not definitions or not any(charge.unit in definition.units for definition in definitions):
                    msg = "Accounting bucket or unit is unsupported by the feature registry."
                    raise ValueError(msg)

    async def _apply(self, session: AsyncSession, events: tuple[UsageEvent, ...]) -> AppliedUsage:
        await session.execute(text("BEGIN IMMEDIATE"))
        return await SQLAlchemyUsageLedgerRepository(session).apply(events)

    async def apply_batch(self, events: tuple[UsageEvent, ...], *, actor: ActorContext) -> AppliedUsage:
        """Commit receipts, counters and one payload-free audit summary or roll back all three."""
        self._validate(events, actor)
        batch_id = hashlib.sha256("\n".join(event.event_id for event in events).encode()).hexdigest()
        return await self._audited_uow.execute(
            actor=actor,
            target_type="usage_batch",
            target_id=batch_id,
            action="apply",
            operation=partial(self._apply, events=events),
            changed_fields=lambda result: {
                "events_applied": result.events_applied,
                "aggregates_updated": result.aggregates_updated,
                "units_added": result.units_added,
            },
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
