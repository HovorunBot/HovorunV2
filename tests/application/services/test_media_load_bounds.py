"""Bounded adversarial load over real Valkey admission; no external providers."""

import secrets
import time

from dishka import AsyncContainer

from hovorunv2.application.features.registry import TriggerKind
from hovorunv2.application.jobs.admission import AdmissionLimits, AdmissionState
from hovorunv2.application.jobs.feature_jobs import JobTarget
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.domain.enums import TargetKind
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.runtime.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.runtime.feature_payloads import ValkeyPayloadStore
from tests.utils.jobs import PRIVATE_BODY, job_envelope
from tests.utils.runtime import ManualClock, runtime_cache
from tests.utils.usage import USER

PROVIDER_A = "twitter_video"
PROVIDER_B = "tiktok_video"
OTHER = 360002
THIRD = 360003
GROUP_A = -390001
GROUP_B = -390002


async def _admit(
    store: ValkeyAdmissionStore,
    payloads: ValkeyPayloadStore,
    clock: ManualClock,
    *,
    feature: str,
    charged: int,
    limits: AdmissionLimits,
    job_id: str,
) -> AdmissionState:
    sealed = await payloads.seal(job_id, PRIVATE_BODY, clock.now() + 300)
    envelope = job_envelope(
        sealed.reference,
        clock,
        job_id=sealed.job_id,
        feature=feature,
        charged_telegram_id=charged,
        actor_id=charged,
        dedupe_key=secrets.token_hex(32),
    )
    return (await store.admit(envelope, sealed, limits, clock.now())).state


async def test_subject_cap_is_shared_across_providers(test_container: AsyncContainer) -> None:
    """One charged subject cannot take more than its pending cap by changing provider."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps(global_pending=50)
    limits = AdmissionLimits(subject_pending=3, ingress_pending=50, safety=safety)
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        store = ValkeyAdmissionStore(cache, namespace)
        for index in range(3):
            feature = PROVIDER_A if index % 2 == 0 else PROVIDER_B
            state = await _admit(
                store, payloads, clock, feature=feature, charged=USER, limits=limits, job_id=f"subject-{index}"
            )
            assert state == AdmissionState.ACCEPTED
        blocked = await _admit(
            store, payloads, clock, feature=PROVIDER_A, charged=USER, limits=limits, job_id="subject-blocked"
        )
        assert blocked == AdmissionState.FULL
        other = await _admit(
            store, payloads, clock, feature=PROVIDER_A, charged=OTHER, limits=limits, job_id="subject-other"
        )
        assert other == AdmissionState.ACCEPTED


async def test_global_pending_and_ingress_are_hard_bounds(test_container: AsyncContainer) -> None:
    """Global pending blocks a fourth distinct subject; per-sender ingress blocks a third request."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps(global_pending=3)
    limits = AdmissionLimits(subject_pending=50, ingress_pending=50, safety=safety)
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        store = ValkeyAdmissionStore(cache, namespace)
        for index, charged in enumerate((USER, OTHER, THIRD)):
            assert (
                await _admit(
                    store, payloads, clock, feature=PROVIDER_A, charged=charged, limits=limits, job_id=f"global-{index}"
                )
                == AdmissionState.ACCEPTED
            )
        assert (
            await _admit(
                store, payloads, clock, feature=PROVIDER_A, charged=OTHER + 10, limits=limits, job_id="global-blocked"
            )
            == AdmissionState.FULL
        )
    ingress_safety = UsageSafetyCaps(global_pending=50)
    ingress_limits = AdmissionLimits(subject_pending=50, ingress_pending=2, safety=ingress_safety)
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, ingress_safety)
        store = ValkeyAdmissionStore(cache, namespace)
        for index in range(2):
            assert (
                await _admit(
                    store,
                    payloads,
                    clock,
                    feature=PROVIDER_A,
                    charged=USER,
                    limits=ingress_limits,
                    job_id=f"ingress-{index}",
                )
                == AdmissionState.ACCEPTED
            )
        assert (
            await _admit(
                store,
                payloads,
                clock,
                feature=PROVIDER_A,
                charged=USER,
                limits=ingress_limits,
                job_id="ingress-blocked",
            )
            == AdmissionState.FULL
        )
        assert (
            await _admit(
                store, payloads, clock, feature=PROVIDER_A, charged=OTHER, limits=ingress_limits, job_id="ingress-other"
            )
            == AdmissionState.ACCEPTED
        )


