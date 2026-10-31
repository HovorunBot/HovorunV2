"""Real Valkey safety and concurrency proofs for native Python budget transactions."""

import asyncio
import json
from contextlib import ExitStack
from dataclasses import replace

import pytest
from dishka import AsyncContainer

from hovorunv2.application.features.registry import TriggerKind
from hovorunv2.application.usage.budgets import BudgetRequest, ReservationState
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.application.usage.usage_policy_service import UsagePolicyService
from hovorunv2.bootstrap.worker_runtime import WorkerRuntime
from hovorunv2.infrastructure.budget_state import MAX_EXACT_INTEGER, build_record
from hovorunv2.infrastructure.budgets import ValkeyBudgetStore
from hovorunv2.infrastructure.config import Settings
from tests.utils.identities import FEATURE, ONE, ZERO
from tests.utils.runtime import ManualClock, runtime_cache
from tests.utils.usage import USER, usage_subjects


@pytest.mark.parametrize("damage", ["quota_field", "rate_field", "held", "membership", "active_index", "rate_key"])
async def test_missing_resource_ownership_never_partially_settles_or_refunds(
    test_container: AsyncContainer,
    damage: str,
) -> None:
    """Every required hash field and membership must survive until the verified atomic settlement."""
    await usage_subjects(test_container)
    usage = await (await test_container.get(UsagePolicyService)).resolve(FEATURE, TriggerKind.DIRECT, USER)
    settings = await test_container.get(Settings)
    clock = ManualClock()
    request = BudgetRequest("damaged-reservation", usage, ONE, 400)
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyBudgetStore(cache, namespace)
        prefix = namespace.value + "budget:"
        record = build_record(prefix, request, clock.now(), (0, 0))
        assert (await store.reserve(request, clock.now(), (0, 0))).allowed
        quota = record.quotas[0]
        if damage == "quota_field":
            await cache.client.hdel(quota.key, "used")
        elif damage == "rate_field":
            await cache.client.hdel(quota.rate, "tokens")
        elif damage == "held":
            await cache.client.hset(quota.key, "held", 0)
        elif damage == "membership":
            await cache.client.srem(record.concurrency[0].key, request.reservation_id)
        elif damage == "active_index":
            await cache.client.zrem(prefix + "active", request.reservation_id)
        else:
            await cache.client.delete(quota.rate)
        key = prefix + "reservation:" + request.reservation_id
        before_record = await cache.client.get(key)
        before_quota = await cache.client.execute_command("HGETALL", quota.key)
        other_quota = record.quotas[1]
        before_other = await cache.client.execute_command("HGETALL", other_quota.key)
        with WorkerRuntime(settings, clock, namespace, UsageSafetyCaps()) as runtime:
            assert (
                await asyncio.to_thread(runtime.budgets.finish, request.reservation_id, ZERO, clock.now())
            ).state == (ReservationState.UNAVAILABLE)
        assert (await store.finish(request.reservation_id, ZERO, clock.now())).state == ReservationState.UNAVAILABLE
        assert (await store.reserve(request, clock.now(), (0, 0))).state == ReservationState.UNAVAILABLE
        assert await cache.client.get(key) == before_record
        assert await cache.client.execute_command("HGETALL", quota.key) == before_quota
        assert await cache.client.execute_command("HGETALL", other_quota.key) == before_other


@pytest.mark.parametrize("damage", ["redirect_rate", "redirect_window", "redirect_concurrency", "counter_overflow"])
async def test_malformed_record_and_counter_overflow_fail_before_any_mutation(
    test_container: AsyncContainer,
    damage: str,
) -> None:
    """Stored metadata cannot redirect settlement into another hash or overflow established integer bounds."""
    await usage_subjects(test_container)
    usage = await (await test_container.get(UsagePolicyService)).resolve(FEATURE, TriggerKind.DIRECT, USER)
    clock = ManualClock()
    request = BudgetRequest("malformed-reservation", usage, ONE, 400)
    async with runtime_cache(test_container) as (cache, namespace):
        prefix = namespace.value + "budget:"
        store = ValkeyBudgetStore(cache, namespace)
        assert (await store.reserve(request, clock.now(), (0, 0))).allowed
        key = prefix + "reservation:" + request.reservation_id
        record = json.loads(await cache.client.get(key))
        quota = record["quotas"][0]
        if damage == "redirect_rate":
            quota["rate"] = prefix + "unrelated"
        elif damage == "redirect_window":
            quota["key"] = prefix + "unrelated"
        elif damage == "redirect_concurrency":
            record["concurrency"][0]["key"] = prefix + "unrelated"
        else:
            await cache.client.hset(quota["key"], "used", MAX_EXACT_INTEGER)
        await cache.client.set(key, json.dumps(record))
        before = await cache.client.get(key)
        assert (await store.finish(request.reservation_id, ONE, clock.now())).state == ReservationState.UNAVAILABLE
        assert await cache.client.get(key) == before
        assert not await cache.client.exists(prefix + "unrelated")
        assert await cache.client.sismember(prefix + "global", request.reservation_id)


