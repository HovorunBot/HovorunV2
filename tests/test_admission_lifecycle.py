"""Real Valkey regressions for admission ownership, stage recovery and publication fences."""

import asyncio
import secrets
import subprocess
import sys
import time
from os import _exit, fork, waitpid

import pytest
import redis.asyncio as async_redis
from dishka import AsyncContainer

from hovorunv2.application.admission import AdmissionLimits, AdmissionState, JobStage, TicketState, incoming_action_key
from hovorunv2.application.budgets import BudgetRequest, ReservationState, RuntimeNamespace, UnitAmount, WallClock
from hovorunv2.application.feature_jobs import EnqueueState, JobOutcome, JobTarget, PayloadState
from hovorunv2.application.features import FEATURE_REGISTRY, TriggerKind
from hovorunv2.application.group_grants import GroupGrantStore
from hovorunv2.application.ports import CommandStore, UserStore
from hovorunv2.application.services.usage_policy_service import UsagePolicyService
from hovorunv2.application.usage import MEDIA_BUCKET, UsagePolicy, UsagePolicyStore, UsageSafetyCaps, UsageUnit
from hovorunv2.bootstrap.worker_runtime import WorkerRuntime
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import CommandName, TargetKind
from hovorunv2.infrastructure.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.budgets import ValkeyBudgetStore
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.feature_payloads import ValkeyPayloadStore
from hovorunv2.infrastructure.feature_queue import CeleryFeatureQueue
from tests.conftest import verify_group_admins
from tests.utils.identities import FEATURE
from tests.utils.jobs import PRIVATE_BODY, admission_service, job_envelope
from tests.utils.runtime import ManualClock, runtime_cache
from tests.utils.usage import GROUP, USER, usage_subjects


async def test_duplicate_claim_at_deadline_keeps_running_owner_and_capacity(test_container: AsyncContainer) -> None:
    """A late duplicate cannot treat the original deadline as proof that expensive execution stopped."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    owner, duplicate_owner = secrets.token_hex(16), secrets.token_hex(16)
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        store = ValkeyAdmissionStore(cache, namespace)
        sealed = await payloads.seal("running-deadline", PRIVATE_BODY, clock.now() + 300)
        envelope = job_envelope(sealed.reference, clock, job_id=sealed.job_id)
        assert (await store.admit(envelope, sealed, AdmissionLimits(1, 1, safety), clock.now())).state == (
            AdmissionState.ACCEPTED
        )
        assert await store.claim_stage(envelope.job_id, JobStage.MEDIA, clock.now(), owner) is not None
        clock.value = envelope.deadline
        assert await store.claim_stage(envelope.job_id, JobStage.MEDIA, clock.now(), duplicate_owner) is None
        ticket = await store.get(envelope.job_id)
        assert ticket is not None
        assert ticket.state == TicketState.RUNNING
        assert ticket.token == owner
        async with async_redis.from_url(settings.valkey_url) as client:
            totals = await client.execute_command("HGETALL", namespace.value + "admission:totals")
            assert int(totals[b"count"]) == 1
            assert int(totals[b"bytes"]) == sealed.byte_size
            assert await client.exists(namespace.value + "payload:" + sealed.reference)
        assert await store.finish(envelope.job_id, JobOutcome.EXPIRED, token=duplicate_owner) is None
        finished = await store.finish(envelope.job_id, JobOutcome.EXPIRED, token=owner)
        assert finished is not None
        assert finished.state == TicketState.TERMINAL


async def test_browser_handoff_is_recoverable_and_fences_previous_owner(test_container: AsyncContainer) -> None:
    """A crash before browser publication retains the same ticket, ciphertext and original deadline."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    media_owner, browser_owner = secrets.token_hex(16), secrets.token_hex(16)
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        store = ValkeyAdmissionStore(cache, namespace)
        sealed = await payloads.seal("browser-handoff", PRIVATE_BODY, clock.now() + 300)
        envelope = job_envelope(sealed.reference, clock, job_id=sealed.job_id)
        assert (await store.admit(envelope, sealed, AdmissionLimits(1, 1, safety), clock.now())).state == (
            AdmissionState.ACCEPTED
        )
        with WorkerRuntime(settings, clock, namespace, safety) as runtime:
            worker = runtime.admission
            media = await asyncio.to_thread(
                worker.claim_stage, envelope.job_id, JobStage.MEDIA, clock.now(), media_owner
            )
            assert media is not None
            assert media.attempt == 1
            ready = await asyncio.to_thread(worker.handoff_stage, envelope.job_id, media_owner, JobStage.BROWSER)
            assert ready is not None
            assert ready.envelope == envelope
            assert ready.stage == JobStage.BROWSER
            recoverable = await store.recoverable(clock.now())
            assert len(recoverable) == 1
            assert recoverable[0].envelope == envelope
            assert recoverable[0].stage == JobStage.BROWSER
            assert (await payloads.load(envelope.payload_ref, envelope.job_id)).state == PayloadState.READY
            assert await store.finish(envelope.job_id, JobOutcome.COMPLETE, token=media_owner) is None
            assert await store.claim_stage(envelope.job_id, JobStage.MEDIA, clock.now(), secrets.token_hex(16)) is None
            browser = await store.claim_stage(envelope.job_id, JobStage.BROWSER, clock.now(), browser_owner)
            assert browser is not None
            assert browser.attempt == 2
            assert browser.envelope == envelope
            assert await store.finish(envelope.job_id, JobOutcome.COMPLETE, token=media_owner) is None
            async with async_redis.from_url(settings.valkey_url) as inspector:
                totals = await inspector.execute_command("HGETALL", namespace.value + "admission:totals")
                assert int(totals[b"count"]) == 1
                assert int(totals[b"bytes"]) == sealed.byte_size
            finished = await asyncio.to_thread(worker.finish, envelope.job_id, JobOutcome.COMPLETE, token=browser_owner)
            assert finished is not None
            assert finished.state == TicketState.TERMINAL
        assert (await payloads.load(envelope.payload_ref, envelope.job_id)).state == PayloadState.MISSING


