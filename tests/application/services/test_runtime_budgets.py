"""Atomic execution budgets against isolated real Valkey, with deterministic time."""

import asyncio
import json
from dataclasses import replace

import pytest
from dishka import AsyncContainer

from hovorunv2.application.features.registry import TriggerKind
from hovorunv2.application.usage.budget_service import BudgetService
from hovorunv2.application.usage.budgets import BudgetRequest, ReservationDecision, ReservationState, UnitAmount
from hovorunv2.application.usage.policies import UsageUnit
from hovorunv2.application.usage.usage_ledger import AccountingSubject, UsageCharge, UsageEvent, UsageLedgerStore
from hovorunv2.application.usage.usage_policy_service import UsagePolicyService
from hovorunv2.bootstrap.worker_runtime import WorkerRuntime
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import TargetKind
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.runtime.budgets import ValkeyBudgetStore, WorkerBudgetClient
from hovorunv2.infrastructure.runtime.cache import CacheService
from tests.utils.identities import FEATURE
from tests.utils.runtime import ManualClock, runtime_cache
from tests.utils.usage import USER, usage_subjects

ONE = (UnitAmount(UsageUnit.REQUESTS, 1),)
ZERO = (UnitAmount(UsageUnit.REQUESTS, 0),)
GROUP_REQUESTS_PER_MINUTE = 60


async def _reserve_budget(
    store: ValkeyBudgetStore | WorkerBudgetClient, request: BudgetRequest, now: float
) -> ReservationDecision:
    totals = tuple(0 for _ in request.usage.policies)
    if isinstance(store, WorkerBudgetClient):
        return await asyncio.to_thread(store.reserve, request, now, totals)
    return await store.reserve(request, now, totals)


async def _finish_budget(
    store: ValkeyBudgetStore | WorkerBudgetClient, reservation_id: str, actual: tuple[UnitAmount, ...], now: float
) -> ReservationDecision:
    if isinstance(store, WorkerBudgetClient):
        return await asyncio.to_thread(store.finish, reservation_id, actual, now)
    return await store.finish(reservation_id, actual, now)


async def test_concurrent_reservations_observe_capacity_and_replay(test_container: AsyncContainer) -> None:
    """Concurrent attempts cannot overdraw personal capacity; retrying an ID reserves nothing extra."""
    await usage_subjects(test_container)
    usage = await (await test_container.get(UsagePolicyService)).resolve(FEATURE, TriggerKind.DIRECT, USER)
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyBudgetStore(cache, namespace)
        requests = [BudgetRequest(f"job-{index}", usage, ONE, 400) for index in range(12)]
        results = await asyncio.gather(*[store.reserve(request, 120, (0, 0)) for request in requests])
        assert sum(result.allowed for result in results) == 1
        accepted = requests[next(index for index, result in enumerate(results) if result.allowed)]
        assert (await store.reserve(accepted, 121, (0, 0))).allowed
        assert (await store.finish(accepted.reservation_id, ONE, 121)).state == ReservationState.COMMITTED
        assert (await store.finish(accepted.reservation_id, ONE, 121)).state == ReservationState.COMMITTED
        with pytest.raises(ValueError, match="conflicts"):
            await store.finish(accepted.reservation_id, ZERO, 121)
        with pytest.raises(ValueError, match="conflicts"):
            await store.reserve(replace(accepted, deadline=401), 121, (0, 0))


async def test_burst_window_release_and_durable_seed_use_controlled_time(test_container: AsyncContainer) -> None:
    """Release refunds once, burst refills at its configured rate, and quota waits for the UTC window."""
    await usage_subjects(test_container)
    usage = await (await test_container.get(UsagePolicyService)).resolve(FEATURE, TriggerKind.DIRECT, USER)
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyBudgetStore(cache, namespace)
        for index in range(3):
            request = BudgetRequest(f"burst-{index}", usage, ONE, 400)
            assert (await store.reserve(request, 120, (0, 0))).allowed
            assert (await store.finish(request.reservation_id, ONE, 120)).state == ReservationState.COMMITTED
        waiting = await store.reserve(BudgetRequest("burst-wait", usage, ONE, 400), 120, (0, 0))
        assert waiting.state == ReservationState.WAITING
        assert waiting.next_eligible_at == 130
        assert (await store.reserve(BudgetRequest("burst-wait", usage, ONE, 400), 130, (0, 0))).allowed
        assert (await store.finish("burst-wait", ZERO, 130)).state == ReservationState.RELEASED
        assert (await store.finish("burst-wait", ZERO, 130)).state == ReservationState.RELEASED
        for index in range(3):
            request = BudgetRequest(f"rest-{index}", usage, ONE, 400)
            assert (await store.reserve(request, 150, (0, 0))).allowed
            await store.finish(request.reservation_id, ONE, 150)
        waiting = await store.reserve(BudgetRequest("window-wait", usage, ONE, 400), 150, (0, 0))
        assert waiting.next_eligible_at == 180
        assert (await store.reserve(BudgetRequest("next-window", usage, ONE, 400), 180, (0, 0))).allowed
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyBudgetStore(cache, namespace)
        assert (await store.reserve(BudgetRequest("seeded", usage, ONE, 400), 120, (6, 6))).next_eligible_at == 180


