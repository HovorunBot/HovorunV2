"""Bounded work admission and recoverable, explicitly nontransactional queue handoff."""

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from hovorunv2.application.feature_jobs import JobEnvelope, JobOutcome, SealedPayload
from hovorunv2.application.features import TriggerKind
from hovorunv2.application.usage import UsageSafetyCaps

DEFAULT_INGRESS_PENDING = 10
RECOVERY_PAGE_LIMIT = 100


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
    TERMINAL = "terminal"


class JobStage(StrEnum):
    """Execution stages for multi-stage feature processing."""

    MEDIA = "media"
    BROWSER = "browser"


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

    async def recoverable(self, now: float, limit: int = RECOVERY_PAGE_LIMIT) -> tuple[AdmissionTicket, ...]:
        """Return a bounded page, possibly empty; raise AdmissionUnavailableError on inaccessible or corrupt state."""
        ...