async def test_stale_media_publish_ack_preserves_browser_recovery(test_container: AsyncContainer) -> None:
    """An acknowledgement arriving after media execution cannot acknowledge the browser continuation."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        store = ValkeyAdmissionStore(cache, namespace)
        sealed = await payloads.seal("stale-publish-ack", PRIVATE_BODY, clock.now() + 300)
        envelope = job_envelope(sealed.reference, clock, job_id=sealed.job_id)
        admitted = await store.admit(envelope, sealed, AdmissionLimits(1, 1, safety), clock.now())
        initial = admitted.ticket
        assert initial is not None
        owner = secrets.token_hex(16)
        assert await store.claim_stage(envelope.job_id, JobStage.MEDIA, clock.now(), owner) is not None
        ready = await store.handoff_stage(envelope.job_id, owner, JobStage.BROWSER)
        assert ready is not None
        stale = await store.mark_enqueued(envelope.job_id, initial.stage, initial.attempt)
        assert stale is not None
        assert stale.state == TicketState.ACCEPTED
        assert stale.stage == JobStage.BROWSER
        assert await store.recoverable(clock.now()) == (ready,)
        wrong_attempt = await store.mark_enqueued(envelope.job_id, ready.stage, initial.attempt)
        assert wrong_attempt is not None
        assert wrong_attempt.state == TicketState.ACCEPTED
        assert await store.recoverable(clock.now()) == (ready,)
        assert (await payloads.load(sealed.reference, sealed.job_id)).state == PayloadState.READY
        acknowledged = await store.mark_enqueued(envelope.job_id, ready.stage, ready.attempt)
        assert acknowledged is not None
        assert acknowledged.state == TicketState.ENQUEUED
        assert not await store.recoverable(clock.now())
        assert (
            await store.claim_stage(envelope.job_id, JobStage.BROWSER, clock.now(), secrets.token_hex(16)) is not None
        )


@pytest.mark.parametrize(("invalid_time", "token"), [(False, ""), (True, "claim-owner")])
async def test_claim_rejects_invalid_owner_and_time_before_mutation(
    test_container: AsyncContainer, *, invalid_time: bool, token: str
) -> None:
    """Both runtime adapters reject ownerless claims and nonfinite clocks without touching the ticket."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyAdmissionStore(cache, namespace)
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        sealed = await payloads.seal("invalid-claim", PRIVATE_BODY, clock.now() + 300)
        envelope = job_envelope(sealed.reference, clock, job_id=sealed.job_id)
        admitted = await store.admit(envelope, sealed, AdmissionLimits(1, 1, safety), clock.now())
        now = float("nan") if invalid_time else clock.now()
        with pytest.raises(ValueError, match="Stage claims require"):
            await store.claim_stage(envelope.job_id, JobStage.MEDIA, now, token)
        with (
            WorkerRuntime(settings, clock, namespace, safety) as runtime,
            pytest.raises(ValueError, match="Stage claims require"),
        ):
            await asyncio.to_thread(runtime.admission.claim_stage, envelope.job_id, JobStage.MEDIA, now, token)
        assert await store.get(envelope.job_id) == admitted.ticket
        assert (await payloads.load(sealed.reference, sealed.job_id)).state == PayloadState.READY