async def test_total_payload_bytes_are_bounded(test_container: AsyncContainer) -> None:
    """Accumulated ciphertext is capped independently from the count ceilings."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    generous = UsageSafetyCaps(global_pending=50)
    async with runtime_cache(test_container) as (cache, namespace):
        probe = ValkeyPayloadStore(cache, settings, clock, namespace, generous)
        first = await probe.seal("byte-probe", PRIVATE_BODY, clock.now() + 300)
        byte_cap = first.byte_size * 3
        safety = UsageSafetyCaps(
            global_pending=50,
            payload_bytes=first.byte_size + 64,
            total_payload_bytes=byte_cap + 64,
        )
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        store = ValkeyAdmissionStore(cache, namespace)
        limits = AdmissionLimits(subject_pending=50, ingress_pending=50, safety=safety)
        for index in range(3):
            assert (
                await _admit(
                    store, payloads, clock, feature=PROVIDER_A, charged=USER, limits=limits, job_id=f"bytes-{index}"
                )
                == AdmissionState.ACCEPTED
            )
        assert (
            await _admit(
                store, payloads, clock, feature=PROVIDER_A, charged=USER, limits=limits, job_id="bytes-blocked"
            )
            == AdmissionState.FULL
        )


async def _admit_group(
    store: ValkeyAdmissionStore,
    payloads: ValkeyPayloadStore,
    clock: ManualClock,
    *,
    feature: str,
    group: int,
    limits: AdmissionLimits,
    job_id: str,
) -> AdmissionState:
    sealed = await payloads.seal(job_id, PRIVATE_BODY, clock.now() + 300)
    envelope = job_envelope(
        sealed.reference,
        clock,
        job_id=sealed.job_id,
        feature=feature,
        trigger=TriggerKind.AUTOMATIC,
        actor_id=None,
        actor_generation=None,
        charged_kind=TargetKind.GROUP,
        charged_telegram_id=group,
        target=JobTarget(kind=TargetKind.GROUP, entity_id=1, generation=1, telegram_id=group),
        dedupe_key=secrets.token_hex(32),
    )
    return (await store.admit(envelope, sealed, limits, clock.now())).state


async def test_group_subject_cap_is_shared_across_providers(test_container: AsyncContainer) -> None:
    """A group subject is charged once across providers and cannot monopolize all capacity."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps(global_pending=50)
    limits = AdmissionLimits(subject_pending=2, ingress_pending=50, safety=safety)
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        store = ValkeyAdmissionStore(cache, namespace)
        assert (
            await _admit_group(
                store, payloads, clock, feature=PROVIDER_A, group=GROUP_A, limits=limits, job_id="group-a"
            )
            == AdmissionState.ACCEPTED
        )
        assert (
            await _admit_group(
                store, payloads, clock, feature=PROVIDER_B, group=GROUP_A, limits=limits, job_id="group-b"
            )
            == AdmissionState.ACCEPTED
        )
        assert (
            await _admit_group(
                store, payloads, clock, feature=PROVIDER_A, group=GROUP_A, limits=limits, job_id="group-blocked"
            )
            == AdmissionState.FULL
        )
        assert (
            await _admit_group(
                store, payloads, clock, feature=PROVIDER_A, group=GROUP_B, limits=limits, job_id="group-other"
            )
            == AdmissionState.ACCEPTED
        )
