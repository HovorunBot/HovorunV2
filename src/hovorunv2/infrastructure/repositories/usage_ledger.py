"""Session-bound deterministic accounting and replay conflict detection."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

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


class UsageLedgerRepository:
    """Account historical known subjects without recreating or authorizing their identities."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def require_known_subject(self, subject: AccountingSubject) -> None:
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
        if self._session.scalar(statement.limit(1)) is None:
            msg = "Accounting subject must be a known current or historical identity."
            raise ValueError(msg)

    def aggregate(
        self,
        subject: AccountingSubject,
        bucket: str,
        unit: UsageUnit,
        window: UsageWindow,
        timestamp: int,
    ) -> UsageAggregateDB | None:
        """Use stable Telegram identity, never a lifecycle primary key, for the aggregate key."""
        return self._session.scalar(
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

    def _charge(self, event: UsageEvent, charge: UsageCharge) -> int:
        statement = insert(UsageAggregateDB).values(
            subject_kind=event.subject.kind.value,
            telegram_id=event.subject.telegram_id,
            bucket=charge.bucket,
            unit=charge.unit.value,
            window_seconds=charge.window.seconds,
            window_start=charge.window.start(event.occurred_at),
            amount=charge.amount,
        )
        row_id = self._session.scalar(
            statement.on_conflict_do_update(
                constraint="uq_usage_aggregate_window",
                set_={"amount": UsageAggregateDB.amount + statement.excluded.amount},
                where=UsageAggregateDB.amount <= MAX_ACCOUNTING_INTEGER - statement.excluded.amount,
            ).returning(UsageAggregateDB.id)
        )
        if row_id is None:
            msg = "Usage aggregate exceeds database integer capacity."
            raise ValueError(msg)
        return row_id

    def apply(self, events: tuple[UsageEvent, ...]) -> AppliedUsage:
        """Abort the whole batch on a conflicting replay, including earlier aggregate changes."""
        applied, units = 0, 0
        aggregates: set[int] = set()
        for event in sorted(events, key=lambda item: item.event_id):
            inserted = self._session.scalar(
                insert(UsageAccountingEventDB)
                .values(event_id=event.event_id, fingerprint=event.fingerprint)
                .on_conflict_do_nothing()
                .returning(UsageAccountingEventDB.event_id)
            )
            if inserted is None:
                receipt = self._session.get(UsageAccountingEventDB, event.event_id)
                if receipt is None:
                    msg = "Accounting receipt is unavailable after conflicting insertion."
                    raise ValueError(msg)
                if receipt.fingerprint != event.fingerprint:
                    msg = "Accounting event ID was already used for different charges."
                    raise ValueError(msg)
                continue
            self.require_known_subject(event.subject)
            for charge in event.charges:
                aggregates.add(self._charge(event, charge))
                units += charge.amount
            applied += 1
        return AppliedUsage(applied, len(aggregates), units)
