"""Valkey-owned admission tickets, counted ciphertext and recoverable handoff indexes."""

import json
import math

from redis.exceptions import RedisError

from hovorunv2.application.admission import (
    RECOVERY_PAGE_LIMIT,
    AdmissionLimits,
    AdmissionPort,
    AdmissionResult,
    AdmissionState,
    AdmissionTicket,
    TicketState,
)
from hovorunv2.application.budgets import RUNTIME_RETENTION_SECONDS, TECHNICAL_ID, RuntimeNamespace
from hovorunv2.application.feature_jobs import PAYLOAD_CLEANUP_SECONDS, JobEnvelope, JobOutcome, SealedPayload
from hovorunv2.domain.enums import TargetKind
from hovorunv2.infrastructure.admission_scripts import ADMIT_WORK, TRANSITION_WORK
from hovorunv2.infrastructure.cache import CacheService


class ValkeyAdmissionStore(AdmissionPort):
    """Never evict accepted work or expire its capacity before explicit terminal reconciliation."""

    def __init__(self, cache: CacheService, namespace: RuntimeNamespace) -> None:
        self._cache = cache
        self._namespace = namespace.value
        self._prefix = namespace.value + "admission:"

    def _id(self, job_id: str) -> str:
        if TECHNICAL_ID.fullmatch(job_id) is None:
            msg = "Admission requires a technical job identity."
            raise ValueError(msg)
        return self._prefix + "ticket:" + job_id

    def _ticket(self, raw: object) -> AdmissionTicket:
        if isinstance(raw, bytes):
            raw = raw.decode()
        value = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(value, dict):
            msg = "Admission ticket metadata is unavailable."
            raise TypeError(msg)
        return AdmissionTicket(
            JobEnvelope.model_validate_json(value["envelope"]),
            TicketState(value["state"]),
            int(value["byte_size"]),
            JobOutcome(value["outcome"]) if value.get("outcome") else None,
        )

    async def admit(
        self, envelope: JobEnvelope, payload: SealedPayload, limits: AdmissionLimits, now: float
    ) -> AdmissionResult:
        """Write ciphertext and all counter reservations in the same script as the accepted ticket."""
        if (
            not math.isfinite(now)
            or now < envelope.accepted_at
            or payload.reference != envelope.payload_ref
            or payload.job_id != envelope.job_id
            or payload.deadline != envelope.deadline
            or payload.byte_size != len(payload.blob.encode())
            or payload.byte_size <= 0
        ):
            msg = "Admission payload, original timing and envelope binding disagree."
            raise ValueError(msg)
        owner = f"{envelope.charged_kind.value}:{envelope.charged_telegram_id}"
        ingress = (
            f"group:{envelope.target.telegram_id}:sender:{envelope.actor_id}"
            if envelope.target.kind == TargetKind.GROUP
            else f"user:{envelope.actor_id}"
        )
        keys = [
            self._id(envelope.job_id),
            self._prefix + "dedupe:" + envelope.dedupe_key,
            self._prefix + "totals",
            self._prefix + "subject:" + owner,
            self._prefix + "ingress:" + ingress,
            self._namespace + "payload:" + payload.reference,
            self._prefix + "handoff",
            self._prefix + "pending",
        ]
        values = {
            "envelope": envelope.model_dump(mode="json"),
            "envelope_json": json.dumps(envelope.model_dump(mode="json"), separators=(",", ":")),
            "blob": payload.blob,
            "byte_size": payload.byte_size,
            "now": now,
            "ticket_prefix": self._prefix + "ticket:",
            "global_limit": limits.safety.global_pending,
            "bytes_limit": limits.safety.total_payload_bytes,
            "payload_limit": limits.safety.payload_bytes,
            "subject_limit": limits.subject_pending,
            "ingress_limit": limits.ingress_pending,
            "payload_ttl": max(1, math.ceil(envelope.deadline - now) + PAYLOAD_CLEANUP_SECONDS),
        }
        try:
            result = await self._cache.run_script(ADMIT_WORK, keys, [json.dumps(values)])
        except RedisError, OSError:
            return AdmissionResult(AdmissionState.UNAVAILABLE)
        if not isinstance(result, list) or not result:
            return AdmissionResult(AdmissionState.UNAVAILABLE)
        state = result[0].decode() if isinstance(result[0], bytes) else str(result[0])
        if state == "conflict":
            msg = "Admission identity conflicts with existing work."
            raise ValueError(msg)
        return AdmissionResult(AdmissionState(state), self._ticket(result[1]) if len(result) > 1 else None)

    async def get(self, job_id: str) -> AdmissionTicket | None:
        """Read the stored immutable envelope, never ciphertext or broker implementation fields."""
        raw = await self._cache.get(self._id(job_id))
        return self._ticket(raw) if raw is not None else None

    async def _transition(self, job_id: str, state: str, outcome: str = "") -> AdmissionTicket | None:
        result = await self._cache.run_script(
            TRANSITION_WORK,
            [self._id(job_id), self._prefix + "totals", self._prefix + "handoff", self._prefix + "pending"],
            [state, outcome, str(RUNTIME_RETENTION_SECONDS)],
        )
        if not isinstance(result, list) or not result:
            return None
        code = result[0].decode() if isinstance(result[0], bytes) else str(result[0])
        if code in {"conflict", "unavailable"}:
            msg = "Admission terminal state or capacity metadata conflicts with this transition."
            raise ValueError(msg)
        return self._ticket(result[0])

    async def mark_enqueued(self, job_id: str) -> AdmissionTicket | None:
        """Acknowledge enqueue without reviving or re-counting a terminal ticket."""
        return await self._transition(job_id, TicketState.ENQUEUED.value)

    async def finish(self, job_id: str, outcome: JobOutcome) -> AdmissionTicket | None:
        """Release once only after the caller has verified terminal execution/delivery semantics."""
        return await self._transition(job_id, TicketState.TERMINAL.value, outcome.value)

    async def recoverable(self, now: float, limit: int = RECOVERY_PAGE_LIMIT) -> tuple[AdmissionTicket, ...]:
        """Skip expired tickets for handoff; they retain capacity until the separate terminal sweeper."""
        if not math.isfinite(now) or now < 0 or not 0 < limit <= RECOVERY_PAGE_LIMIT:
            msg = "Recovery requires finite time and a bounded positive page size."
            raise ValueError(msg)
        keys = await self._cache.run_script(
            "return redis.call('ZRANGEBYSCORE',KEYS[1],ARGV[1],'+inf','LIMIT',0,ARGV[2])",
            [self._prefix + "handoff"],
            [f"({now}", str(limit)],
        )
        if not isinstance(keys, list):
            return ()
        tickets = []
        for key in keys:
            ticket = await self.get(key.decode() if isinstance(key, bytes) else str(key))
            if ticket is not None and ticket.state == TicketState.ACCEPTED:
                tickets.append(ticket)
        return tuple(tickets)
