"""Valkey-owned admission with bounded optimistic transactions and shared pure transitions."""

import math
from collections.abc import Callable
from dataclasses import replace

import redis
from redis.asyncio.client import Pipeline as AsyncPipeline
from redis.client import Pipeline
from redis.exceptions import RedisError, WatchError

from hovorunv2.application.features.emergency import resolve_emergency_key
from hovorunv2.application.jobs.admission import (
    RECOVERY_PAGE_LIMIT,
    AdmissionLimits,
    AdmissionPort,
    AdmissionResult,
    AdmissionState,
    AdmissionTicket,
    AdmissionUnavailableError,
    JobStage,
    NoticeKind,
    SettlementIntent,
    TicketState,
)
from hovorunv2.application.jobs.feature_jobs import (
    PAYLOAD_CLEANUP_SECONDS,
    EnqueueState,
    JobEnvelope,
    JobOutcome,
    SealedPayload,
)
from hovorunv2.application.usage.budgets import RUNTIME_RETENTION_SECONDS, RuntimeNamespace
from hovorunv2.infrastructure.runtime.admission_state import (
    AdmissionCapacity,
    AdmissionChange,
    AdmissionKeys,
    AdmissionOperation,
    AdmissionRecord,
    AdmissionTransition,
    PublicationDecision,
    admission_keys,
    admission_ticket_key,
    calculate_change,
    envelope_json,
    read_capacity,
    read_counter,
    read_record,
    read_string,
    validate_claim,
)
from hovorunv2.infrastructure.runtime.cache import CacheService

MAX_ADMISSION_ATTEMPTS = 32
SORTED_SET_ENTRY_SIZE = 2


def _validate_payload(envelope: JobEnvelope, payload: SealedPayload, now: float) -> None:
    msg = "Admission payload, original timing and envelope binding disagree."
    if not math.isfinite(now) or now < envelope.accepted_at:
        raise ValueError(msg)
    if payload.reference != envelope.payload_ref or payload.job_id != envelope.job_id:
        raise ValueError(msg)
    if payload.deadline != envelope.deadline:
        raise ValueError(msg)
    if payload.byte_size != len(payload.blob.encode()) or payload.byte_size <= 0:
        raise ValueError(msg)


def _initial_record(envelope: JobEnvelope, payload: SealedPayload, keys: AdmissionKeys) -> AdmissionRecord:
    return AdmissionRecord(
        envelope=envelope_json(envelope),
        state=TicketState.ACCEPTED,
        stage=JobStage.MEDIA,
        byte_size=payload.byte_size,
        attempt=0,
        token="",
        published=0,
        not_before=0,
        subject_key=keys.subject,
        ingress_key=keys.ingress,
        payload_key=keys.payload,
        dedupe_key=keys.dedupe,
    )


def _duplicate_id(raw: object) -> str:
    value = read_string(raw)
    try:
        admission_ticket_key("", value)
    except ValueError:
        msg = "Admission deduplication metadata is unavailable."
        raise AdmissionUnavailableError(msg) from None
    return value


def _is_full(capacity: AdmissionCapacity, payload: SealedPayload, limits: AdmissionLimits) -> bool:
    return (
        capacity.count >= limits.safety.global_pending
        or capacity.byte_size + payload.byte_size > limits.safety.total_payload_bytes
        or payload.byte_size > limits.safety.payload_bytes
        or capacity.subject >= limits.subject_pending
        or capacity.ingress >= limits.ingress_pending
    )


def _validate_duplicate(record: AdmissionRecord, job_id: str, envelope: JobEnvelope) -> None:
    if record.job.job_id != job_id or record.job.dedupe_key != envelope.dedupe_key:
        msg = "Admission deduplication metadata is unavailable."
        raise AdmissionUnavailableError(msg)


def _require_new_identity(exists: object) -> None:
    if exists:
        msg = "Admission identity conflicts with existing work."
        raise ValueError(msg)


def _require_matching_blob(raw: object, payload: SealedPayload) -> None:
    if raw is not None and read_string(raw) != payload.blob:
        msg = "Admission identity conflicts with existing work."
        raise ValueError(msg)


def _validate_publication(envelope: JobEnvelope, not_before: float) -> None:
    if not math.isfinite(not_before) or not_before < 0:
        msg = "Scheduled feature time must be finite and nonnegative."
        raise ValueError(msg)
    if not_before >= envelope.deadline:
        msg = "Scheduled feature time exceeds the original deadline."
        raise ValueError(msg)


