"""Safe worker boundary preventing request-bearing exceptions/results from reaching the broker."""

from hovorunv2.application.budgets import Clock
from hovorunv2.application.feature_jobs import FeatureExecutor, JobEnvelope, JobOutcome, PayloadPort, PayloadState


class FeatureJobRunner:
    """Validate metadata and payload availability before handing bytes to a real executor."""

    def __init__(self, executor: FeatureExecutor, payloads: PayloadPort, clock: Clock) -> None:
        self._executor = executor
        self._payloads = payloads
        self._clock = clock

    async def run(self, envelope_data: dict[str, object]) -> str:
        """Catch request-bearing executor failures and return only an allowlisted outcome."""
        try:
            envelope = JobEnvelope.model_validate(envelope_data)
            if self._clock.now() >= envelope.deadline:
                return JobOutcome.EXPIRED.value
            loaded = await self._payloads.load(envelope.payload_ref, envelope.job_id)
            if loaded.state != PayloadState.READY or loaded.data is None:
                return JobOutcome.PAYLOAD_UNAVAILABLE.value
            result = await self._executor.execute(envelope, loaded.data)
            return result.value if isinstance(result, JobOutcome) else JobOutcome.FAILED.value
        except Exception:
            return JobOutcome.FAILED.value