@pytest.mark.parametrize("changed_field", ["payload_ref", "feature_epoch", "message_id"])
async def test_claim_rejects_envelope_that_disagrees_with_admitted_identity(
    test_container: AsyncContainer, changed_field: str
) -> None:
    """Possession of a job ID grants no right to substitute ciphertext, feature authority or source message."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        store = ValkeyAdmissionStore(cache, namespace)
        queue = CeleryFeatureQueue(settings, clock, namespace, safety)
        try:
            sealed = await payloads.seal("envelope-fence", PRIVATE_BODY, clock.now() + 300)
            envelope = job_envelope(sealed.reference, clock, job_id=sealed.job_id)
            assert (await store.admit(envelope, sealed, AdmissionLimits(1, 1, safety), clock.now())).state == (
                AdmissionState.ACCEPTED
            )
            changes: dict[str, object] = {"payload_ref": "f" * 32, "feature_epoch": 8, "message_id": 2}
            changed = envelope.model_copy(update={changed_field: changes[changed_field]})
            assert await queue.claim(changed) is None
            ticket = await store.get(envelope.job_id)
            assert ticket is not None
            assert ticket.envelope == envelope
            assert ticket.state == TicketState.ACCEPTED
            assert await queue.claim(envelope) is not None
        finally:
            await queue.close()


async def test_publishing_without_admission_cannot_create_executable_ticket(test_container: AsyncContainer) -> None:
    """The transport cannot bypass admission's encrypted payload and count reservations."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyAdmissionStore(cache, namespace)
        queue = CeleryFeatureQueue(settings, clock, namespace, UsageSafetyCaps())
        try:
            envelope = job_envelope("f" * 32, clock, job_id="unadmitted")
            assert await queue.enqueue(envelope) == EnqueueState.UNAVAILABLE
            assert await store.get(envelope.job_id) is None
            assert await queue.claim(envelope) is None
            async with async_redis.from_url(settings.valkey_url) as client:
                assert not [key async for key in client.scan_iter(match="*" + namespace.value + "*")]
        finally:
            await queue.close()


