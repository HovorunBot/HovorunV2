"""Celery feature publication with Valkey-owned execution facts and exclusive runner claims."""

import asyncio
import math
import secrets
from collections.abc import Callable
from datetime import UTC, datetime

from celery import Celery
from kombu import Queue
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
from hovorunv2.application.usage import UsageSafetyCaps
from hovorunv2.application.worker_io import SyncQueuePublisherPort
from hovorunv2.infrastructure.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.cache import CacheService
from hovorunv2.infrastructure.config import Settings

BROKER_VISIBILITY_SECONDS = 2 * RUNTIME_RETENTION_SECONDS
BROKER_SOCKET_SECONDS = 2

RECOVERY_TASK_FUNCTION = "recover_feature_jobs"

TaskRunner = Callable[[dict[str, object], JobStage], str]
_task_runner: TaskRunner | None = None
_recovery_runner: Callable[[], int] | None = None


def set_feature_task_runner(runner: TaskRunner | None) -> None:
    """Attach the active runner callback for native worker processes."""
    global _task_runner  # noqa: PLW0603
    _task_runner = runner


def set_recovery_runner(runner: Callable[[], int] | None) -> None:
    """Attach recovery only after child-local resources are initialized."""
    global _recovery_runner  # noqa: PLW0603
    _recovery_runner = runner


def recover_feature_jobs() -> int:
    """Run one bounded native lifecycle sweep without private task inputs or results."""
    if _recovery_runner is None:
        msg = "Worker lifecycle recovery is not initialized."
        raise ValueError(msg)
    return _recovery_runner()


def get_feature_task_runner() -> TaskRunner:
    """Return the attached runner or raise when invoked before worker initialization."""
    if _task_runner is None:
        msg = "Worker feature task runner is not initialized."
        raise ValueError(msg)
    return _task_runner


def register_feature_tasks(app: Celery) -> None:
    """Register synchronous worker tasks on a Celery application instance."""
    if RECOVERY_TASK_FUNCTION not in app.tasks:
        app.task(name=RECOVERY_TASK_FUNCTION, ignore_result=True)(recover_feature_jobs)
    if FEATURE_JOB_FUNCTION not in app.tasks:

        @app.task(name=FEATURE_JOB_FUNCTION, bind=True)
        def _run_feature_job(task: object, envelope: dict[str, object], stage: str = "media") -> str:
            runner = get_feature_task_runner()
            return runner(envelope, JobStage(stage))

    if "run_media_job" not in app.tasks:

        @app.task(name="run_media_job", bind=True)
        def _run_media_job(task: object, envelope: dict[str, object]) -> str:
            runner = get_feature_task_runner()
            return runner(envelope, JobStage.MEDIA)

    if "run_browser_job" not in app.tasks:

        @app.task(name="run_browser_job", bind=True)
        def _run_browser_job(task: object, envelope: dict[str, object]) -> str:
            runner = get_feature_task_runner()
            return runner(envelope, JobStage.BROWSER)


celery_app = Celery("hovorun_features")
register_feature_tasks(celery_app)


class CeleryFeatureQueue(FeatureQueuePort, SyncQueuePublisherPort):
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
        self._media_queue = namespace.value + "media"
        self._browser_queue = namespace.value + "browser"
        self._queue_name = self._media_queue
        self._app = Celery("hovorun_features", broker=settings.valkey_url)
        self._app.conf.update(
            task_default_queue=self._media_queue,
            task_queues=[
                Queue(self._media_queue),
                Queue(self._browser_queue),
            ],
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
        register_feature_tasks(self._app)

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

    def publish_stage(self, envelope: JobEnvelope, stage: JobStage, not_before: float = 0) -> bool:
        """Publish an envelope to the appropriate stage queue."""
        queue = self._browser_queue if stage == JobStage.BROWSER else self._media_queue
        return self._publish(envelope, not_before, queue_name=queue, stage=stage)

    def _publish(
        self,
        envelope: JobEnvelope,
        not_before: float,
        *,
        queue_name: str | None = None,
        stage: JobStage = JobStage.MEDIA,
    ) -> bool:
        """Run synchronous broker I/O only through to_thread; never share async resources with that thread."""
        remaining = envelope.deadline - max(self._clock.now(), not_before)
        if remaining <= 0:
            return False
        queue = queue_name or self._media_queue
        with self._app.connection_for_write() as connection, self._app.amqp.Producer(connection) as producer:
            self._app.send_task(
                FEATURE_JOB_FUNCTION,
                kwargs={"envelope": envelope.model_dump(mode="json"), "stage": stage.value},
                task_id=envelope.job_id,
                producer=producer,
                queue=queue,
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
            TicketState.WAITING: JobStatus.PENDING,
            TicketState.SETTLING: JobStatus.RUNNING,
            TicketState.DELIVERY_UNCERTAIN: JobStatus.RUNNING,
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
