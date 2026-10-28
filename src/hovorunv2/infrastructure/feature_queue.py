"""Celery feature publication with Valkey-owned execution facts and exclusive runner claims."""

import asyncio
import json
import math
import secrets
from datetime import UTC, datetime

import redis.asyncio as redis
from celery import Celery
from kombu.exceptions import OperationalError
from redis.exceptions import RedisError

from hovorunv2.application.budgets import RUNTIME_RETENTION_SECONDS, TECHNICAL_ID, Clock, RuntimeNamespace
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
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.feature_queue_scripts import (
    CANCEL_FEATURE,
    CLAIM_FEATURE,
    FINISH_FEATURE,
    REGISTER_FEATURE,
)

BROKER_VISIBILITY_SECONDS = 2 * RUNTIME_RETENTION_SECONDS
BROKER_SOCKET_SECONDS = 2


def _envelope_json(envelope: JobEnvelope) -> str:
    return json.dumps(envelope.model_dump(mode="json"), separators=(",", ":"))


class CeleryFeatureQueue(FeatureQueuePort):
    """Keep publication uncertainty separate from exclusive execution and verified terminal outcomes.

    Celery task IDs are correlation identifiers, not a deduplication facility. A lost publish
    acknowledgement can produce duplicate broker messages. Every consumer must use claim()
    before invoking an executor. A running claim is never reclaimed on a timer.
    """

    def __init__(self, settings: Settings, clock: Clock, namespace: RuntimeNamespace, safety: UsageSafetyCaps) -> None:
        self._clock = clock
        self._safety = safety
        self._prefix = namespace.value + "feature-job:"
        self._client = redis.from_url(settings.valkey_url, decode_responses=True)
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
        key = self._key(envelope.job_id)
        try:
            registered = await self._client.execute_command(
                "EVAL", REGISTER_FEATURE, 1, key, _envelope_json(envelope), repr(not_before)
            )
            if registered == -1:
                msg = "Feature job ID conflicts with another envelope."
                raise ValueError(msg)
            if registered == 0:
                return EnqueueState.EXISTS
            # Read the original schedule, not a retry's potentially changed scheduling request.
            original_schedule = await self._client.execute_command("HGET", key, "not_before")
            if original_schedule is None:
                return EnqueueState.UNAVAILABLE
            if not await asyncio.to_thread(self._publish, envelope, float(original_schedule)):
                return EnqueueState.EXPIRED
            await self._client.execute_command("HSET", key, "published", "1")
        except RedisError, OperationalError, OSError:
            return EnqueueState.UNAVAILABLE
        return EnqueueState.ENQUEUED if registered == 1 else EnqueueState.EXISTS

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
        record = await self._client.execute_command("HGETALL", self._key(job_id))
        if not record:
            return None
        return QueuedFeature(
            JobEnvelope.model_validate_json(record["envelope"]),
            JobStatus(record["state"]),
            JobOutcome(record["outcome"]) if "outcome" in record else None,
        )

    async def claim(self, envelope: JobEnvelope) -> str | None:
        """Acquire one execution owner; duplicates, missing state, cancellation and expiry cannot execute."""
        token = secrets.token_hex(16)
        claimed = await self._client.execute_command(
            "EVAL",
            CLAIM_FEATURE,
            1,
            self._key(envelope.job_id),
            _envelope_json(envelope),
            repr(self._clock.now()),
            repr(envelope.deadline),
            token,
            RUNTIME_RETENTION_SECONDS,
        )
        return token if claimed == 1 else None

    async def finish(self, job_id: str, token: str, outcome: JobOutcome) -> bool:
        """Only the actual execution owner may record a safe outcome; never settle admission or budgets here."""
        if not isinstance(outcome, JobOutcome):
            msg = "Feature completion requires a safe typed outcome."
            raise TypeError(msg)
        return (
            await self._client.execute_command(
                "EVAL", FINISH_FEATURE, 1, self._key(job_id), token, outcome.value, RUNTIME_RETENTION_SECONDS
            )
            == 1
        )

    async def cancel(self, job_id: str) -> bool:
        """Tombstone queued work atomically; running work retains its claim pending cooperative cancellation."""
        return (
            await self._client.execute_command("EVAL", CANCEL_FEATURE, 1, self._key(job_id), RUNTIME_RETENTION_SECONDS)
            == 1
        )

    def _key(self, job_id: str) -> str:
        if TECHNICAL_ID.fullmatch(job_id) is None:
            msg = "Invalid feature job identity."
            raise ValueError(msg)
        return self._prefix + job_id

    async def close(self) -> None:
        """Close owned clients without starting a worker or touching other queues."""
        try:
            await asyncio.to_thread(self._app.close)
        finally:
            await self._client.aclose()


class FeatureWorkerBridge:
    """Mandatory consumer boundary; compose the real runner and queue inside each executing process."""

    def __init__(self, runner: FeatureJobRunner, queue: CeleryFeatureQueue) -> None:
        self._runner = runner
        self._queue = queue

    async def run_feature_job(self, envelope: dict[str, object]) -> str:
        """Claim before loading private bytes; duplicate deliveries never invoke the runner twice."""
        job = JobEnvelope.model_validate(envelope)
        token = await self._queue.claim(job)
        if token is None:
            existing = await self._queue.inspect(job.job_id)
            return existing.outcome.value if existing and existing.outcome else JobOutcome.FAILED.value
        outcome = JobOutcome(await self._runner.run(envelope))
        await self._queue.finish(job.job_id, token, outcome)
        return outcome.value
