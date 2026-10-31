"""Bounded work admission and recoverable, explicitly nontransactional queue handoff."""

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, model_validator

from hovorunv2.application.budgets import UnitAmount
from hovorunv2.application.feature_jobs import JobEnvelope, JobOutcome, SealedPayload
from hovorunv2.application.features import TriggerKind
from hovorunv2.application.usage import UsageSafetyCaps
from hovorunv2.application.usage_ledger import UsageEvent

DEFAULT_INGRESS_PENDING = 10
RECOVERY_PAGE_LIMIT = 100
RECOVERY_RETRY_SECONDS = 5


class AdmissionUnavailableError(ValueError):
    """Required admission state cannot be read, verified or updated safely."""


class AdmissionConflictError(ValueError):
    """A terminal completion conflicts with the recorded outcome."""


def incoming_action_key(feature: str, chat_id: int, actor_id: int | None, message_id: int, trigger: TriggerKind) -> str:
    """Hash stable incoming action metadata, never URL/message contents or current processing time."""
    identity = ["telegram", chat_id, actor_id or 0, message_id, trigger.value, feature]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


class TicketState(StrEnum):
    """Accepted work survives a crash before or after enqueue confirmation."""

    ACCEPTED = "accepted"
    ENQUEUED = "enqueued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    WAITING = "waiting"
    SETTLING = "settling"
    DELIVERY_UNCERTAIN = "delivery_uncertain"
    TERMINAL = "terminal"


class NoticeKind(StrEnum):
    """At most one delayed or terminal feedback dispatch claim per admission ticket."""

    DELAYED = "delayed"
    TERMINAL = "terminal"


class JobStage(StrEnum):
    """Execution stages for multi-stage feature processing."""

    MEDIA = "media"
    BROWSER = "browser"


class SettlementIntent(BaseModel):
    """Immutable proof of stopped execution and replayable consumption, without message contents."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    outcome: JobOutcome
    actual: tuple[UnitAmount, ...] = ()
    event: UsageEvent | None = None

    @model_validator(mode="after")
    def validate_consumption(self) -> SettlementIntent:
        """Bind successful consumption to a durable receipt and exact runtime amounts."""
        if len({item.unit for item in self.actual}) != len(self.actual):
            msg = "Settlement units must be distinct."
            raise ValueError(msg)
        consumed = any(item.amount for item in self.actual)
        if consumed != (self.event is not None):
            msg = "Successful consumption requires a durable receipt."
            raise ValueError(msg)
        if self.event is not None and self.outcome != JobOutcome.COMPLETE:
            msg = "Consumption requires confirmed delivery."
            raise ValueError(msg)
        if self.event is not None:
            amounts = {item.unit: item.amount for item in self.actual}
            if any(charge.amount != amounts.get(charge.unit) for charge in self.event.charges):
                msg = "Receipt charges must match runtime consumption."
                raise ValueError(msg)
        return self


@dataclass(frozen=True, slots=True)
class AdmissionTicket:
    """Minimal operational ticket; no duplicate ciphertext blob or executable payload."""

    envelope: JobEnvelope
    state: TicketState
    byte_size: int
    stage: JobStage = JobStage.MEDIA
    token: str | None = None
    attempt: int = 0
    outcome: JobOutcome | None = None
    not_before: float = 0
    settlement: SettlementIntent | None = None
    budget_reserved: bool = False
    delayed_notice_requested: bool = False
    terminal_notice_requested: bool = False


class AdmissionState(StrEnum):
    """Overflow affects only new work; duplicates return their original ticket."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    ENQUEUED = "enqueued"
    FULL = "full"
    EXPIRED = "expired"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    """Safe state and original immutable metadata, never a framework job object."""

    state: AdmissionState
    ticket: AdmissionTicket | None = None


@dataclass(frozen=True, slots=True)
class AdmissionLimits:
    """Subject/global/ingress counts and independent ciphertext byte ceilings."""

    subject_pending: int
    ingress_pending: int
    safety: UsageSafetyCaps

    def __post_init__(self) -> None:
        """Zero or negative limits never mean unlimited admission."""
        if (
            type(self.subject_pending) is not int
            or type(self.ingress_pending) is not int
            or min(self.subject_pending, self.ingress_pending) <= 0
        ):
            msg = "Admission count bounds must be positive integers."
            raise ValueError(msg)