def _publication(change: AdmissionChange | None) -> PublicationDecision:
    if change is None or change.record is None:
        return PublicationDecision(EnqueueState.UNAVAILABLE)
    return PublicationDecision(
        change.publication_state, change.record.ticket, change.record.not_before, change.should_publish
    )


def _ticket(change: AdmissionChange | None) -> AdmissionTicket | None:
    return change.record.ticket if change is not None and change.record is not None else None


def _claimed(change: AdmissionChange | None) -> AdmissionTicket | None:
    ticket = _ticket(change) if change is not None and change.success else None
    return ticket if ticket is not None and ticket.state == TicketState.RUNNING else None


def _validate_recovery(now: float, limit: int) -> None:
    if not math.isfinite(now) or now < 0 or not 0 < limit <= RECOVERY_PAGE_LIMIT:
        msg = "Recovery requires finite time and a bounded positive page size."
        raise ValueError(msg)


def _validate_key_type(raw: object, expected: str) -> None:
    kind = raw.decode() if isinstance(raw, bytes) else raw
    if kind not in {"none", expected}:
        msg = "Admission key type is unavailable."
        raise AdmissionUnavailableError(msg)


def _require_ticket_identity(keys: AdmissionKeys, ticket_key: str) -> None:
    if keys.ticket != ticket_key:
        msg = "Admission ticket identity is unavailable."
        raise AdmissionUnavailableError(msg)


def _require_rotation_member(score: object) -> None:
    if not isinstance(score, int | float):
        msg = "Admission recovery membership is unavailable."
        raise AdmissionUnavailableError(msg)
    if not math.isfinite(score) or score < 0:
        msg = "Admission recovery membership is unavailable."
        raise AdmissionUnavailableError(msg)


def _rotation_score(raw: object) -> float:
    """Read one bounded scheduler score without trusting Redis response shapes."""
    if raw == []:
        return 0
    if isinstance(raw, list) and len(raw) == 1:
        entry = raw[0]
        if isinstance(entry, tuple | list) and len(entry) == SORTED_SET_ENTRY_SIZE:
            score = entry[1]
            if isinstance(score, int | float) and math.isfinite(score) and 0 <= score < (1 << 53) - 1:
                return float(score)
    msg = "Recovery rotation score is unavailable."
    raise AdmissionUnavailableError(msg)


