"""Session-bound deterministic accounting and replay conflict detection."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.application.usage import UsageUnit, UsageWindow
from hovorunv2.application.usage_ledger import (
    MAX_ACCOUNTING_INTEGER,
    AccountingSubject,
    AppliedUsage,
    UsageCharge,
    UsageEvent,
)
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import TargetKind
from hovorunv2.domain.usage_accounting import UsageAccountingEventDB, UsageAggregateDB
from hovorunv2.domain.user import UserDB


class SQLAlchemyUsageLedgerRepository:
    """Account historical known subjects without recreating or authorizing their identities."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def require_known_subject(self, subject: AccountingSubject) -> None:
        """Allow delayed accounting for deleted generations; never fabricate unknown identities."""
        if subject.kind == TargetKind.USER:
            statement = select(UserDB.id).where(
                UserDB.telegram_id == subject.telegram_id, UserDB.platform == "telegram"
            )
        else:
            statement = select(ChatDB.id).where(
                ChatDB.chat_id == subject.telegram_id,
                ChatDB.platform == "telegram",
                ChatDB.chat_type.in_(("group", "supergroup")),
            )
        if await self._session.scalar(statement.limit(1)) is None:
            msg = "Accounting subject must be a known current or historical identity."
            raise ValueError(msg)

    async def aggregate(
        self,
        subject: AccountingSubject,
        bucket: str,
        unit: UsageUnit,
        window: UsageWindow,
        timestamp: int,
    ) -> UsageAggregateDB | None:
        """Use stable Telegram identity, never a lifecycle primary key, for the aggregate key."""
        return await self._session.scalar(
            select(UsageAggregateDB).where(
                UsageAggregateDB.platform == "telegram",
                UsageAggregateDB.subject_kind == subject.kind.value,
                UsageAggregateDB.telegram_id == subject.telegram_id,
                UsageAggregateDB.bucket == bucket,
                UsageAggregateDB.unit == unit.value,
                UsageAggregateDB.window_seconds == window.seconds,
                UsageAggregateDB.window_start == window.start(timestamp),
            )
        )

    async def _charge(self, event: UsageEvent, charge: UsageCharge) -> UsageAggregateDB:
        row = await self.aggregate(event.subject, charge.bucket, charge.unit, charge.window, event.occurred_at)
        if row is None:
            row = UsageAggregateDB(
                subject_kind=event.subject.kind.value,
                telegram_id=event.subject.telegram_id,
                bucket=charge.bucket,
                unit=charge.unit.value,
                window_seconds=charge.window.seconds,
                window_start=charge.window.start(event.occurred_at),
                amount=charge.amount,
            )
            self._session.add(row)
        else:
            if row.amount > MAX_ACCOUNTING_INTEGER - charge.amount:
                msg = "Usage aggregate exceeds database integer capacity."
                raise ValueError(msg)
            row.amount += charge.amount
        await self._session.flush()
        return row

    async def apply(self, events: tuple[UsageEvent, ...]) -> AppliedUsage:
        """Abort the whole batch on a conflicting replay, including earlier aggregate changes."""
        applied, units = 0, 0
        aggregates: set[int] = set()
        for event in events:
            receipt = await self._session.get(UsageAccountingEventDB, event.event_id)
            if receipt is not None:
                if receipt.fingerprint != event.fingerprint:
                    msg = "Accounting event ID was already used for different charges."
                    raise ValueError(msg)
                continue
            await self.require_known_subject(event.subject)
            self._session.add(UsageAccountingEventDB(event_id=event.event_id, fingerprint=event.fingerprint))
            for charge in event.charges:
                row = await self._charge(event, charge)
                aggregates.add(row.id)
                units += charge.amount
            applied += 1
        return AppliedUsage(applied, len(aggregates), units)