class AdmissionPort(Protocol):
    """Runtime tickets, capacity and ciphertext share one Valkey admission transaction."""

    async def admit(
        self, envelope: JobEnvelope, payload: SealedPayload, limits: AdmissionLimits, now: float
    ) -> AdmissionResult:
        """Deduplicate and acquire every count/byte dimension before accepting work."""
        ...

    async def get(self, job_id: str) -> AdmissionTicket | None:
        """Return None only for a missing ticket; raise AdmissionUnavailableError for unusable state."""
        ...

    async def mark_enqueued(self, job_id: str, stage: JobStage, attempt: int) -> AdmissionTicket | None:
        """CAS accepted to enqueued; stale stage/attempt retains current metadata, failure raises."""
        ...

    async def claim_stage(self, job_id: str, stage: JobStage, now: float, token: str) -> AdmissionTicket | None:
        """Return None for missing work or rejected ownership; raise AdmissionUnavailableError on runtime failure."""
        ...

    async def handoff_stage(self, job_id: str, token: str, next_stage: JobStage) -> AdmissionTicket | None:
        """Hand off the running stage without releasing capacity or renewing its deadline."""
        ...

    async def cancel(self, job_id: str) -> bool:
        """Tombstone queued work atomically; running work transitions to cancelling."""
        ...

    async def finish(self, job_id: str, outcome: JobOutcome, *, token: str | None = None) -> AdmissionTicket | None:
        """Release once; missing/stale ownership returns None, conflicts or unavailable state raise."""
        ...

    async def claim_notice(self, job_id: str, kind: NoticeKind) -> bool:
        """Claim feedback once before sending; never retain rendered message contents."""
        ...

    async def recoverable(self, now: float, limit: int = RECOVERY_PAGE_LIMIT) -> tuple[AdmissionTicket, ...]:
        """Return a bounded page, possibly empty; raise AdmissionUnavailableError on inaccessible or corrupt state."""
        ...


class SyncAdmissionPort(Protocol):
    """Synchronous worker admission lifecycle and stage claim contract."""

    def get(self, job_id: str) -> AdmissionTicket | None:
        """Read validated ticket metadata only within the owning child context."""
        ...

    def claim_stage(
        self, job_id: str, stage: JobStage, now: float, token: str, *, expected_envelope: JobEnvelope | None = None
    ) -> AdmissionTicket | None:
        """Claim exclusively with exact envelope checking before any private access."""
        ...

    def handoff_stage(self, job_id: str, token: str, next_stage: JobStage) -> AdmissionTicket | None:
        """Hand off without a second ciphertext, new deadline or admission refund."""
        ...

    def finish(self, job_id: str, outcome: JobOutcome, *, token: str | None = None) -> AdmissionTicket | None:
        """Settle once using the current owner's token and the shared terminal rules."""
        ...

    def claim_notice(self, job_id: str, kind: NoticeKind) -> bool:
        """Claim feedback once without retrying uncertain external acceptance."""
        ...

    def mark_reserved(self, job_id: str, token: str) -> AdmissionTicket | None:
        """Bind an acknowledged reservation to the owner before any stage handoff."""
        ...

    def defer(self, job_id: str, token: str, not_before: float) -> AdmissionTicket | None:
        """Release a quota-denied execution claim while retaining admission and original timestamps."""
        ...

    def begin_delivery(self, job_id: str, token: str, now: float) -> AdmissionTicket | None:
        """Fence cancellation and expiry before marking external delivery potentially accepted."""
        ...

    def prepare_settlement(self, job_id: str, token: str, intent: SettlementIntent) -> AdmissionTicket | None:
        """Record verified stopped effects before touching runtime or durable consumption."""
        ...

    def allow_republication(self, ticket: AdmissionTicket, now: float) -> bool:
        """Rate bound uncertain publication retries using the current stage and attempt."""
        ...

    def recovery_page(self, limit: int = RECOVERY_PAGE_LIMIT) -> tuple[AdmissionTicket, ...]:
        """Rotate bounded subject/provider lanes without reclaiming active execution."""
        ...

    def cancel(self, job_id: str) -> bool:
        """Retain running ownership until execution effects are verified terminal."""
        ...