async def test_native_runtime_roundtrips_unicode_sender_label_and_rejects_closed_references(
    test_container: AsyncContainer,
) -> None:
    """Bot and native worker share encrypted payloads while retained worker references require live ownership."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    sender_label = "Синтетичний автор 🐱"
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        sealed = await payloads.seal("unicode-label", PRIVATE_BODY, clock.now() + 300, sender_label=sender_label)
        assert sender_label not in sealed.blob
        assert await payloads.put(sealed) == PayloadState.READY
        context = WorkerRuntime(settings, clock, namespace, safety)
        with pytest.raises(ValueError, match="open context"):
            context.payloads.load(sealed.reference, sealed.job_id)
        with context as runtime:
            native_payloads = runtime.payloads
            native_admission = runtime.admission
            native_budgets = runtime.budgets
            loaded = await asyncio.to_thread(native_payloads.load, sealed.reference, sealed.job_id)
            assert loaded.state == PayloadState.READY
            assert loaded.data == PRIVATE_BODY
            assert loaded.sender_label == sender_label
            native_sealed = await asyncio.to_thread(
                native_payloads.seal, "native-label", PRIVATE_BODY, clock.now() + 300, sender_label=sender_label
            )
            assert await asyncio.to_thread(native_payloads.put, native_sealed) == PayloadState.READY
            loaded_by_bot = await payloads.load(native_sealed.reference, native_sealed.job_id)
            assert loaded_by_bot.data == PRIVATE_BODY
            assert loaded_by_bot.sender_label == sender_label
            await asyncio.to_thread(native_payloads.delete, native_sealed.reference)
            assert (await payloads.load(native_sealed.reference, native_sealed.job_id)).state == PayloadState.MISSING
        with pytest.raises(ValueError, match="open context"):
            native_payloads.load(sealed.reference, sealed.job_id)
        with pytest.raises(ValueError, match="open context"):
            native_admission.get(sealed.job_id)
        with pytest.raises(ValueError, match="open context"):
            native_budgets.finish(sealed.job_id, (UnitAmount(UsageUnit.REQUESTS, 0),), clock.now())
        with pytest.raises(ValueError, match="more than once"):
            context.__enter__()


async def test_native_budget_reserve_and_settlement_share_bot_state(test_container: AsyncContainer) -> None:
    """Reservations and replayed settlements remain one atomic resource across synchronous and async adapters."""
    await usage_subjects(test_container)
    usage = await (await test_container.get(UsagePolicyService)).resolve(FEATURE, TriggerKind.DIRECT, USER)
    settings = await test_container.get(Settings)
    clock = ManualClock()
    actual = (UnitAmount(UsageUnit.REQUESTS, 1),)
    request = BudgetRequest("native-reservation", usage, actual, 400)
    async with runtime_cache(test_container) as (cache, namespace):
        bot_budgets = ValkeyBudgetStore(cache, namespace)
        with WorkerRuntime(settings, clock, namespace, UsageSafetyCaps()) as runtime:
            reserved = await asyncio.to_thread(runtime.budgets.reserve, request, clock.now(), (0, 0))
            assert reserved.state == ReservationState.RESERVED
            assert (await bot_budgets.reserve(request, clock.now(), (0, 0))).state == ReservationState.RESERVED
            assert (await bot_budgets.finish(request.reservation_id, actual, clock.now())).state == (
                ReservationState.COMMITTED
            )
            replay = await asyncio.to_thread(runtime.budgets.finish, request.reservation_id, actual, clock.now())
            assert replay.state == ReservationState.COMMITTED
            with pytest.raises(ValueError, match="conflicts"):
                await asyncio.to_thread(
                    runtime.budgets.finish, request.reservation_id, (UnitAmount(UsageUnit.REQUESTS, 0),), clock.now()
                )


@pytest.mark.parametrize(
    ("video_enabled", "post_enabled"), [(False, False), (False, True), (True, False), (True, True)]
)
@pytest.mark.parametrize("ingress_feature", [CommandName.TWITTER, CommandName.TWITTER_VIDEO, CommandName.TWITTER_POST])
async def test_provider_admission_checks_both_splits_and_reserves_shared_media_policy(
    test_container: AsyncContainer, ingress_feature: CommandName, *, video_enabled: bool, post_enabled: bool
) -> None:
    """Provider ingress accepts either enabled split and does not reserve an undetermined split's pending budget."""
    user, _group = await usage_subjects(test_container)
    await (await test_container.get(UserStore)).set_trust(USER, is_trusted=True, actor=ActorContext.cli())
    commands = await test_container.get(CommandStore)
    provider = CommandName.TWITTER
    video = FEATURE_REGISTRY.capture(provider, is_video=True).key
    post = FEATURE_REGISTRY.capture(provider, is_video=False).key
    for key, enabled in ((video, video_enabled), (post, post_enabled)):
        await commands.enable_command(USER, key, actor=ActorContext.cli())
        if not enabled:
            await commands.disable_command(USER, key, actor=ActorContext.cli())
    overrides = await test_container.get(UsagePolicyStore)
    for key in (video, post):
        assert await overrides.set_override(
            user.target, UsagePolicy(f"feature:{key}", 6, 3, 1, 1), actor=ActorContext.cli()
        )
    policies = await test_container.get(UsagePolicyService)
    media_usage = await policies.resolve_media(provider, TriggerKind.DIRECT, USER)
    assert {policy.bucket for policy in media_usage.policies} == {MEDIA_BUCKET}
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        store = ValkeyAdmissionStore(cache, namespace)
        queue = CeleryFeatureQueue(settings, clock, namespace, UsageSafetyCaps())
        try:
            service = await admission_service(test_container, store, queue, clock)
            for index in range(2):
                job_id = f"split-admission-{index}"
                sealed = await payloads.seal(job_id, PRIVATE_BODY, clock.now() + 300)
                envelope = job_envelope(
                    sealed.reference,
                    clock,
                    job_id=job_id,
                    feature=str(ingress_feature),
                    actor_id=USER,
                    target=JobTarget(
                        kind=TargetKind.USER, entity_id=user.entity_id, generation=user.generation, telegram_id=USER
                    ),
                    charged_telegram_id=USER,
                    message_id=index + 1,
                    dedupe_key=incoming_action_key(str(ingress_feature), USER, USER, index + 1, TriggerKind.DIRECT),
                )
                result = await service.admit(envelope, sealed)
                assert result.state == (
                    AdmissionState.ACCEPTED if video_enabled or post_enabled else AdmissionState.DENIED
                )
                assert (await payloads.load(sealed.reference, job_id)).state == (
                    PayloadState.READY if video_enabled or post_enabled else PayloadState.MISSING
                )
        finally:
            await queue.close()


