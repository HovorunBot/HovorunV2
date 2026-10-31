"""Synthetic operational job metadata and queue helpers."""

import json
from dataclasses import replace
from queue import Empty
from typing import Any

import aiohttp
from celery import Celery

from hovorunv2.application.features.registry import TriggerKind
from hovorunv2.application.jobs.admission import AdmissionLimits
from hovorunv2.application.jobs.feature_jobs import JobEnvelope, JobOutcome, JobTarget
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.domain.enums import TargetKind
from hovorunv2.infrastructure.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.cache import CacheService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.feature_payloads import ValkeyPayloadStore
from tests.utils.identities import FEATURE
from tests.utils.runtime import ManualClock, RuntimeNamespace

PRIVATE_URL = "https://synthetic.invalid/private-request?secret=never-store"
PRIVATE_BODY = (PRIVATE_URL + "\nprivate caption").encode()


def job_envelope(reference: str, clock: ManualClock, *, job_id: str = "feature-job", **changes: object) -> JobEnvelope:
    """Construct minimal synthetic operational metadata without payload content."""
    values: dict[str, object] = {
        "job_id": job_id,
        "payload_ref": reference,
        "feature": FEATURE,
        "target": JobTarget(kind=TargetKind.GROUP, entity_id=1, generation=1, telegram_id=-390001),
        "actor_id": 390001,
        "actor_generation": 1,
        "trigger": TriggerKind.DIRECT,
        "charged_kind": TargetKind.USER,
        "charged_telegram_id": 390001,
        "accepted_at": clock.now(),
        "deadline": clock.now() + 300,
        "dedupe_key": "a" * 64,
        "feature_epoch": 7,
        "message_id": 1,
    }
    values.update(changes)
    return JobEnvelope.model_validate(values)


async def admit_test_job(
    settings: Settings,
    cache: CacheService,
    namespace: RuntimeNamespace,
    clock: ManualClock,
    envelope: JobEnvelope,
) -> None:
    """Accept real counted ciphertext before exercising the publication boundary."""
    payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
    raw = await cache.get(namespace.value + "payload:" + envelope.payload_ref)
    if raw is None:
        sealed = await payloads.seal(envelope.job_id, PRIVATE_BODY, envelope.deadline)
        sealed = replace(sealed, reference=envelope.payload_ref)
    else:
        blob = json.dumps(raw, separators=(",", ":"))
        from_payload = await payloads.seal(envelope.job_id, PRIVATE_BODY, envelope.deadline)
        sealed = replace(from_payload, reference=envelope.payload_ref, blob=blob, byte_size=len(blob.encode()))
    result = await ValkeyAdmissionStore(cache, namespace).admit(
        envelope, sealed, AdmissionLimits(10, 10, UsageSafetyCaps()), clock.now()
    )
    assert result.ticket is not None


class HTTPExecutor:
    """Exercise real external HTTP exception handling through the executor boundary."""

    async def execute(self, envelope: JobEnvelope, payload: bytes) -> JobOutcome:
        """Use actual HTTP I/O intercepted only at the external API boundary."""
        url = payload.decode().splitlines()[0]
        async with aiohttp.ClientSession() as session, session.get(url, raise_for_status=True) as response:
            await response.read()
        return JobOutcome.COMPLETE


def _drain_queue_messages(queue: Any) -> list[dict[str, object]]:
    envelopes = []
    while True:
        try:
            message = queue.get_nowait()
        except Empty:
            return envelopes
        assert PRIVATE_URL not in json.dumps(message.payload)
        assert message.headers["kwargsrepr"] == "<feature envelope>"
        envelopes.append(message.payload[1]["envelope"])
        message.ack()


def broker_envelopes(settings: Settings, namespace: str, queue_name: str = "media") -> list[dict[str, object]]:
    """Consume real broker messages without starting a production executor or worker."""
    app = Celery("test_features", broker=settings.valkey_url)
    app.conf.broker_transport_options = {"global_keyprefix": namespace}
    try:
        with app.connection_for_read() as connection, connection.SimpleQueue(namespace + queue_name) as queue:
            return _drain_queue_messages(queue)
    finally:
        app.close()
