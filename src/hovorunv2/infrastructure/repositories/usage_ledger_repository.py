"""Async driver boundary for the shared PostgreSQL accounting repository."""

from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from hovorunv2.application.usage import UsageUnit, UsageWindow
from hovorunv2.application.usage_ledger import AccountingSubject, AppliedUsage, UsageEvent
from hovorunv2.domain.usage_accounting import UsageAggregateDB
from hovorunv2.infrastructure.repositories.usage_ledger import UsageLedgerRepository


class SQLAlchemyUsageLedgerRepository:
    """Reuse receipt claims and atomic upserts without duplicating accounting logic."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def require_known_subject(self, subject: AccountingSubject) -> None:
        """Require a retained current or historical identity."""
        await self._session.run_sync(partial(_require_known_subject, subject=subject))

    async def aggregate(
        self,
        subject: AccountingSubject,
        bucket: str,
        unit: UsageUnit,
        window: UsageWindow,
        timestamp: int,
    ) -> UsageAggregateDB | None:
        """Read one stable accounting window through the async driver."""
        return await self._session.run_sync(
            partial(
                _aggregate,
                subject=subject,
                bucket=bucket,
                unit=unit,
                window=window,
                timestamp=timestamp,
            )
        )

    async def apply(self, events: tuple[UsageEvent, ...]) -> AppliedUsage:
        """Run shared accounting in the caller's audit transaction."""
        return await self._session.run_sync(partial(_apply, events=events))


def _require_known_subject(session: Session, subject: AccountingSubject) -> None:
    UsageLedgerRepository(session).require_known_subject(subject)


def _aggregate(
    session: Session,
    subject: AccountingSubject,
    bucket: str,
    unit: UsageUnit,
    window: UsageWindow,
    timestamp: int,
) -> UsageAggregateDB | None:
    return UsageLedgerRepository(session).aggregate(subject, bucket, unit, window, timestamp)


def _apply(session: Session, events: tuple[UsageEvent, ...]) -> AppliedUsage:
    return UsageLedgerRepository(session).apply(events)