async def test_sender_chat_automatic_group_duplicates_share_one_ticket_and_encrypted_label(
    test_container: AsyncContainer,
) -> None:
    """Sender-chat posts need no fabricated human identity, and concurrent retries retain one charged group ticket."""
    _user, group = await usage_subjects(test_container)
    await verify_group_admins(test_container, GROUP)
    assert await (await test_container.get(GroupGrantStore)).approve(group.entity_id, ActorContext.cli())
    provider = CommandName.TWITTER
    post = FEATURE_REGISTRY.capture(provider, is_video=False).key
    await (await test_container.get(CommandStore)).enable_command(GROUP, post, actor=ActorContext.cli())
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    sender_label = "Synthetic channel 🐱"
    dedupe = incoming_action_key(str(provider), GROUP, None, 1, TriggerKind.AUTOMATIC)
    assert dedupe == incoming_action_key(str(provider), GROUP, 0, 1, TriggerKind.AUTOMATIC)
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        store = ValkeyAdmissionStore(cache, namespace)
        queue = CeleryFeatureQueue(settings, clock, namespace, UsageSafetyCaps())
        try:
            service = await admission_service(test_container, store, queue, clock)
            entries = []
            for index in range(8):
                job_id = f"sender-chat-{index}"
                sealed = await payloads.seal(job_id, PRIVATE_BODY, clock.now() + 300, sender_label=sender_label)
                envelope = job_envelope(
                    sealed.reference,
                    clock,
                    job_id=job_id,
                    feature=str(provider),
                    actor_id=None,
                    actor_generation=None,
                    target=JobTarget(
                        kind=TargetKind.GROUP, entity_id=group.entity_id, generation=group.generation, telegram_id=GROUP
                    ),
                    trigger=TriggerKind.AUTOMATIC,
                    charged_kind=TargetKind.GROUP,
                    charged_telegram_id=GROUP,
                    dedupe_key=dedupe,
                )
                entries.append((envelope, sealed))
            results = await asyncio.gather(*[service.admit(envelope, sealed) for envelope, sealed in entries])
            assert sum(result.state == AdmissionState.ACCEPTED for result in results) == 1
            assert sum(result.state == AdmissionState.DUPLICATE for result in results) == 7
            winner = next(result.ticket for result in results if result.state == AdmissionState.ACCEPTED)
            assert winner is not None
            assert winner.envelope.actor_id is None
            assert winner.envelope.actor_generation is None
            assert all(result.ticket is not None and result.ticket.envelope == winner.envelope for result in results)
            loaded = await payloads.load(winner.envelope.payload_ref, winner.envelope.job_id)
            assert loaded.data == PRIVATE_BODY
            assert loaded.sender_label == sender_label
            states = [
                (await payloads.load(envelope.payload_ref, envelope.job_id)).state for envelope, _sealed in entries
            ]
            assert states.count(PayloadState.READY) == 1
            assert states.count(PayloadState.MISSING) == 7
            assert len(await store.recoverable(clock.now())) == 1
        finally:
            await queue.close()


def verify_inherited_runtime_rejected(namespace: RuntimeNamespace) -> None:
    """Open a real pool before a fork in a fresh, single-threaded interpreter."""
    settings = Settings(
        _env_file=None,
        bot_token="123456:synthetic-token",  # noqa: S106
        cache_encryption_key="synthetic-test-key",
    )
    with WorkerRuntime(settings, WallClock(), namespace, UsageSafetyCaps()) as runtime:
        admission = runtime.admission
        admission.get("inheritance-probe")
        child = fork()
        if child == 0:
            try:
                admission.get("inheritance-probe")
            except ValueError:
                _exit(0)
            _exit(1)
        _, status = waitpid(child, 0)
        assert status == 0


@pytest.mark.subprocess
@pytest.mark.slow
async def test_native_runtime_rejects_inherited_live_pool(test_container: AsyncContainer) -> None:
    """A genuine fork cannot use retained parent adapters even when the parent's pool already connected."""
    async with runtime_cache(test_container) as (_cache, namespace):
        result = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-W",
                "error",
                "-m",
                "tests.test_admission_lifecycle",
                namespace.value,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert not result.stderr


if __name__ == "__main__":
    verify_inherited_runtime_rejected(RuntimeNamespace(sys.argv[1]))