async def test_async_and_native_competing_reservations_and_settlements_are_atomic(
    test_container: AsyncContainer,
) -> None:
    """Bot and worker contend on the same keys and commit actual usage exactly once across duplicate settlement."""
    await usage_subjects(test_container)
    usage = await (await test_container.get(UsagePolicyService)).resolve(FEATURE, TriggerKind.DIRECT, USER)
    settings = await test_container.get(Settings)
    clock = ManualClock()
    requests = [BudgetRequest(f"competing-{index}", usage, ONE, 400) for index in range(12)]
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyBudgetStore(cache, namespace)
        with ExitStack() as resources:
            workers = [
                resources.enter_context(WorkerRuntime(settings, clock, namespace, UsageSafetyCaps())) for _ in range(6)
            ]
            results = await asyncio.gather(
                *[
                    store.reserve(request, clock.now(), (0, 0))
                    if index % 2
                    else asyncio.to_thread(workers[index // 2].budgets.reserve, request, clock.now(), (0, 0))
                    for index, request in enumerate(requests)
                ]
            )
            assert sum(result.allowed for result in results) == 1
            winner = requests[next(index for index, result in enumerate(results) if result.allowed)]
            settlements = await asyncio.gather(
                *[
                    store.finish(winner.reservation_id, ONE, clock.now())
                    if index % 2
                    else asyncio.to_thread(workers[index // 2].budgets.finish, winner.reservation_id, ONE, clock.now())
                    for index in range(12)
                ]
            )
            assert all(result.state == ReservationState.COMMITTED for result in settlements), [
                result.state for result in settlements
            ]
        record = build_record(namespace.value + "budget:", winner, clock.now(), (0, 0))
        for quota in record.quotas:
            assert await cache.client.hget(quota.key, "used") == b"1"
            assert await cache.client.hget(quota.key, "held") == b"0"
        assert await cache.client.scard(namespace.value + "budget:global") == 0


async def test_terminal_receipt_replay_preserves_expiry_and_admin_empty_quota_record(
    test_container: AsyncContainer,
) -> None:
    """Native terminal replay never extends retention and accepts the historical empty quota JSON shape."""
    await usage_subjects(test_container)
    usage = await (await test_container.get(UsagePolicyService)).resolve(FEATURE, TriggerKind.DIRECT, USER)
    settings = await test_container.get(Settings)
    clock = ManualClock()
    request = BudgetRequest("admin-empty-quotas", replace(usage, bypass_personal_quota=True), ONE, 400)
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyBudgetStore(cache, namespace)
        assert (await store.reserve(request, clock.now(), (0, 0))).allowed
        key = namespace.value + "budget:reservation:" + request.reservation_id
        raw = json.loads(await cache.client.get(key))
        raw["quotas"] = {}
        await cache.client.set(key, json.dumps(raw))
        with WorkerRuntime(settings, clock, namespace, UsageSafetyCaps()) as runtime:
            settled = await asyncio.to_thread(runtime.budgets.finish, request.reservation_id, ONE, clock.now())
            assert settled.state == ReservationState.COMMITTED
            before = await cache.client.pttl(key)
            assert before > 0
            replay = await asyncio.to_thread(runtime.budgets.finish, request.reservation_id, ONE, clock.now())
            assert replay.state == ReservationState.COMMITTED
            assert 0 < await cache.client.pttl(key) <= before
