"""Payload-free feature queue contracts and bounded encrypted payload references."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hovorunv2.application.features import TriggerKind
from hovorunv2.application.usage import MINUTE_SECONDS
from hovorunv2.domain.enums import TargetKind

MAX_JOB_LIFETIME_SECONDS = 5 * MINUTE_SECONDS
PAYLOAD_CLEANUP_SECONDS = 60
FEATURE_JOB_FUNCTION = "run_feature_job"


class JobTarget(BaseModel):
    """Canonical delivery identity independent of the human actor and charged subject."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    kind: TargetKind
    entity_id: int = Field(gt=0)
    generation: int = Field(gt=0)
    telegram_id: int

    @model_validator(mode="after")
    def validate_identity(self) -> JobTarget:
        """Reject ambiguous Telegram target kinds and invalid stable identities."""
        if not 0 < abs(self.telegram_id) < (1 << 63) or (self.kind == TargetKind.USER) != (self.telegram_id > 0):
            msg = "Job target kind and Telegram identity disagree."
            raise ValueError(msg)
        return self


class JobEnvelope(BaseModel):
    """Serialized operational metadata only; sensitive input lives at payload_ref."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True, allow_inf_nan=False)

    job_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,96}$")
    payload_ref: str = Field(pattern=r"^[a-f0-9]{32}$")
    feature: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,96}$")
    target: JobTarget
    actor_id: int = Field(gt=0, lt=1 << 63)
    actor_generation: int = Field(gt=0)
    trigger: TriggerKind
    charged_kind: TargetKind
    charged_telegram_id: int
    accepted_at: float = Field(ge=0)
    deadline: float = Field(gt=0)
    dedupe_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    feature_epoch: int = Field(ge=0)
    message_id: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_binding(self) -> JobEnvelope:
        """Keep charging, original deadline and canonical transport facts consistent."""
        if self.deadline <= self.accepted_at or self.deadline - self.accepted_at > MAX_JOB_LIFETIME_SECONDS:
            msg = "Feature job requires an original deadline within five minutes of acceptance."
            raise ValueError(msg)
        if self.trigger == TriggerKind.AUTOMATIC:
            valid = (
                self.target.kind == TargetKind.GROUP
                and self.charged_kind == TargetKind.GROUP
                and self.charged_telegram_id == self.target.telegram_id
            )
        else:
            valid = self.charged_kind == TargetKind.USER and self.charged_telegram_id == self.actor_id
        if self.target.kind == TargetKind.USER:
            valid = valid and self.target.telegram_id == self.actor_id
        if not valid:
            msg = "Job trigger, target and charged subject disagree."
            raise ValueError(msg)
        return self


class JobOutcome(StrEnum):
    """Only safe terminal outcome codes may enter queue results or errors."""

    COMPLETE = "complete"
    EXPIRED = "expired"
    PAYLOAD_UNAVAILABLE = "payload_unavailable"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PayloadState(StrEnum):
    """Do not expose decryption or backend exception details."""

    READY = "ready"
    MISSING = "missing"
    UNREADABLE = "unreadable"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class SealedPayload:
    """Prepared ciphertext; byte_size measures the exact stored blob, not plaintext length."""

    reference: str
    job_id: str
    deadline: float
    blob: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class LoadedPayload:
    """Private in-process bytes; never a SAQ result or envelope field."""

    state: PayloadState
    data: bytes | None = None


class PayloadPort(Protocol):
    """Encrypt before admission; store/read opaque payloads with bounded retention."""

    async def seal(self, job_id: str, data: bytes, deadline: float) -> SealedPayload:
        """Prepare ciphertext without persisting a plaintext or uncounted payload."""
        ...

    async def put(self, payload: SealedPayload) -> PayloadState:
        """Store once without extending the original deadline on replay."""
        ...

    async def load(self, reference: str, job_id: str) -> LoadedPayload:
        """Return safe unavailable state when payload is missing, expired or undecryptable."""
        ...

    async def delete(self, reference: str) -> None:
        """Remove the exact opaque payload reference during terminal cleanup."""
        ...


class EnqueueState(StrEnum):
    """Typed handoff results; uncertain backend outcomes require idempotent retry."""

    ENQUEUED = "enqueued"
    EXISTS = "exists"
    UNAVAILABLE = "unavailable"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class QueuedFeature:
    """Minimal inspection result without SAQ objects or sensitive error strings."""

    envelope: JobEnvelope
    status: str
    outcome: JobOutcome | None


class FeatureQueuePort(Protocol):
    """Separate feature work from operational notifications and maintenance."""

    async def enqueue(self, envelope: JobEnvelope, *, not_before: float = 0) -> EnqueueState:
        """Schedule idempotently by stable job ID without renewing the original deadline."""
        ...

    async def inspect(self, job_id: str) -> QueuedFeature | None:
        """Expose only typed metadata and safe result codes."""
        ...

    async def cancel(self, job_id: str) -> bool:
        """Cancel one job using a fixed safe operational reason."""
        ...


class FeatureExecutor(Protocol):
    """Later worker integration consumes private bytes only in process."""

    async def execute(self, envelope: JobEnvelope, payload: bytes) -> JobOutcome:
        """Return a safe code; implementation must revalidate execution and delivery authority."""
        ...
