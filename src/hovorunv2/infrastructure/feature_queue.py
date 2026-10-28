"""Typed SAQ feature adapter with payload-free serialization and safe worker results."""

import contextlib
import math

import redis.asyncio as redis
from redis.exceptions import RedisError
from saq.job import Job, Status
from saq.queue.redis import RedisQueue

from hovorunv2.application.budgets import RUNTIME_RETENTION_SECONDS, TECHNICAL_ID, Clock, RuntimeNamespace
from hovorunv2.application.feature_jobs import (
    FEATURE_JOB_FUNCTION,
    EnqueueState,
    FeatureQueuePort,
    JobEnvelope,
    JobOutcome,
    QueuedFeature,
)
from hovorunv2.application.services.feature_job_runner import FeatureJobRunner
from hovorunv2.application.usage import UsageSafetyCaps
from hovorunv2.infrastructure.config import Settings


class SafeFeatureQueue(RedisQueue):
    """Sanitize SAQ's persisted result/error and debug rendering at the serialization boundary."""

    def serialize(self, job: Job) -> bytes | str:
        """Reject arbitrary kwargs and strip exception strings before SAQ persistence or finish logging."""
        kwargs = job.kwargs or {}
        if job.function != FEATURE_JOB_FUNCTION or set(kwargs) != {"envelope"}:
            msg = "Feature queue accepts only typed envelope jobs."
            raise ValueError(msg)
        envelope = JobEnvelope.model_validate(kwargs["envelope"])
        job.kwargs = {"envelope": envelope.model_dump(mode="json")}
        job.meta = {}
        if job.error is not None:
            job.error = "feature_job_failed"
        if job.result is not None:
            job.result = (
                JobOutcome(job.result).value
                if isinstance(job.result, str) and job.result in set(JobOutcome)
                else JobOutcome.FAILED.value
            )
        return super().serialize(job)

    async def abort(self, job: Job, error: str, ttl: float = 5) -> None:
        """Preserve SAQ cancellation flags without its deprecated SETEX path."""
        if not math.isfinite(ttl) or ttl <= 0:
            msg = "Cancellation retention must be positive and finite."
            raise ValueError(msg)
        async with self._op_sem:
            async with self.redis.pipeline(transaction=True) as pipe:
                job.status = Status.ABORTING
                job.error = "feature_job_cancelled"
                dequeued, *_ = await (
                    pipe.lrem(self._queued, 0, job.id)
                    .zrem(self._incomplete, job.id)
                    .set(job.id, self.serialize(job))
                    .set(job.abort_id, "feature_job_cancelled", ex=math.ceil(ttl))
                    .publish(job.id, job.status)
                    .execute()
                )
            if dequeued:
                await self.finish(job, Status.ABORTED, error="feature_job_cancelled")
                await self.redis.delete(job.abort_id)
            else:
                await self.redis.lrem(self._active, 0, job.id)

    async def _finish(
        self,
        job: Job,
        status: Status,
        *,
        result: object = None,
        error: str | None = None,
    ) -> None:
        """Preserve SAQ terminal cleanup using SET with expiry instead of deprecated SETEX."""
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.lrem(self._active, 1, job.id).zrem(self._incomplete, job.id)
            if job.ttl > 0:
                pipe.set(job.id, self.serialize(job), ex=job.ttl)
            elif job.ttl == 0:
                pipe.set(job.id, self.serialize(job))
            else:
                pipe.delete(job.id)
            await pipe.execute()
            await self.notify(job)


class SAQFeatureQueue(FeatureQueuePort):
    """Own a dedicated feature queue; generic operational TaskQueueService stays separate."""

    def __init__(self, settings: Settings, clock: Clock, namespace: RuntimeNamespace, safety: UsageSafetyCaps) -> None:
        self._clock = clock
        self._safety = safety
        self._queue = SafeFeatureQueue(redis.from_url(settings.valkey_url), name=namespace.value + "features")

    async def enqueue(self, envelope: JobEnvelope, *, not_before: float = 0) -> EnqueueState:
        """SAQ timeout limits execution; envelope deadline remains absolute acceptance-to-terminal time."""
        now = self._clock.now()
        if not math.isfinite(not_before) or not_before < 0:
            msg = "Scheduled feature time must be finite and nonnegative."
            raise ValueError(msg)
        if now >= envelope.deadline or not_before >= envelope.deadline:
            return EnqueueState.EXPIRED
        timeout = max(1, math.ceil(min(self._safety.execution_seconds, envelope.deadline - max(now, not_before))))
        try:
            existing = await self._queue.job(envelope.job_id)
            if existing is not None:
                if existing.kwargs != {"envelope": envelope.model_dump(mode="json")}:
                    msg = "Feature job ID conflicts with another envelope."
                    raise ValueError(msg)
                return EnqueueState.EXISTS
            job = await self._queue.enqueue(
                FEATURE_JOB_FUNCTION,
                key=envelope.job_id,
                envelope=envelope.model_dump(mode="json"),
                scheduled=not_before,
                timeout=timeout,
                ttl=RUNTIME_RETENTION_SECONDS,
                retries=0,
            )
        except RedisError, OSError:
            return EnqueueState.UNAVAILABLE
        if job is None:
            winner = await self._queue.job(envelope.job_id)
            if winner is None or winner.kwargs != {"envelope": envelope.model_dump(mode="json")}:
                msg = "Feature job ID conflicts with another envelope."
                raise ValueError(msg)
        return EnqueueState.ENQUEUED if job is not None else EnqueueState.EXISTS

    async def inspect(self, job_id: str) -> QueuedFeature | None:
        """Return operational metadata only; raw SAQ error strings never leave infrastructure."""
        self._validate_id(job_id)
        job = await self._queue.job(job_id)
        if job is None:
            return None
        envelope = JobEnvelope.model_validate((job.kwargs or {}).get("envelope"))
        outcome = JobOutcome(job.result) if isinstance(job.result, str) and job.result in set(JobOutcome) else None
        return QueuedFeature(envelope, job.status.value, outcome)

    async def cancel(self, job_id: str) -> bool:
        """Use a fixed operational cancellation reason, never a request-bearing exception."""
        self._validate_id(job_id)
        job = await self._queue.job(job_id)
        if job is None:
            return False
        await self._queue.abort(job, "feature_job_cancelled")
        return True

    def _validate_id(self, job_id: str) -> None:
        if TECHNICAL_ID.fullmatch(job_id) is None:
            msg = "Invalid feature job identity."
            raise ValueError(msg)

    async def close(self) -> None:
        """Close the dedicated SAQ connection without touching operational queues."""
        try:
            with contextlib.suppress(RedisError, OSError):
                await self._queue.disconnect()
        finally:
            await self._queue.redis.aclose()


class FeatureWorkerBridge:
    """Adapt a mandatory application runner to SAQ without putting private bytes into job state."""

    def __init__(self, runner: FeatureJobRunner) -> None:
        self._runner = runner

    async def run_feature_job(self, context: object, envelope: dict[str, object]) -> str:
        """Return only the runner's safe outcome code to the queue framework."""
        return await self._runner.run(envelope)