class ValkeyAdmissionStore(AdmissionPort):
    """Keep accepted work counted until an explicit, owner-fenced terminal transaction."""

    def __init__(self, cache: CacheService, namespace: RuntimeNamespace) -> None:
        self._cache = cache
        self._namespace = namespace.value
        self._prefix = namespace.value + "admission:"

    def _id(self, job_id: str) -> str:
        return admission_ticket_key(self._prefix, job_id)

    async def admit(
        self, envelope: JobEnvelope, payload: SealedPayload, limits: AdmissionLimits, now: float
    ) -> AdmissionResult:
        """Atomically reserve every counter, index and ciphertext reference without embedded scripts."""
        _validate_payload(envelope, payload, now)
        keys = admission_keys(self._namespace, envelope)
        try:
            return await self._admit_with_retries(keys, envelope, payload, limits, now)
        except RedisError, OSError, UnicodeError, AdmissionUnavailableError:
            return AdmissionResult(AdmissionState.UNAVAILABLE)

    async def _admit_with_retries(
        self, keys: AdmissionKeys, envelope: JobEnvelope, payload: SealedPayload, limits: AdmissionLimits, now: float
    ) -> AdmissionResult:
        for _attempt in range(MAX_ADMISSION_ATTEMPTS):
            async with self._cache.client.pipeline() as pipe:
                try:
                    return await self._admit_attempt(pipe, keys, envelope, payload, limits, now)
                except WatchError:
                    continue
        return AdmissionResult(AdmissionState.UNAVAILABLE)

    async def _admit_attempt(
        self,
        pipe: AsyncPipeline,
        keys: AdmissionKeys,
        envelope: JobEnvelope,
        payload: SealedPayload,
        limits: AdmissionLimits,
        now: float,
    ) -> AdmissionResult:
        try:
            await pipe.watch(*keys.watched)
            duplicate = await pipe.get(keys.dedupe)
            if duplicate is not None:
                return await self._read_duplicate(pipe, duplicate, envelope)
            _require_new_identity(await pipe.exists(keys.ticket))
            if envelope.deadline <= now:
                return AdmissionResult(AdmissionState.EXPIRED)
            capacity = await self._read_capacity(pipe, keys)
            if _is_full(capacity, payload, limits):
                return AdmissionResult(AdmissionState.FULL)
            existing = await pipe.get(keys.payload)
            _validate_key_type(await pipe.type(keys.handoff), "zset")
            _validate_key_type(await pipe.type(keys.rotation), "zset")
            _validate_key_type(await pipe.type(keys.lane), "zset")
            _require_matching_blob(existing, payload)
            record = _initial_record(envelope, payload, keys)
            rotation = await pipe.execute_command("ZRANGE", keys.rotation, -1, -1, "WITHSCORES", withscores=True)
            last_job = await pipe.execute_command("ZRANGE", keys.lane, -1, -1, "WITHSCORES", withscores=True)
            schedule_scores = (_rotation_score(rotation) + 1, max(envelope.accepted_at, _rotation_score(last_job) + 1))
            self._write_admission(
                pipe, keys, envelope, record, payload, capacity, now, existing=existing, schedule_scores=schedule_scores
            )
            await pipe.execute()
            return AdmissionResult(AdmissionState.ACCEPTED, record.ticket)
        except AdmissionUnavailableError, ValueError, TypeError:
            # A partial snapshot can disagree while another valid transaction commits.
            # Confirm WATCH before treating it as stable corruption or a semantic conflict.
            pipe.multi()
            await pipe.execute()
            raise

    async def _read_duplicate(self, pipe: AsyncPipeline, duplicate: object, envelope: JobEnvelope) -> AdmissionResult:
        old_id = _duplicate_id(duplicate)
        await pipe.watch(self._id(old_id))
        record = read_record(await pipe.get(self._id(old_id)))
        record.keys(self._namespace)
        _validate_duplicate(record, old_id, envelope)
        pipe.multi()
        await pipe.execute()
        return AdmissionResult(AdmissionState.DUPLICATE, record.ticket)

    def _write_admission(
        self,
        pipe: AsyncPipeline,
        keys: AdmissionKeys,
        envelope: JobEnvelope,
        record: AdmissionRecord,
        payload: SealedPayload,
        capacity: AdmissionCapacity,
        now: float,
        *,
        existing: object,
        schedule_scores: tuple[float, float],
    ) -> None:
        pipe.multi()
        if existing is None:
            pipe.set(
                keys.payload,
                payload.blob,
                ex=max(1, math.ceil(envelope.deadline - now) + PAYLOAD_CLEANUP_SECONDS),
            )
        pipe.hset(
            keys.totals,
            mapping={"count": capacity.count + 1, "bytes": capacity.byte_size + payload.byte_size},
        )
        pipe.set(keys.subject, capacity.subject + 1)
        pipe.set(keys.ingress, capacity.ingress + 1)
        pipe.set(keys.ticket, record.encode())
        pipe.set(keys.dedupe, envelope.job_id)
        pipe.zadd(keys.handoff, {envelope.job_id: envelope.deadline})
        pipe.zadd(keys.pending, {envelope.job_id: envelope.deadline})
        pipe.zadd(keys.rotation, {keys.lane: schedule_scores[0]}, nx=True)
        pipe.zadd(keys.lane, {envelope.job_id: schedule_scores[1]})

    async def get(self, job_id: str) -> AdmissionTicket | None:
        """Return None only for absence; reject unreadable or misbound required metadata."""
        key = self._id(job_id)
        try:
            raw = await self._cache.client.get(key)
            if raw is None:
                return None
            record = read_record(raw)
            _require_ticket_identity(record.keys(self._namespace), key)
        except RedisError, OSError, UnicodeError, AdmissionUnavailableError:
            msg = "Admission runtime state is unavailable."
            raise AdmissionUnavailableError(msg) from None
        else:
            return record.ticket

    async def _apply(self, job_id: str, request: AdmissionTransition) -> AdmissionChange | None:
        key = self._id(job_id)
        try:
            return await self._apply_with_retries(key, job_id, request)
        except RedisError, OSError, UnicodeError, AdmissionUnavailableError:
            msg = "Admission runtime state is unavailable."
            raise AdmissionUnavailableError(msg) from None

    async def _apply_with_retries(self, key: str, job_id: str, request: AdmissionTransition) -> AdmissionChange | None:
        for _attempt in range(MAX_ADMISSION_ATTEMPTS):
            async with self._cache.client.pipeline() as pipe:
                try:
                    return await self._apply_attempt(pipe, key, job_id, request)
                except WatchError:
                    continue
        msg = "Admission transaction retry limit exhausted."
        raise AdmissionUnavailableError(msg)

    async def _apply_attempt(
        self, pipe: AsyncPipeline, key: str, job_id: str, request: AdmissionTransition
    ) -> AdmissionChange | None:
        try:
            await pipe.watch(key)
            raw = await pipe.get(key)
            if raw is None:
                return None
            record = read_record(raw)
            keys = record.keys(self._namespace)
            _require_ticket_identity(keys, key)
            change = calculate_change(record, request)
            capacity = None
            if change.write and record.state != TicketState.TERMINAL:
                capacity = await self._read_active_capacity(pipe, keys, record, job_id)
            pipe.multi()
            self._write_change(pipe, keys, change, capacity)
            await pipe.execute()
        except AdmissionUnavailableError, ValueError, TypeError:
            # A partial snapshot can disagree while another valid transaction commits.
            # Confirm WATCH before treating it as stable corruption or a semantic conflict.
            pipe.multi()
            await pipe.execute()
            raise
        else:
            return change

    async def _read_capacity(self, pipe: AsyncPipeline, keys: AdmissionKeys) -> AdmissionCapacity:
        return read_capacity(
            await pipe.execute_command("HGETALL", keys.totals),
            await pipe.get(keys.subject),
            await pipe.get(keys.ingress),
            await pipe.zcard(keys.pending),
        )

    async def _read_active_capacity(
        self, pipe: AsyncPipeline, keys: AdmissionKeys, record: AdmissionRecord, job_id: str
    ) -> AdmissionCapacity:
        await pipe.watch(*keys.watched)
        capacity = await self._read_capacity(pipe, keys)
        capacity.require_active(
            record,
            await pipe.get(keys.dedupe),
            await pipe.zscore(keys.pending, job_id),
            await pipe.zscore(keys.handoff, job_id),
        )
        _validate_key_type(await pipe.type(keys.handoff), "zset")
        _validate_key_type(await pipe.type(keys.rotation), "zset")
        _validate_key_type(await pipe.type(keys.lane), "zset")
        _validate_key_type(await pipe.type(keys.payload), "string")
        _require_rotation_member(await pipe.zscore(keys.rotation, keys.lane))
        _require_rotation_member(await pipe.zscore(keys.lane, job_id))
        return replace(capacity, lane_pending=read_counter(await pipe.zcard(keys.lane)))

    def _write_change(
        self, pipe: AsyncPipeline, keys: AdmissionKeys, change: AdmissionChange, capacity: AdmissionCapacity | None
    ) -> None:
        if not change.write or change.record is None:
            return
        record = change.record
        job = record.job
        if change.release:
            assert capacity is not None
            pipe.hset(
                keys.totals, mapping={"count": capacity.count - 1, "bytes": capacity.byte_size - record.byte_size}
            )
            if capacity.subject > 1:
                pipe.set(keys.subject, capacity.subject - 1)
            else:
                pipe.delete(keys.subject)
            if capacity.ingress > 1:
                pipe.set(keys.ingress, capacity.ingress - 1)
            else:
                pipe.delete(keys.ingress)
            pipe.delete(keys.payload)
            pipe.zrem(keys.pending, job.job_id)
            pipe.zrem(keys.lane, job.job_id)
            if capacity.lane_pending == 1:
                pipe.zrem(keys.rotation, keys.lane)
            pipe.set(keys.ticket, record.encode(), ex=RUNTIME_RETENTION_SECONDS)
            pipe.expire(keys.dedupe, RUNTIME_RETENTION_SECONDS)
        else:
            pipe.set(keys.ticket, record.encode(), keepttl=True)
        if change.handoff is True:
            pipe.zadd(keys.handoff, {job.job_id: job.deadline})
        elif change.handoff is False:
            pipe.zrem(keys.handoff, job.job_id)

    async def mark_enqueued(self, job_id: str, stage: JobStage, attempt: int) -> AdmissionTicket | None:
        """Acknowledge only the stage and attempt captured before publication."""
        return _ticket(
            await self._apply(job_id, AdmissionTransition(AdmissionOperation.ENQUEUED, stage=stage, attempt=attempt))
        )

    async def claim_stage(
        self, job_id: str, stage: JobStage, now: float, token: str, *, expected_envelope: JobEnvelope | None = None
    ) -> AdmissionTicket | None:
        """Claim one owner before private access; expiry never releases an existing runner."""
        validate_claim(now, token)
        return _claimed(
            await self._apply(
                job_id,
                AdmissionTransition(
                    AdmissionOperation.CLAIM, stage=stage, now=now, token=token, expected_envelope=expected_envelope
                ),
            )
        )

    async def handoff_stage(self, job_id: str, token: str, next_stage: JobStage) -> AdmissionTicket | None:
        """Release the media owner while retaining capacity, payload and original deadline."""
        return _ticket(
            await self._apply(job_id, AdmissionTransition(AdmissionOperation.HANDOFF, token=token, stage=next_stage))
        )

    async def cancel(self, job_id: str) -> bool:
        """Queued cancellation releases capacity; running cancellation retains the live owner."""
        change = await self._apply(job_id, AdmissionTransition(AdmissionOperation.CANCEL))
        return change is not None and change.success

    async def finish(self, job_id: str, outcome: JobOutcome, *, token: str | None = None) -> AdmissionTicket | None:
        """Release once after verified terminal execution using the current owner's fence."""
        return _ticket(
            await self._apply(
                job_id, AdmissionTransition(AdmissionOperation.FINISH, outcome=outcome, token=token or "")
            )
        )

    async def register_publication(self, envelope: JobEnvelope, not_before: float = 0) -> PublicationDecision:
        """Register the first schedule without extending it on uncertain broker retries."""
        _validate_publication(envelope, not_before)
        return _publication(
            await self._apply(
                envelope.job_id,
                AdmissionTransition(AdmissionOperation.REGISTER, expected_envelope=envelope, not_before=not_before),
            )
        )

    async def ack_publication(self, job_id: str, stage: JobStage, attempt: int) -> bool:
        """Fence broker acknowledgement against a newer stage or owner attempt."""
        change = await self._apply(
            job_id, AdmissionTransition(AdmissionOperation.PUBLISHED, stage=stage, attempt=attempt)
        )
        return change is not None and change.success

    async def claim_notice(self, job_id: str, kind: NoticeKind) -> bool:
        """Claim a delayed or terminal feedback dispatch once without renewing retention."""
        change = await self._apply(job_id, AdmissionTransition(AdmissionOperation.NOTICE, notice=kind))
        return change is not None and change.success

    async def recoverable(self, now: float, limit: int = RECOVERY_PAGE_LIMIT) -> tuple[AdmissionTicket, ...]:
        """Read a bounded native sorted-set page without reclaiming possibly running work."""
        _validate_recovery(now, limit)
        try:
            ids = await self._cache.client.zrangebyscore(
                self._prefix + "handoff", f"({now}", "+inf", start=0, num=limit
            )
            tickets = []
            for job_id in ids:
                ticket = await self.get(_duplicate_id(job_id))
                if ticket is not None and ticket.state == TicketState.ACCEPTED and ticket.envelope.deadline > now:
                    tickets.append(ticket)
            return tuple(tickets)
        except RedisError, OSError, UnicodeError, AdmissionUnavailableError:
            msg = "Admission recovery state is unavailable."
            raise AdmissionUnavailableError(msg) from None

    async def pending_for_feature(self, feature: str, limit: int = RECOVERY_PAGE_LIMIT) -> tuple[AdmissionTicket, ...]:
        """Read a bounded pending page and keep one normalized provider family."""
        if limit < 1:
            return ()
        key = resolve_emergency_key(feature)
        try:
            ids = await self._cache.client.zrange(self._prefix + "pending", 0, limit - 1)
            tickets = []
            for job_id in ids:
                ticket = await self.get(_duplicate_id(job_id))
                if ticket is not None and resolve_emergency_key(ticket.envelope.feature) == key:
                    tickets.append(ticket)
            return tuple(tickets)
        except RedisError, OSError, UnicodeError, AdmissionUnavailableError:
            msg = "Admission pending state is unavailable."
            raise AdmissionUnavailableError(msg) from None