async def test_admin_bypass_keeps_global_safety_and_group_quotas(test_container: AsyncContainer) -> None:
    """Even forged group bypass facts cannot bypass group quota; personal admins retain global concurrency caps."""
    user, group = await usage_subjects(test_container)
    policy = await (await test_container.get(UsagePolicyService)).resolve(FEATURE, TriggerKind.DIRECT, USER)
    admin = replace(policy, bypass_personal_quota=True)
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyBudgetStore(cache, namespace)
        assert (await store.reserve(BudgetRequest("admin-1", admin, ONE, 400), 120, (999, 999))).allowed
        assert (await store.reserve(BudgetRequest("admin-2", admin, ONE, 400), 120, (999, 999))).allowed
        assert (
            await store.reserve(BudgetRequest("admin-3", admin, ONE, 400), 120, (999, 999))
        ).state == ReservationState.WAITING
        await store.finish("admin-1", ZERO, 120)
        await store.finish("admin-2", ZERO, 120)
        automatic = replace(policy, subject=group, bypass_personal_quota=True)
        assert (
            await store.reserve(BudgetRequest("group", automatic, ONE, 400), 120, (6, 6))
        ).state == ReservationState.WAITING
        assert user.kind == TargetKind.USER


async def test_budget_service_seeds_durable_usage_and_valkey_failure_pauses(test_container: AsyncContainer) -> None:
    """Production orchestration uses the injected clock and durable totals; unavailable runtime never allows work."""
    await usage_subjects(test_container)
    usage = await (await test_container.get(UsagePolicyService)).resolve(FEATURE, TriggerKind.DIRECT, USER)
    ledger = await test_container.get(UsageLedgerStore)
    await ledger.apply_batch(
        (
            UsageEvent(
                "seed",
                AccountingSubject(TargetKind.USER, USER),
                120,
                tuple(UsageCharge(policy.bucket, 6) for policy in usage.policies),
            ),
        ),
        actor=ActorContext.system(),
    )
    async with runtime_cache(test_container) as (cache, namespace):
        clock = ManualClock()
        service = BudgetService(ValkeyBudgetStore(cache, namespace), ledger, clock)
        assert (await service.reserve(BudgetRequest("clocked", usage, ONE, 400))).next_eligible_at == 180
        clock.value = 180
        assert (await service.reserve(BudgetRequest("clocked", usage, ONE, 400))).allowed
        assert (await service.finish("clocked", ZERO)).state == ReservationState.RELEASED
        unavailable = CacheService("redis://127.0.0.1:1/0")
        try:
            store = ValkeyBudgetStore(unavailable, namespace)
            assert (
                await store.reserve(BudgetRequest("paused", usage, ONE, 400), 120, (0, 0))
            ).state == ReservationState.UNAVAILABLE
        finally:
            await unavailable.close()


async def test_deadline_impossible_estimate_and_partial_settlement(test_container: AsyncContainer) -> None:
    """Expired retries cannot restart execution, and over-estimate refunds only the unused consumption."""
    await usage_subjects(test_container)
    usage = await (await test_container.get(UsagePolicyService)).resolve(FEATURE, TriggerKind.DIRECT, USER)
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyBudgetStore(cache, namespace)
        expired = BudgetRequest("expired", usage, ONE, 120)
        assert (await store.reserve(expired, 120, (0, 0))).state == ReservationState.EXPIRED
        impossible = BudgetRequest("impossible", usage, (UnitAmount(UsageUnit.REQUESTS, 4),), 400)
        assert (await store.reserve(impossible, 120, (0, 0))).state == ReservationState.IMPOSSIBLE
        active = BudgetRequest("active", usage, (UnitAmount(UsageUnit.REQUESTS, 3),), 121)
        assert (await store.reserve(active, 120, (0, 0))).allowed
        assert (await store.reserve(active, 121, (0, 0))).state == ReservationState.EXPIRED
        assert (
            await store.reserve(BudgetRequest("blocked", usage, ONE, 400), 121, (0, 0))
        ).state == ReservationState.WAITING
        assert (await store.finish("active", ONE, 121)).state == ReservationState.COMMITTED
        two = (UnitAmount(UsageUnit.REQUESTS, 2),)
        assert (await store.reserve(BudgetRequest("refund", usage, two, 400), 121, (0, 0))).allowed
        assert (await store.finish("refund", ZERO, 121)).state == ReservationState.RELEASED


