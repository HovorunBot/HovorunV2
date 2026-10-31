"""Async driver boundary for the shared PostgreSQL accounting repository."""

from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from hovorunv2.application.usage.policies import UsageUnit, UsageWindow
from hovorunv2.application.usage.usage_ledger import AccountingSubject, AppliedUsage, UsageEvent
from hovorunv2.domain.usage_accounting import UsageAggregateDB
from hovorunv2.infrastructure.persistence.repositories.usage_ledger import UsageLedgerRepository


class SQLAlchemyUsageLedgerRepository:
    """Reuse receipt claims and atomic upserts without duplicating accounting logic."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def require_known_subject(self, subject: AccountingSubject) -> None:
        """Require a retained current or historical identity.

        Args:
            subject: Accounting subject whose identity must exist.
        """
        await self._session.run_sync(partial(_require_known_subject, subject=subject))

    async def aggregate(
        self,
        subject: AccountingSubject,
        bucket: str,
        unit: UsageUnit,
        window: UsageWindow,
        timestamp: int,
    ) -> UsageAggregateDB | None:
        """Read one stable accounting window through the async driver.

        Args:
            subject: Accounting subject.
            bucket: Accounting bucket.
            unit: Usage unit.
            window: Usage window.
            timestamp: Occurrence timestamp used to compute the window start.

        Returns:
            Matching aggregate row, or None if none exists.
        """
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
        """Run shared accounting in the caller's audit transaction.

        Args:
            events: Usage events to account in event ID order.

        Returns:
            Applied usage summarizing events applied, aggregates touched and units charged.
        """
        return await self._session.run_sync(partial(_apply, events=events))


def _require_known_subject(session: Session, subject: AccountingSubject) -> None:
    """Delegate the subject existence check to the synchronous repository."""
    UsageLedgerRepository(session).require_known_subject(subject)


def _aggregate(
    session: Session,
    subject: AccountingSubject,
    bucket: str,
    unit: UsageUnit,
    window: UsageWindow,
    timestamp: int,
) -> UsageAggregateDB | None:
    """Delegate aggregate lookup to the synchronous repository."""
    return UsageLedgerRepository(session).aggregate(subject, bucket, unit, window, timestamp)


def _apply(session: Session, events: tuple[UsageEvent, ...]) -> AppliedUsage:
    """Delegate batch accounting to the synchronous repository."""
    return UsageLedgerRepository(session).apply(events)