class WorkerAdmissionClient:
    """Native worker transactions share pure state rules while retaining child-owned I/O."""

    def __init__(self, client: redis.Redis, namespace: RuntimeNamespace, require_owner: Callable[[], None]) -> None:
        self._client = client
        self._namespace = namespace.value
        self._prefix = namespace.value + "admission:"
        self._require_owner = require_owner

    def _id(self, job_id: str) -> str:
        self._require_owner()
        return admission_ticket_key(self._prefix, job_id)

    def get(self, job_id: str) -> AdmissionTicket | None:
        """Read validated ticket metadata only within the owning child context."""
        key = self._id(job_id)
        try:
            raw = self._client.get(key)
            if raw is None:
                return None
            record = read_record(raw)
            _require_ticket_identity(record.keys(self._namespace), key)
        except RedisError, OSError, UnicodeError, AdmissionUnavailableError:
            msg = "Admission runtime state is unavailable."
            raise AdmissionUnavailableError(msg) from None
        else:
            return record.ticket

    def pending_for_feature(self, feature: str, limit: int = RECOVERY_PAGE_LIMIT) -> tuple[AdmissionTicket, ...]:
        """Read a bounded pending page and keep one normalized provider family."""
        self._require_owner()
        if limit < 1:
            return ()
        key = resolve_emergency_key(feature)
        ids = self._client.execute_command("ZRANGE", self._prefix + "pending", 0, limit - 1)
        tickets = []
        for raw in ids:
            ticket = self.get(read_string(raw))
            if ticket is not None and resolve_emergency_key(ticket.envelope.feature) == key:
                tickets.append(ticket)
        return tuple(tickets)

    def _apply(self, job_id: str, request: AdmissionTransition) -> AdmissionChange | None:
        key = self._id(job_id)
        try:
            return self._apply_with_retries(key, job_id, request)
        except RedisError, OSError, UnicodeError, AdmissionUnavailableError:
            msg = "Admission runtime state is unavailable."
            raise AdmissionUnavailableError(msg) from None

    def _apply_with_retries(self, key: str, job_id: str, request: AdmissionTransition) -> AdmissionChange | None:
        for _attempt in range(MAX_ADMISSION_ATTEMPTS):
            with self._client.pipeline() as pipe:
                try:
                    return self._apply_attempt(pipe, key, job_id, request)
                except WatchError:
                    continue
        msg = "Admission transaction retry limit exhausted."
        raise AdmissionUnavailableError(msg)

    def _apply_attempt(
        self, pipe: Pipeline, key: str, job_id: str, request: AdmissionTransition
    ) -> AdmissionChange | None:
        try:
            pipe.watch(key)
            raw = pipe.get(key)
            if raw is None:
                return None
            record = read_record(raw)
            keys = record.keys(self._namespace)
            _require_ticket_identity(keys, key)
            change = calculate_change(record, request)
            capacity = None
            if change.write and record.state != TicketState.TERMINAL:
                capacity = self._read_active_capacity(pipe, keys, record, job_id)
            pipe.multi()
            self._write_change(pipe, keys, change, capacity)
            pipe.execute()
        except AdmissionUnavailableError, ValueError, TypeError:
            # A partial snapshot can disagree while another valid transaction commits.
            # Confirm WATCH before treating it as stable corruption or a semantic conflict.
            pipe.multi()
            pipe.execute()
            raise
        else:
            return change

    def _read_capacity(self, pipe: Pipeline, keys: AdmissionKeys) -> AdmissionCapacity:
        return read_capacity(
            pipe.hgetall(keys.totals),
            pipe.get(keys.subject),
            pipe.get(keys.ingress),
            pipe.zcard(keys.pending),
        )

    def _read_active_capacity(
        self, pipe: Pipeline, keys: AdmissionKeys, record: AdmissionRecord, job_id: str
    ) -> AdmissionCapacity:
        pipe.watch(*keys.watched)
        capacity = self._read_capacity(pipe, keys)
        capacity.require_active(
            record,
            pipe.get(keys.dedupe),
            pipe.zscore(keys.pending, job_id),
            pipe.zscore(keys.handoff, job_id),
        )
        _validate_key_type(pipe.type(keys.handoff), "zset")
        _validate_key_type(pipe.type(keys.rotation), "zset")
        _validate_key_type(pipe.type(keys.lane), "zset")
        _validate_key_type(pipe.type(keys.payload), "string")
        _require_rotation_member(pipe.zscore(keys.rotation, keys.lane))
        _require_rotation_member(pipe.zscore(keys.lane, job_id))
        return replace(capacity, lane_pending=read_counter(pipe.execute_command("ZCARD", keys.lane)))

    def _write_change(
        self, pipe: Pipeline, keys: AdmissionKeys, change: AdmissionChange, capacity: AdmissionCapacity | None
    ) -> None:
        if not change.write or change.record is None:
            return
        record = change.record
        job = record.job
        if change.release:
            assert capacity is not None
            pipe.hset(
                keys.totals, mapping={"count": capacity.count - 1, "bytes": capacity.byte_size - record.byte_size}
            )
            if capacity.subject > 1:
                pipe.set(keys.subject, capacity.subject - 1)
            else:
                pipe.delete(keys.subject)
            if capacity.ingress > 1:
                pipe.set(keys.ingress, capacity.ingress - 1)
            else:
                pipe.delete(keys.ingress)
            pipe.delete(keys.payload)
            pipe.zrem(keys.pending, job.job_id)
            pipe.zrem(keys.lane, job.job_id)
            if capacity.lane_pending == 1:
                pipe.zrem(keys.rotation, keys.lane)
            pipe.set(keys.ticket, record.encode(), ex=RUNTIME_RETENTION_SECONDS)
            pipe.expire(keys.dedupe, RUNTIME_RETENTION_SECONDS)
        else:
            pipe.set(keys.ticket, record.encode(), keepttl=True)
        if change.handoff is True:
            pipe.zadd(keys.handoff, {job.job_id: job.deadline})
        elif change.handoff is False:
            pipe.zrem(keys.handoff, job.job_id)

    def mark_enqueued(self, job_id: str, stage: JobStage, attempt: int) -> AdmissionTicket | None:
        """Acknowledge only the captured stage and attempt in the owning child."""
        return _ticket(
            self._apply(job_id, AdmissionTransition(AdmissionOperation.ENQUEUED, stage=stage, attempt=attempt))
        )

    def claim_stage(
        self, job_id: str, stage: JobStage, now: float, token: str, *, expected_envelope: JobEnvelope | None = None
    ) -> AdmissionTicket | None:
        """Claim exclusively with exact envelope checking before any private access."""
        self._require_owner()
        validate_claim(now, token)
        return _claimed(
            self._apply(
                job_id,
                AdmissionTransition(
                    AdmissionOperation.CLAIM, stage=stage, now=now, token=token, expected_envelope=expected_envelope
                ),
            )
        )

    def handoff_stage(self, job_id: str, token: str, next_stage: JobStage) -> AdmissionTicket | None:
        """Hand off without a second ciphertext, new deadline or admission refund."""
        return _ticket(
            self._apply(job_id, AdmissionTransition(AdmissionOperation.HANDOFF, token=token, stage=next_stage))
        )

    def cancel(self, job_id: str) -> bool:
        """Retain running ownership until execution effects are verified terminal."""
        change = self._apply(job_id, AdmissionTransition(AdmissionOperation.CANCEL))
        return change is not None and change.success

    def finish(self, job_id: str, outcome: JobOutcome, *, token: str | None = None) -> AdmissionTicket | None:
        """Settle once using the current owner's token and the shared terminal rules."""
        return _ticket(
            self._apply(job_id, AdmissionTransition(AdmissionOperation.FINISH, outcome=outcome, token=token or ""))
        )

    def claim_notice(self, job_id: str, kind: NoticeKind) -> bool:
        """Claim feedback before external delivery so uncertain acceptance cannot trigger duplicates."""
        change = self._apply(job_id, AdmissionTransition(AdmissionOperation.NOTICE, notice=kind))
        return change is not None and change.success

    def mark_reserved(self, job_id: str, token: str) -> AdmissionTicket | None:
        """Bind an acknowledged budget reservation before any stage handoff."""
        return _ticket(self._apply(job_id, AdmissionTransition(AdmissionOperation.RESERVED, token=token)))

    def defer(self, job_id: str, token: str, not_before: float) -> AdmissionTicket | None:
        """Persist quota waiting without retaining the running claim or renewing acceptance."""
        return _ticket(
            self._apply(
                job_id,
                AdmissionTransition(
                    AdmissionOperation.DEFER,
                    token=token,
                    not_before=not_before,
                ),
            )
        )

    def begin_delivery(self, job_id: str, token: str, now: float) -> AdmissionTicket | None:
        """Fence cancellation immediately before any external delivery effect."""
        validate_claim(now, token)
        return _ticket(self._apply(job_id, AdmissionTransition(AdmissionOperation.DELIVERY, token=token, now=now)))

    def prepare_settlement(self, job_id: str, token: str, intent: SettlementIntent) -> AdmissionTicket | None:
        """Persist immutable stopped-execution facts before either accounting phase."""
        return _ticket(
            self._apply(job_id, AdmissionTransition(AdmissionOperation.PREPARE, token=token, settlement=intent))
        )

    def allow_republication(self, ticket: AdmissionTicket, now: float) -> bool:
        """Bound publication attempts even when the broker acknowledgement is lost."""
        validate_claim(now, "publication")
        change = self._apply(
            ticket.envelope.job_id,
            AdmissionTransition(
                AdmissionOperation.REPUBLISH,
                stage=ticket.stage,
                attempt=ticket.attempt,
                now=now,
            ),
        )
        return change is not None and change.success

    def recovery_page(self, limit: int = RECOVERY_PAGE_LIMIT) -> tuple[AdmissionTicket, ...]:
        """Rotate subject/provider lanes and their tickets with bounded transactional retries."""
        self._require_owner()
        _validate_recovery(0, limit)
        try:
            return self._rotate_page(limit)
        except RedisError, OSError, UnicodeError:
            msg = "Admission recovery state is unavailable."
            raise AdmissionUnavailableError(msg) from None

    def _rotate_page(self, limit: int) -> tuple[AdmissionTicket, ...]:
        tickets = []
        lane_count = read_counter(self._client.execute_command("ZCARD", self._prefix + "rotation"))
        for _ in range(min(limit, lane_count)):
            job_id, lane = self._rotate_next()
            if lane is None:
                break
            if job_id is None:
                continue
            ticket = self.get(job_id)
            if ticket is None:
                msg = "Recovery index references missing admission state."
                raise AdmissionUnavailableError(msg)
            if admission_keys(self._namespace, ticket.envelope).lane != lane:
                msg = "Recovery lane disagrees with its admission subject."
                raise AdmissionUnavailableError(msg)
            tickets.append(ticket)
        return tuple(tickets)

    def _rotate_next(self) -> tuple[str | None, str | None]:
        rotation = self._prefix + "rotation"
        for _ in range(MAX_ADMISSION_ATTEMPTS):
            with self._client.pipeline() as pipe:
                try:
                    return self._rotate_attempt(pipe, rotation)
                except WatchError:
                    continue
        msg = "Admission recovery rotation retry limit exhausted."
        raise AdmissionUnavailableError(msg)

    def _rotate_attempt(self, pipe: Pipeline, rotation: str) -> tuple[str | None, str | None]:
        pipe.watch(rotation)
        lanes = pipe.execute_command("ZRANGE", rotation, 0, 0)
        if not lanes:
            return None, None
        lane = read_string(lanes[0])
        if not lane.startswith(self._prefix + "lane:"):
            msg = "Recovery lane is misbound."
            raise AdmissionUnavailableError(msg)
        pipe.watch(lane)
        head = pipe.execute_command("ZRANGE", lane, 0, 0)
        last_lane = pipe.execute_command("ZRANGE", rotation, -1, -1, "WITHSCORES", withscores=True)
        last_job = pipe.execute_command("ZRANGE", lane, -1, -1, "WITHSCORES", withscores=True)
        pipe.multi()
        if not head:
            pipe.zrem(rotation, lane)
        else:
            pipe.zadd(rotation, {lane: _rotation_score(last_lane) + 1})
            pipe.zadd(lane, {read_string(head[0]): _rotation_score(last_job) + 1})
        pipe.execute()
        return (read_string(head[0]) if head else None), lane

    def register_publication(self, envelope: JobEnvelope, not_before: float = 0) -> PublicationDecision:
        """Preserve the first publication schedule in synchronous worker continuations."""
        self._require_owner()
        _validate_publication(envelope, not_before)
        return _publication(
            self._apply(
                envelope.job_id,
                AdmissionTransition(AdmissionOperation.REGISTER, expected_envelope=envelope, not_before=not_before),
            )
        )

    def ack_publication(self, job_id: str, stage: JobStage, attempt: int) -> bool:
        """Acknowledge only the current synchronous publisher's captured stage and attempt."""
        change = self._apply(job_id, AdmissionTransition(AdmissionOperation.PUBLISHED, stage=stage, attempt=attempt))
        return change is not None and change.success
