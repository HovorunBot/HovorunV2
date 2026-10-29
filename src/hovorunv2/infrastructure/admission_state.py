"""Pure admission records, key bindings and lifecycle transitions shared by both runtimes."""

import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from hovorunv2.application.admission import (
    AdmissionConflictError,
    AdmissionTicket,
    AdmissionUnavailableError,
    JobStage,
    TicketState,
)
from hovorunv2.application.budgets import TECHNICAL_ID
from hovorunv2.application.feature_jobs import EnqueueState, JobEnvelope, JobOutcome
from hovorunv2.domain.enums import TargetKind

_ACTIVE_CAPACITY_ERROR: Final = "Active admission capacity metadata is unavailable."
_HANDOFF_CONFLICT_ERROR: Final = "Stage handoff token conflicts with current owner."


def envelope_json(envelope: JobEnvelope) -> str:
    """Serialize immutable operational metadata consistently across runtimes."""
    return json.dumps(envelope.model_dump(mode="json"), separators=(",", ":"))


def admission_ticket_key(prefix: str, job_id: str) -> str:
    """Build the canonical admission ticket key."""
    if TECHNICAL_ID.fullmatch(job_id) is None:
        msg = "Admission requires a technical job identity."
        raise ValueError(msg)
    return prefix + "ticket:" + job_id


@dataclass(frozen=True, slots=True)
class AdmissionKeys:
    """Canonical pointers derived exclusively from the validated immutable envelope."""

    ticket: str
    dedupe: str
    totals: str
    subject: str
    ingress: str
    payload: str
    handoff: str
    pending: str

    @property
    def watched(self) -> tuple[str, ...]:
        """Include every admission key read or mutated by a conditional transition."""
        return (
            self.ticket,
            self.dedupe,
            self.totals,
            self.subject,
            self.ingress,
            self.payload,
            self.handoff,
            self.pending,
        )


def admission_keys(namespace: str, envelope: JobEnvelope) -> AdmissionKeys:
    """Keep actor ingress independent of the charged subject and canonical target."""
    prefix = namespace + "admission:"
    owner = f"{envelope.charged_kind.value}:{envelope.charged_telegram_id}"
    ingress = (
        f"group:{envelope.target.telegram_id}:sender:{envelope.actor_id or 0}"
        if envelope.target.kind == TargetKind.GROUP
        else f"user:{envelope.actor_id}"
    )
    return AdmissionKeys(
        admission_ticket_key(prefix, envelope.job_id),
        prefix + "dedupe:" + envelope.dedupe_key,
        prefix + "totals",
        prefix + "subject:" + owner,
        prefix + "ingress:" + ingress,
        namespace + "payload:" + envelope.payload_ref,
        prefix + "handoff",
        prefix + "pending",
    )


