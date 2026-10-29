"""Celery feature publication with Valkey-owned execution facts and exclusive runner claims."""

import asyncio
import math
import secrets
from datetime import UTC, datetime

from celery import Celery
from kombu.exceptions import OperationalError
from redis.exceptions import RedisError

from hovorunv2.application.admission import AdmissionConflictError, AdmissionUnavailableError, JobStage, TicketState
from hovorunv2.application.budgets import RUNTIME_RETENTION_SECONDS, Clock, RuntimeNamespace
from hovorunv2.application.feature_jobs import (
    FEATURE_JOB_FUNCTION,
    EnqueueState,
    FeatureQueuePort,
    JobEnvelope,
    JobOutcome,
    JobStatus,
    QueuedFeature,
)
from hovorunv2.application.services.feature_job_runner import FeatureJobRunner
from hovorunv2.application.usage import UsageSafetyCaps
from hovorunv2.infrastructure.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.cache import CacheService
from hovorunv2.infrastructure.config import Settings

BROKER_VISIBILITY_SECONDS = 2 * RUNTIME_RETENTION_SECONDS
BROKER_SOCKET_SECONDS = 2


class CeleryFeatureQueue(FeatureQueuePort):
    """Keep publication uncertainty separate from exclusive execution and verified terminal outcomes.

    Celery task IDs are correlation identifiers, not a deduplication facility. A lost publish
    acknowledgement can produce duplicate broker messages. Every consumer must use claim()
    before invoking an executor. A running claim is never reclaimed on a timer.
    """

    def __init__(self, settings: Settings, clock: Clock, namespace: RuntimeNamespace, safety: UsageSafetyCaps) -> None:
        self._clock = clock
        self._safety = safety
        self._cache = CacheService(settings.valkey_url)
        self._admission = ValkeyAdmissionStore(self._cache, namespace)
        self._queue_name = namespace.value + "features"
        self._app = Celery("hovorun_features", broker=settings.valkey_url)
        self._app.conf.update(
            task_default_queue=self._queue_name,
            task_serializer="json",
            accept_content=["json"],
            task_ignore_result=True,
            task_store_errors_even_if_ignored=False,
            task_send_sent_event=False,
            worker_send_task_events=False,
            task_acks_late=True,
            task_reject_on_worker_lost=False,
            worker_prefetch_multiplier=1,
            broker_connection_timeout=BROKER_SOCKET_SECONDS,
            broker_transport_options={
                "global_keyprefix": namespace.value,
                "visibility_timeout": BROKER_VISIBILITY_SECONDS,
                "socket_connect_timeout": BROKER_SOCKET_SECONDS,
                "socket_timeout": BROKER_SOCKET_SECONDS,
                "retry_policy": {"max_retries": 0},
            },
        )

    async def enqueue(self, envelope: JobEnvelope, *, not_before: float = 0) -> EnqueueState:
        """Retry uncertain publication with original metadata; the consumer claim blocks duplicate execution."""
        now = self._clock.now()
        if not math.isfinite(not_before) or not_before < 0:
            msg = "Scheduled feature time must be finite and nonnegative."
            raise ValueError(msg)
        if now >= envelope.deadline or not_before >= envelope.deadline:
            return EnqueueState.EXPIRED
        try:
            publication = await self._admission.register_publication(envelope, not_before)
            if not publication.should_publish or publication.ticket is None:
                return publication.state
            if not await asyncio.to_thread(self._publish, envelope, publication.not_before):
                return EnqueueState.EXPIRED
            ticket = publication.ticket
            await self._admission.ack_publication(envelope.job_id, ticket.stage, ticket.attempt)
        except AdmissionUnavailableError, RedisError, OperationalError, OSError:
            return EnqueueState.UNAVAILABLE
        return publication.state

    def _publish(self, envelope: JobEnvelope, not_before: float) -> bool:
        """Run synchronous broker I/O only through to_thread; never share async resources with that thread."""
        remaining = envelope.deadline - max(self._clock.now(), not_before)
        if remaining <= 0:
            return False
        with self._app.connection_for_write() as connection, self._app.amqp.Producer(connection) as producer:
            self._app.send_task(
                FEATURE_JOB_FUNCTION,
                kwargs={"envelope": envelope.model_dump(mode="json")},
                task_id=envelope.job_id,
                producer=producer,
                queue=self._queue_name,
                eta=datetime.fromtimestamp(not_before, UTC) if not_before else None,
                expires=datetime.fromtimestamp(envelope.deadline, UTC),
                time_limit=max(1, math.ceil(min(self._safety.execution_seconds, remaining))),
                ignore_result=True,
                retry=False,
                argsrepr="()",
                kwargsrepr="<feature envelope>",
            )
        return True

    async def inspect(self, job_id: str) -> QueuedFeature | None:
        """Read application facts; Celery PENDING cannot distinguish unknown, queued and lost jobs."""
        ticket = await self._admission.get(job_id)
        if ticket is None:
            return None
        status_map = {
            TicketState.ACCEPTED: JobStatus.PENDING,
            TicketState.ENQUEUED: JobStatus.PENDING,
            TicketState.RUNNING: JobStatus.RUNNING,
            TicketState.CANCELLING: JobStatus.CANCELLING,
            TicketState.TERMINAL: JobStatus.TERMINAL,
        }
        return QueuedFeature(
            ticket.envelope,
            status_map.get(ticket.state, JobStatus.TERMINAL),
            ticket.outcome,
        )

    async def claim(self, envelope: JobEnvelope, stage: JobStage = JobStage.MEDIA) -> str | None:
        """Acquire one execution owner; duplicates, missing state, cancellation and expiry cannot execute."""
        token = secrets.token_hex(16)
        ticket = await self._admission.claim_stage(
            envelope.job_id, stage, self._clock.now(), token, expected_envelope=envelope
        )
        return token if ticket is not None else None

    async def finish(self, job_id: str, token: str, outcome: JobOutcome) -> bool:
        """Complete only the current execution owner through admission lifecycle fencing."""
        if not isinstance(outcome, JobOutcome):
            msg = "Feature completion requires a safe typed outcome."
            raise TypeError(msg)
        try:
            ticket = await self._admission.finish(job_id, outcome, token=token)
        except AdmissionConflictError:
            return False
        return ticket is not None

    async def cancel(self, job_id: str) -> bool:
        """Tombstone queued work atomically; running work retains its claim pending cooperative cancellation."""
        return await self._admission.cancel(job_id)

    async def close(self) -> None:
        """Close owned clients without starting a worker or touching other queues."""
        try:
            await asyncio.to_thread(self._app.close)
        finally:
            await self._cache.close()


class FeatureWorkerBridge:
    """Mandatory consumer boundary; compose the real runner and queue inside each executing process."""

    def __init__(self, runner: FeatureJobRunner, queue: CeleryFeatureQueue) -> None:
        self._runner = runner
        self._queue = queue

    async def run_feature_job(self, envelope: dict[str, object]) -> str:
        """Claim before loading private bytes; duplicate deliveries never invoke the runner twice."""
        try:
            return await self._run_claimed(envelope)
        except AdmissionUnavailableError:
            return JobOutcome.FAILED.value

    async def _run_claimed(self, envelope: dict[str, object]) -> str:
        job = JobEnvelope.model_validate(envelope)
        token = await self._queue.claim(job)
        if token is None:
            existing = await self._queue.inspect(job.job_id)
            return existing.outcome.value if existing and existing.outcome else JobOutcome.FAILED.value
        outcome = JobOutcome(await self._runner.run(envelope))
        await self._queue.finish(job.job_id, token, outcome)
        return outcome.value
