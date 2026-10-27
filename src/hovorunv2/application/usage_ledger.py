"""Bounded replay-safe aggregate accounting contracts without request contents."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Protocol

from hovorunv2.application.usage import UsageUnit, UsageWindow
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import TargetKind

MAX_USAGE_BATCH = 256
MAX_ACCOUNTING_INTEGER = (1 << 63) - 1
MAX_BUCKET_LENGTH = 160
EVENT_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


@dataclass(frozen=True, slots=True)
class AccountingSubject:
    """Stable Telegram subject; lifecycle generations cannot reset current-window usage."""

    kind: TargetKind
    telegram_id: int

    def __post_init__(self) -> None:
        """Reject ambiguous kinds, private group transports and invalid numeric identities."""
        if (
            not isinstance(self.kind, TargetKind)
            or type(self.telegram_id) is not int
            or not 0 < abs(self.telegram_id) <= MAX_ACCOUNTING_INTEGER
            or (self.kind == TargetKind.USER) != (self.telegram_id > 0)
        ):
            msg = "Accounting requires an explicit stable user/group Telegram identity."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class UsageCharge:
    """One successful measured charge, not a reservation or queued request."""

    bucket: str
    amount: int
    unit: UsageUnit = UsageUnit.REQUESTS
    window: UsageWindow = field(default_factory=UsageWindow)

    def __post_init__(self) -> None:
        """Validate bounded integer amounts and typed dimensions before persistence."""
        if type(self.amount) is not int or not 0 < self.amount <= MAX_ACCOUNTING_INTEGER:
            msg = "Accounting amount must be a positive signed 64-bit integer."
            raise ValueError(msg)
        if (
            not self.bucket.startswith(("feature:", "family:"))
            or not self.bucket.partition(":")[2]
            or len(self.bucket) > MAX_BUCKET_LENGTH
        ):
            msg = "Accounting bucket must identify a bounded feature or family."
            raise ValueError(msg)
        if not isinstance(self.unit, UsageUnit) or not isinstance(self.window, UsageWindow):
            msg = "Accounting requires typed units and windows."
            raise TypeError(msg)
        if self.window.seconds > MAX_ACCOUNTING_INTEGER:
            msg = "Accounting window exceeds database integer capacity."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """Idempotent completion/checkpoint event; never one event per token."""

    event_id: str
    subject: AccountingSubject
    occurred_at: int
    charges: tuple[UsageCharge, ...]

    def __post_init__(self) -> None:
        """Require deterministic dimensions and bounded technical event identities."""
        if EVENT_ID_PATTERN.fullmatch(self.event_id) is None or not self.charges or len(self.charges) > MAX_USAGE_BATCH:
            msg = "Accounting requires a bounded technical event ID and nonempty bounded charges."
            raise ValueError(msg)
        if type(self.occurred_at) is not int or not 0 <= self.occurred_at <= MAX_ACCOUNTING_INTEGER:
            msg = "Accounting timestamp must be a nonnegative signed 64-bit integer."
            raise ValueError(msg)
        dimensions = {(charge.bucket, charge.unit, charge.window.seconds) for charge in self.charges}
        if len(dimensions) != len(self.charges):
            msg = "Accounting event must charge each bucket/unit/window at most once."
            raise ValueError(msg)

    @property
    def fingerprint(self) -> str:
        """Detect event-key reuse with different operational charges, independent of tuple order."""
        charges = sorted(
            (charge.bucket, charge.unit.value, charge.window.seconds, charge.amount) for charge in self.charges
        )
        payload = [self.subject.kind.value, self.subject.telegram_id, self.occurred_at, charges]
        return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class AppliedUsage:
    """Safe batch summary used for one transactional audit event."""

    events_applied: int = 0
    aggregates_updated: int = 0
    units_added: int = 0


class UsageLedgerStore(Protocol):
    """Durable aggregates and replay receipts; never executable queued work."""

    async def apply_batch(self, events: tuple[UsageEvent, ...], *, actor: ActorContext) -> AppliedUsage:
        """Commit receipts, aggregate changes and one audit summary atomically."""
        ...

    async def total(
        self,
        subject: AccountingSubject,
        bucket: str,
        unit: UsageUnit,
        window: UsageWindow,
        timestamp: int,
    ) -> int:
        """Read a stable subject's applicable UTC window across internal generations."""
        ...