class AdmissionRecord(BaseModel):
    """Validated persisted metadata; private ciphertext never enters this model."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True, allow_inf_nan=False)

    envelope: str
    state: TicketState
    stage: JobStage
    byte_size: int = Field(gt=0, strict=True)
    attempt: int = Field(ge=0, strict=True)
    token: str
    published: int = Field(ge=0, le=1, strict=True)
    publication_registered: int = Field(default=0, ge=0, le=1, strict=True)
    not_before: float = Field(ge=0)
    subject_key: str
    ingress_key: str
    payload_key: str
    dedupe_key: str
    outcome: JobOutcome | None = None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        """Reject uncertain owners, invalid terminal states and malformed original envelopes."""
        job = self.job
        self._validate_schedule(job)
        self._validate_execution_owner()
        self._validate_terminal_outcome()
        return self

    def _validate_schedule(self, job: JobEnvelope) -> None:
        if self.not_before >= job.deadline:
            msg = "Admission schedule exceeds its original deadline."
            raise ValueError(msg)

    def _validate_execution_owner(self) -> None:
        if self.state in {TicketState.RUNNING, TicketState.CANCELLING}:
            if TECHNICAL_ID.fullmatch(self.token) is None or self.attempt == 0:
                msg = "Active admission requires an execution owner and attempt."
                raise ValueError(msg)
            return
        if self.state in {TicketState.ACCEPTED, TicketState.ENQUEUED} and self.token:
            msg = "Queued admission cannot retain an execution owner."
            raise ValueError(msg)

    def _validate_terminal_outcome(self) -> None:
        if (self.state == TicketState.TERMINAL) != (self.outcome is not None):
            msg = "Admission terminal state requires exactly one safe outcome."
            raise ValueError(msg)

    @property
    def job(self) -> JobEnvelope:
        """Validate the embedded immutable envelope before using operational pointers."""
        return JobEnvelope.model_validate_json(self.envelope)

    @property
    def ticket(self) -> AdmissionTicket:
        """Expose only the application's typed operational ticket."""
        return AdmissionTicket(
            self.job, self.state, self.byte_size, self.stage, self.token or None, self.attempt, self.outcome
        )

    def encode(self) -> str:
        """Store JSON metadata using the same shape as existing admission tickets."""
        return json.dumps(self.model_dump(mode="json", exclude_none=True), separators=(",", ":"))

    def keys(self, namespace: str) -> AdmissionKeys:
        """Reject arbitrary or stale persisted pointers before touching any referenced key."""
        keys = admission_keys(namespace, self.job)
        if (self.subject_key, self.ingress_key, self.payload_key, self.dedupe_key) != (
            keys.subject,
            keys.ingress,
            keys.payload,
            keys.dedupe,
        ):
            msg = "Admission pointers disagree with its immutable envelope."
            raise AdmissionUnavailableError(msg)
        return keys

    def require_active_indexes(self, pointer: str, score: object, handoff_score: object) -> None:
        """Require dedupe and pending indexes bound to this record's identity and deadline."""
        job = self.job
        if pointer != job.job_id:
            raise AdmissionUnavailableError(_ACTIVE_CAPACITY_ERROR)
        if score != job.deadline:
            raise AdmissionUnavailableError(_ACTIVE_CAPACITY_ERROR)
        if self.state == TicketState.ACCEPTED and handoff_score != job.deadline:
            raise AdmissionUnavailableError(_ACTIVE_CAPACITY_ERROR)


def read_record(raw: object) -> AdmissionRecord:
    """Convert malformed stored metadata to an allowlisted failure without exposing its contents."""
    try:
        if isinstance(raw, dict):
            return AdmissionRecord.model_validate(raw)
        if isinstance(raw, str | bytes):
            return AdmissionRecord.model_validate_json(raw)
    except ValidationError, TypeError, ValueError:
        msg = "Admission ticket metadata is unavailable."
        raise AdmissionUnavailableError(msg) from None
    msg = "Admission ticket metadata is unavailable."
    raise AdmissionUnavailableError(msg)


def validate_claim(now: float, token: str) -> None:
    """Require a finite clock and a nonempty operational owner token before claiming."""
    if not math.isfinite(now) or now < 0 or TECHNICAL_ID.fullmatch(token) is None:
        msg = "Stage claims require finite time and a technical owner token."
        raise ValueError(msg)


def read_string(raw: object) -> str:
    """Reject undecodable or non-string Redis metadata without propagating stored contents."""
    try:
        if isinstance(raw, bytes):
            return raw.decode()
        if isinstance(raw, str):
            return raw
    except UnicodeError:
        pass
    msg = "Admission string metadata is unavailable."
    raise AdmissionUnavailableError(msg)


def read_counter(raw: object, *, missing_zero: bool = False) -> int:
    """Never turn malformed or missing active counters into new capacity."""
    if raw is None and missing_zero:
        return 0
    if not isinstance(raw, str | bytes | int) or isinstance(raw, bool):
        msg = "Admission capacity metadata is unavailable."
        raise AdmissionUnavailableError(msg)
    try:
        value = int(raw)
    except ValueError:
        msg = "Admission capacity metadata is unavailable."
        raise AdmissionUnavailableError(msg) from None
    if value < 0:
        msg = "Admission capacity metadata is unavailable."
        raise AdmissionUnavailableError(msg)
    return value


