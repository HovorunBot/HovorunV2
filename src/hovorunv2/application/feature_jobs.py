"""Payload-free feature queue contracts and bounded encrypted payload references."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hovorunv2.application.features import TriggerKind
from hovorunv2.application.usage import MINUTE_SECONDS
from hovorunv2.domain.enums import TargetKind

MAX_JOB_LIFETIME_SECONDS = 5 * MINUTE_SECONDS
PAYLOAD_CLEANUP_SECONDS = 60
FEATURE_JOB_FUNCTION = "run_feature_job"
_INVALID_JOB_TARGET_MESSAGE: Final = "Job target kind and Telegram identity disagree."
_INVALID_JOB_DEADLINE_MESSAGE: Final = "Feature job requires an original deadline within five minutes of acceptance."
_INVALID_JOB_BINDING_MESSAGE: Final = "Job trigger, target and charged subject disagree."


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
        if not 0 < abs(self.telegram_id) < (1 << 63):
            raise ValueError(_INVALID_JOB_TARGET_MESSAGE)
        if (self.kind == TargetKind.USER) != (self.telegram_id > 0):
            raise ValueError(_INVALID_JOB_TARGET_MESSAGE)
        return self


class JobEnvelope(BaseModel):
    """Serialized operational metadata only; sensitive input lives at payload_ref."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True, allow_inf_nan=False)

    job_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,96}$")
    payload_ref: str = Field(pattern=r"^[a-f0-9]{32}$")
    feature: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,96}$")
    target: JobTarget
    actor_id: int | None = Field(default=None, gt=0, lt=1 << 63)
    actor_generation: int | None = Field(default=None, gt=0)
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
        self._validate_deadline()
        if self.trigger == TriggerKind.AUTOMATIC:
            self._validate_automatic_binding()
            return self
        actor_id = self._require_direct_actor()
        self._validate_direct_charge(actor_id)
        self._validate_private_target(actor_id)
        return self

    def _validate_deadline(self) -> None:
        """Require the original execution window without renewing acceptance time."""
        if self.deadline <= self.accepted_at or self.deadline - self.accepted_at > MAX_JOB_LIFETIME_SECONDS:
            raise ValueError(_INVALID_JOB_DEADLINE_MESSAGE)

    def _validate_automatic_binding(self) -> None:
        """Charge only the canonical group and retain complete or absent actor metadata."""
        if self.target.kind != TargetKind.GROUP:
            raise ValueError(_INVALID_JOB_BINDING_MESSAGE)
        if self.charged_kind != TargetKind.GROUP or self.charged_telegram_id != self.target.telegram_id:
            raise ValueError(_INVALID_JOB_BINDING_MESSAGE)
        if (self.actor_id is None) != (self.actor_generation is None):
            raise ValueError(_INVALID_JOB_BINDING_MESSAGE)

    def _require_direct_actor(self) -> int:
        """Establish the required current actor identity for a direct request."""
        actor_id = self.actor_id
        if actor_id is None or not actor_id > 0:
            raise ValueError(_INVALID_JOB_BINDING_MESSAGE)
        generation = self.actor_generation
        if generation is None or not generation > 0:
            raise ValueError(_INVALID_JOB_BINDING_MESSAGE)
        return actor_id

    def _validate_direct_charge(self, actor_id: int) -> None:
        """Bind personal charging to the established direct actor."""
        if self.charged_kind != TargetKind.USER or self.charged_telegram_id != actor_id:
            raise ValueError(_INVALID_JOB_BINDING_MESSAGE)

    def _validate_private_target(self, actor_id: int) -> None:
        """Prevent private delivery from selecting another user's identity."""
        if self.target.kind == TargetKind.USER and self.target.telegram_id != actor_id:
            raise ValueError(_INVALID_JOB_BINDING_MESSAGE)


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
    """Private in-process bytes; never a queue result or envelope field."""

    state: PayloadState
    data: bytes | None = None
    sender_label: str | None = None


class PayloadPort(Protocol):
    """Encrypt before admission; store/read opaque payloads with bounded retention."""

    async def seal(
        self, job_id: str, data: bytes, deadline: float, *, sender_label: str | None = None
    ) -> SealedPayload:
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


class JobStatus(StrEnum):
    """Application-owned execution facts, independent of broker acknowledgement or result states."""

    PENDING = "pending"
    RUNNING = "running"
    CANCELLING = "cancelling"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class QueuedFeature:
    """Minimal inspection result without backend objects or sensitive error strings."""

    envelope: JobEnvelope
    status: JobStatus
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