async def test_fractional_deadline_replay_preserves_exact_original_timestamp(test_container: AsyncContainer) -> None:
    """Reservation replays compare the exact bound original deadline, including fractional seconds."""
    await usage_subjects(test_container)
    usage = await (await test_container.get(UsagePolicyService)).resolve(FEATURE, TriggerKind.DIRECT, USER)
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyBudgetStore(cache, namespace)
        deadline = 1_700_000_000.1234567
        request = BudgetRequest("fractional", usage, ONE, deadline)
        assert (await store.reserve(request, deadline - 1, (0, 0))).allowed
        assert (await store.reserve(request, deadline, (0, 0))).state == ReservationState.EXPIRED


async def test_inflight_reservation_survives_quota_window_rollover(test_container: AsyncContainer) -> None:
    """An active prior-window request causes concurrency waiting rather than unavailable state."""
    await usage_subjects(test_container)
    usage = await (await test_container.get(UsagePolicyService)).resolve(FEATURE, TriggerKind.DIRECT, USER)
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyBudgetStore(cache, namespace)
        assert (await store.reserve(BudgetRequest("prior-window", usage, ONE, 400), 179, (0, 0))).allowed
        decision = await store.reserve(BudgetRequest("next-window", usage, ONE, 400), 180, (0, 0))
        assert decision.state == ReservationState.WAITING
        assert (await store.finish("prior-window", ZERO, 181)).state == ReservationState.RELEASED
        assert (await store.reserve(BudgetRequest("next-window", usage, ONE, 400), 181, (0, 0))).allowed


@pytest.mark.parametrize("native", [False, True])
async def test_group_receives_sixty_requests_each_minute_across_inflight_rollover(
    test_container: AsyncContainer, *, native: bool
) -> None:
    """Retain a 60/minute group rate and allow spare concurrency while a prior-minute request runs."""
    _, group = await usage_subjects(test_container)
    policy = await (await test_container.get(UsagePolicyService)).resolve(FEATURE, TriggerKind.DIRECT, USER)
    usage = replace(
        policy,
        subject=group,
        policies=tuple(
            replace(item, limit=GROUP_REQUESTS_PER_MINUTE, burst=GROUP_REQUESTS_PER_MINUTE, concurrency=2)
            for item in policy.policies
        ),
    )
    settings = await test_container.get(Settings)
    async with runtime_cache(test_container) as (cache, namespace):
        with WorkerRuntime(settings, ManualClock(), namespace, usage.safety) as runtime:
            store = runtime.budgets if native else ValkeyBudgetStore(cache, namespace)
            assert (await _reserve_budget(store, BudgetRequest("prior", usage, ONE, 400), 179)).allowed
            assert (await _reserve_budget(store, BudgetRequest("next", usage, ONE, 400), 180)).allowed
            assert (await _finish_budget(store, "prior", ZERO, 181)).state == ReservationState.RELEASED
            assert (await _finish_budget(store, "next", ONE, 181)).state == ReservationState.COMMITTED
            for index in range(1, GROUP_REQUESTS_PER_MINUTE):
                request = BudgetRequest(f"minute-{index}", usage, ONE, 400)
                assert (await _reserve_budget(store, request, 181)).allowed
                assert (
                    await _finish_budget(store, request.reservation_id, ONE, 181)
                ).state == ReservationState.COMMITTED
            exhausted = await _reserve_budget(store, BudgetRequest("over-limit", usage, ONE, 400), 181)
            assert exhausted.state == ReservationState.WAITING
            assert exhausted.next_eligible_at == 240
            assert (await _reserve_budget(store, BudgetRequest("new-minute", usage, ONE, 400), 240)).allowed


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("missing", ["reservation", "quota", "rate", "active", "membership"])
async def test_rollover_does_not_hide_missing_prior_ownership(
    test_container: AsyncContainer, missing: str, *, native: bool
) -> None:
    """A new quota window cannot grant work when a prior holder's ownership cannot be verified."""
    await usage_subjects(test_container)
    usage = await (await test_container.get(UsagePolicyService)).resolve(FEATURE, TriggerKind.DIRECT, USER)
    settings = await test_container.get(Settings)
    async with runtime_cache(test_container) as (cache, namespace):
        with WorkerRuntime(settings, ManualClock(), namespace, usage.safety) as runtime:
            store = runtime.budgets if native else ValkeyBudgetStore(cache, namespace)
            assert (await _reserve_budget(store, BudgetRequest("prior", usage, ONE, 400), 179)).allowed
            reservation_key = namespace.value + "budget:reservation:prior"
            record = json.loads(await cache.client.get(reservation_key))
            keys = {
                "reservation": reservation_key,
                "quota": record["quotas"][0]["key"],
                "rate": record["quotas"][0]["rate"],
                "active": namespace.value + "budget:active",
            }
            if missing == "membership":
                await cache.client.srem(namespace.value + "budget:global", "prior")
            else:
                await cache.client.delete(keys[missing])
            decision = await _reserve_budget(store, BudgetRequest("new-minute", usage, ONE, 400), 180)
            assert decision.state == ReservationState.UNAVAILABLE
            assert await cache.client.get(namespace.value + "budget:reservation:new-minute") is None