@dataclass(frozen=True, slots=True)
class AdmissionCapacity:
    """A watched capacity snapshot bound to the complete pending index."""

    count: int
    byte_size: int
    subject: int
    ingress: int

    def require_active(self, record: AdmissionRecord, dedupe: object, score: object, handoff_score: object) -> None:
        """Do not release or execute a ticket whose supporting metadata disappeared."""
        pointer = read_string(dedupe)
        self._require_counts(record.byte_size)
        record.require_active_indexes(pointer, score, handoff_score)

    def _require_counts(self, byte_size: int) -> None:
        if self.count < 1 or self.byte_size < byte_size:
            raise AdmissionUnavailableError(_ACTIVE_CAPACITY_ERROR)
        if self.subject < 1 or self.ingress < 1:
            raise AdmissionUnavailableError(_ACTIVE_CAPACITY_ERROR)


def _read_totals(totals: object) -> tuple[int, int]:
    if not isinstance(totals, dict):
        msg = "Admission totals are unavailable."
        raise AdmissionUnavailableError(msg)
    normalized = {(key.decode() if isinstance(key, bytes) else key): value for key, value in totals.items()}
    if normalized and set(normalized) != {"count", "bytes"}:
        msg = "Admission totals are unavailable."
        raise AdmissionUnavailableError(msg)
    count = read_counter(normalized.get("count"), missing_zero=not normalized)
    byte_size = read_counter(normalized.get("bytes"), missing_zero=not normalized)
    return count, byte_size


def read_capacity(totals: object, subject: object, ingress: object, pending: object) -> AdmissionCapacity:
    """Allow a new empty namespace, but never reset a partially missing active ledger."""
    count, byte_size = _read_totals(totals)
    subject_count = read_counter(subject, missing_zero=True)
    ingress_count = read_counter(ingress, missing_zero=True)
    if count != read_counter(pending) or (count == 0) != (byte_size == 0) or max(subject_count, ingress_count) > count:
        msg = "Admission totals disagree with pending capacity."
        raise AdmissionUnavailableError(msg)
    return AdmissionCapacity(count, byte_size, subject_count, ingress_count)


class AdmissionOperation(StrEnum):
    """The closed set of lifecycle transitions implemented by admission transactions."""

    ENQUEUED = "enqueued"
    CLAIM = "claim"
    HANDOFF = "handoff"
    CANCEL = "cancel"
    FINISH = "finish"
    REGISTER = "register"
    PUBLISHED = "published"


@dataclass(frozen=True, slots=True)
class AdmissionTransition:
    """A scoped lifecycle request; every external effect remains outside this calculation."""

    operation: AdmissionOperation
    stage: JobStage = JobStage.MEDIA
    attempt: int = 0
    token: str = ""
    outcome: JobOutcome | None = None
    now: float = 0
    expected_envelope: JobEnvelope | None = None
    not_before: float = 0


@dataclass(frozen=True, slots=True)
class PublicationDecision:
    """Publication result and original schedule, independent of a broker acknowledgement."""

    state: EnqueueState
    ticket: AdmissionTicket | None = None
    not_before: float = 0
    should_publish: bool = False


@dataclass(frozen=True, slots=True)
class AdmissionChange:
    """Pure transition result, including atomic capacity/index effects required by persistence."""

    record: AdmissionRecord | None
    write: bool = False
    release: bool = False
    handoff: bool | None = None
    success: bool = False
    publication_state: EnqueueState = EnqueueState.EXISTS
    should_publish: bool = False


def _finish(record: AdmissionRecord, request: AdmissionTransition) -> AdmissionChange:
    if not isinstance(request.outcome, JobOutcome):
        msg = "Admission completion requires a safe typed outcome."
        raise TypeError(msg)
    if record.state == TicketState.TERMINAL:
        return _replay_finish(record, request)
    if not _completion_owner_matches(record, request.token):
        return AdmissionChange(None)
    terminal = record.model_copy(update={"state": TicketState.TERMINAL, "outcome": request.outcome})
    return AdmissionChange(terminal, write=True, release=True, handoff=False, success=True)


def _replay_finish(record: AdmissionRecord, request: AdmissionTransition) -> AdmissionChange:
    if request.token and request.token != record.token:
        return AdmissionChange(None)
    if record.outcome != request.outcome:
        msg = "Admission terminal state conflicts with this transition."
        raise AdmissionConflictError(msg)
    return AdmissionChange(record, success=True)


def _completion_owner_matches(record: AdmissionRecord, token: str) -> bool:
    if record.state in {TicketState.RUNNING, TicketState.CANCELLING}:
        return token == record.token
    return not token


def _claim(record: AdmissionRecord, request: AdmissionTransition) -> AdmissionChange:
    if record.state not in {TicketState.ACCEPTED, TicketState.ENQUEUED}:
        return AdmissionChange(None)
    if request.expected_envelope is not None and record.job != request.expected_envelope:
        return AdmissionChange(None)
    if request.now >= record.job.deadline:
        terminal = record.model_copy(update={"state": TicketState.TERMINAL, "outcome": JobOutcome.EXPIRED})
        return AdmissionChange(terminal, write=True, release=True, handoff=False)
    if request.now < record.not_before or record.stage != request.stage:
        return AdmissionChange(None)
    running = record.model_copy(
        update={"state": TicketState.RUNNING, "token": request.token, "attempt": record.attempt + 1}
    )
    return AdmissionChange(running, write=True, handoff=False, success=True)


def _handoff(record: AdmissionRecord, request: AdmissionTransition) -> AdmissionChange:
    if record.state != TicketState.RUNNING:
        return AdmissionChange(None)
    _require_stage_handoff(record, request)
    ready = record.model_copy(
        update={
            "state": TicketState.ACCEPTED,
            "stage": request.stage,
            "token": "",
            "published": 0,
            "publication_registered": 0,
            "not_before": 0,
        }
    )
    return AdmissionChange(ready, write=True, handoff=True, success=True)


def _require_stage_handoff(record: AdmissionRecord, request: AdmissionTransition) -> None:
    if record.token != request.token:
        raise ValueError(_HANDOFF_CONFLICT_ERROR)
    if record.stage != JobStage.MEDIA or request.stage != JobStage.BROWSER:
        raise ValueError(_HANDOFF_CONFLICT_ERROR)


def _register(record: AdmissionRecord, request: AdmissionTransition) -> AdmissionChange:
    if record.job != request.expected_envelope:
        msg = "Feature job ID conflicts with another envelope."
        raise ValueError(msg)
    if record.published == 1 or record.state != TicketState.ACCEPTED:
        return AdmissionChange(record)
    if record.publication_registered:
        return AdmissionChange(record, should_publish=True)
    registered = record.model_copy(update={"not_before": request.not_before, "publication_registered": 1})
    return AdmissionChange(registered, write=True, publication_state=EnqueueState.ENQUEUED, should_publish=True)


def _cancel(record: AdmissionRecord) -> AdmissionChange:
    if record.state == TicketState.RUNNING:
        return AdmissionChange(record.model_copy(update={"state": TicketState.CANCELLING}), write=True, success=True)
    if record.state in {TicketState.ACCEPTED, TicketState.ENQUEUED}:
        terminal = record.model_copy(update={"state": TicketState.TERMINAL, "outcome": JobOutcome.CANCELLED})
        return AdmissionChange(terminal, write=True, release=True, handoff=False, success=True)
    return AdmissionChange(record)


def _acknowledge(record: AdmissionRecord, request: AdmissionTransition) -> AdmissionChange:
    match request.operation:
        case AdmissionOperation.ENQUEUED:
            if record.state == TicketState.ACCEPTED and (record.stage, record.attempt) == (
                request.stage,
                request.attempt,
            ):
                return AdmissionChange(
                    record.model_copy(update={"state": TicketState.ENQUEUED}), write=True, handoff=False
                )
        case AdmissionOperation.PUBLISHED:
            if record.state != TicketState.TERMINAL and (record.stage, record.attempt) == (
                request.stage,
                request.attempt,
            ):
                return AdmissionChange(record.model_copy(update={"published": 1}), write=True, success=True)
    return AdmissionChange(record)


def calculate_change(record: AdmissionRecord, request: AdmissionTransition) -> AdmissionChange:
    """Apply stage, attempt, owner and terminal fences without doing I/O."""
    match request.operation:
        case AdmissionOperation.CLAIM:
            return _claim(record, request)
        case AdmissionOperation.HANDOFF:
            return _handoff(record, request)
        case AdmissionOperation.FINISH:
            return _finish(record, request)
        case AdmissionOperation.REGISTER:
            return _register(record, request)
        case AdmissionOperation.CANCEL:
            return _cancel(record)
        case _:
            return _acknowledge(record, request)
