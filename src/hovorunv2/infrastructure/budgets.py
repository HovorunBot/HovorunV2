"""Native Valkey optimistic transactions for quota, burst and concurrency reservations."""

import math
from collections.abc import Callable

import redis
from redis.asyncio.client import Pipeline as AsyncPipeline
from redis.client import Pipeline
from redis.exceptions import RedisError, WatchError

from hovorunv2.application.budgets import (
    BudgetPort,
    BudgetRequest,
    ReservationDecision,
    ReservationState,
    RuntimeNamespace,
    UnitAmount,
)
from hovorunv2.infrastructure.budget_state import (
    TRANSACTION_ATTEMPTS,
    BudgetMutation,
    BudgetRecord,
    BudgetStateUnavailableError,
    build_record,
    calculate_finish,
    calculate_reserve,
    parse_hash,
    parse_members,
    parse_record,
    validate_finish_args,
    validate_reserve_args,
    verify_active_ownership,
)
from hovorunv2.infrastructure.cache import CacheService


def _require_index_type(raw: object) -> None:
    value = raw.decode() if isinstance(raw, bytes) else raw
    if value not in {"none", "zset"}:
        raise BudgetStateUnavailableError


class ValkeyBudgetStore(BudgetPort):
    """Keep explicit async transactions separate from the synchronous worker connection owner."""

    def __init__(self, cache: CacheService, namespace: RuntimeNamespace) -> None:
        self._cache = cache
        self._prefix = namespace.value + "budget:"

    async def _snapshot(
        self,
        pipe: AsyncPipeline,
        record: BudgetRecord,
        *,
        require_active: bool = False,
    ) -> tuple[dict[str, dict[str, str]], dict[str, frozenset[str]]]:
        await pipe.watch(*record.resource_keys(), self._prefix + "active")
        _require_index_type(await pipe.type(self._prefix + "active"))
        if require_active and await pipe.zscore(self._prefix + "active", record.id) is None:
            raise BudgetStateUnavailableError
        hashes = {}
        for quota in record.quotas:
            hashes[quota.key] = parse_hash(await pipe.execute_command("HGETALL", quota.key))
            hashes[quota.rate] = parse_hash(await pipe.execute_command("HGETALL", quota.rate))
        members = {
            item.key: parse_members(await pipe.execute_command("SMEMBERS", item.key)) for item in record.concurrency
        }
        return hashes, members

    async def _reserve_attempt(self, record: BudgetRecord) -> ReservationDecision:
        key = self._prefix + "reservation:" + record.id
        async with self._cache.client.pipeline() as pipe:
            try:
                await pipe.watch(key)
                raw = await pipe.get(key)
                if raw is not None:
                    return await self._replay_reservation(pipe, record, raw)
                if record.deadline <= record.now:
                    return ReservationDecision(ReservationState.EXPIRED)
                hashes, members = await self._snapshot(pipe, record)
                mutation = calculate_reserve(record, hashes, members)
                if mutation.record is None:
                    return mutation.decision
                self._write_reservation_mutation(pipe, key, record, mutation)
                await pipe.execute()
            except BudgetStateUnavailableError:
                # Verify that missing ownership is stable rather than a concurrent settlement snapshot.
                pipe.multi()
                await pipe.execute()
                raise
            else:
                return mutation.decision

    async def _replay_reservation(self, pipe: AsyncPipeline, record: BudgetRecord, raw: object) -> ReservationDecision:
        old = parse_record(raw, self._prefix, record.id)
        if old.fingerprint != record.fingerprint:
            msg = "Reservation ID or settlement conflicts with its original operation."
            raise ValueError(msg)
        if old.state == ReservationState.RESERVED:
            if record.now >= record.deadline:
                return ReservationDecision(ReservationState.EXPIRED)
            hashes, members = await self._snapshot(pipe, old, require_active=True)
            verify_active_ownership(old, hashes, members)
        return ReservationDecision(old.state)

    def _write_reservation_mutation(
        self, pipe: AsyncPipeline, key: str, record: BudgetRecord, mutation: BudgetMutation
    ) -> None:
        pipe.multi()
        for update in mutation.updates:
            quota = update.quota
            pipe.hset(quota.key, mapping={"used": update.used, "held": update.held})
            pipe.hset(quota.rate, mapping={"tokens": update.tokens, "at": update.at})
            pipe.expire(quota.key, math.ceil(quota.ends - record.now + record.retention))
            pipe.expire(quota.rate, math.ceil(quota.seconds + record.retention))
        for item in record.concurrency:
            pipe.sadd(item.key, record.id)
        pipe.set(key, record.serialized())
        pipe.zadd(self._prefix + "active", {record.id: record.deadline})

    async def reserve(self, request: BudgetRequest, now: float, durable_totals: tuple[int, ...]) -> ReservationDecision:
        """Acquire every resource or none; contention retries remain bounded and fail closed."""
        validate_reserve_args(request, now, durable_totals)
        record = build_record(self._prefix, request, now, durable_totals)
        for _ in range(TRANSACTION_ATTEMPTS):
            try:
                return await self._reserve_attempt(record)
            except WatchError:
                continue
            except RedisError, OSError, UnicodeError, BudgetStateUnavailableError:
                break
        return ReservationDecision(ReservationState.UNAVAILABLE)

    async def _finish_attempt(self, reservation_id: str, actual: dict[str, int], now: float) -> ReservationDecision:
        key = self._prefix + "reservation:" + reservation_id
        async with self._cache.client.pipeline() as pipe:
            try:
                await pipe.watch(key)
                raw = await pipe.get(key)
                if raw is None:
                    return ReservationDecision(ReservationState.UNAVAILABLE)
                record = parse_record(raw, self._prefix, reservation_id)
                hashes, members = (
                    await self._snapshot(pipe, record, require_active=True)
                    if record.state == ReservationState.RESERVED
                    else ({}, {})
                )
                mutation = calculate_finish(record, actual, now, hashes, members)
                settled = mutation.record
                if settled is None:
                    return mutation.decision
                self._write_settlement_mutation(pipe, key, record, settled, mutation)
                await pipe.execute()
            except BudgetStateUnavailableError:
                # Verify that missing ownership is stable rather than a concurrent settlement snapshot.
                pipe.multi()
                await pipe.execute()
                raise
            else:
                return mutation.decision

    def _write_settlement_mutation(
        self, pipe: AsyncPipeline, key: str, record: BudgetRecord, settled: BudgetRecord, mutation: BudgetMutation
    ) -> None:
        pipe.multi()
        for update in mutation.updates:
            pipe.hset(update.quota.key, mapping={"held": update.held, "used": update.used})
            pipe.hset(update.quota.rate, mapping={"tokens": update.tokens, "at": update.at})
        for item in record.concurrency:
            pipe.srem(item.key, record.id)
        pipe.set(key, settled.serialized(), ex=record.retention)
        pipe.zrem(self._prefix + "active", record.id)

    async def finish(self, reservation_id: str, actual: tuple[UnitAmount, ...], now: float) -> ReservationDecision:
        """Settle once without renewing terminal receipts or guessing missing ownership."""
        amounts = validate_finish_args(reservation_id, actual, now)
        for _ in range(TRANSACTION_ATTEMPTS):
            try:
                return await self._finish_attempt(reservation_id, amounts, now)
            except WatchError:
                continue
            except RedisError, OSError, UnicodeError, BudgetStateUnavailableError:
                break
        return ReservationDecision(ReservationState.UNAVAILABLE)


class WorkerBudgetClient:
    """Use native synchronous optimistic transactions inside the resource-owning worker child."""

    def __init__(self, client: redis.Redis, namespace: RuntimeNamespace, require_owner: Callable[[], None]) -> None:
        self._client = client
        self._prefix = namespace.value + "budget:"
        self._require_owner = require_owner

    def _snapshot(
        self,
        pipe: Pipeline,
        record: BudgetRecord,
        *,
        require_active: bool = False,
    ) -> tuple[dict[str, dict[str, str]], dict[str, frozenset[str]]]:
        pipe.watch(*record.resource_keys(), self._prefix + "active")
        _require_index_type(pipe.type(self._prefix + "active"))
        if require_active and pipe.zscore(self._prefix + "active", record.id) is None:
            raise BudgetStateUnavailableError
        hashes = {}
        for quota in record.quotas:
            hashes[quota.key] = parse_hash(pipe.hgetall(quota.key))
            hashes[quota.rate] = parse_hash(pipe.hgetall(quota.rate))
        members = {item.key: parse_members(pipe.smembers(item.key)) for item in record.concurrency}
        return hashes, members

    def _reserve_attempt(self, record: BudgetRecord) -> ReservationDecision:
        key = self._prefix + "reservation:" + record.id
        with self._client.pipeline() as pipe:
            try:
                pipe.watch(key)
                raw = pipe.get(key)
                if raw is not None:
                    return self._replay_reservation(pipe, record, raw)
                if record.deadline <= record.now:
                    return ReservationDecision(ReservationState.EXPIRED)
                hashes, members = self._snapshot(pipe, record)
                mutation = calculate_reserve(record, hashes, members)
                if mutation.record is None:
                    return mutation.decision
                self._write_reservation_mutation(pipe, key, record, mutation)
                pipe.execute()
            except BudgetStateUnavailableError:
                # Verify that missing ownership is stable rather than a concurrent settlement snapshot.
                pipe.multi()
                pipe.execute()
                raise
            else:
                return mutation.decision

    def _replay_reservation(self, pipe: Pipeline, record: BudgetRecord, raw: object) -> ReservationDecision:
        old = parse_record(raw, self._prefix, record.id)
        if old.fingerprint != record.fingerprint:
            msg = "Reservation ID or settlement conflicts with its original operation."
            raise ValueError(msg)
        if old.state == ReservationState.RESERVED:
            if record.now >= record.deadline:
                return ReservationDecision(ReservationState.EXPIRED)
            hashes, members = self._snapshot(pipe, old, require_active=True)
            verify_active_ownership(old, hashes, members)
        return ReservationDecision(old.state)

    def _write_reservation_mutation(
        self, pipe: Pipeline, key: str, record: BudgetRecord, mutation: BudgetMutation
    ) -> None:
        pipe.multi()
        for update in mutation.updates:
            quota = update.quota
            pipe.hset(quota.key, mapping={"used": update.used, "held": update.held})
            pipe.hset(quota.rate, mapping={"tokens": update.tokens, "at": update.at})
            pipe.expire(quota.key, math.ceil(quota.ends - record.now + record.retention))
            pipe.expire(quota.rate, math.ceil(quota.seconds + record.retention))
        for item in record.concurrency:
            pipe.sadd(item.key, record.id)
        pipe.set(key, record.serialized())
        pipe.zadd(self._prefix + "active", {record.id: record.deadline})

    def reserve(self, request: BudgetRequest, now: float, durable_totals: tuple[int, ...]) -> ReservationDecision:
        """Reserve synchronously with the same pure decisions and original identity as bot admission."""
        self._require_owner()
        validate_reserve_args(request, now, durable_totals)
        record = build_record(self._prefix, request, now, durable_totals)
        for _ in range(TRANSACTION_ATTEMPTS):
            try:
                return self._reserve_attempt(record)
            except WatchError:
                continue
            except RedisError, OSError, UnicodeError, BudgetStateUnavailableError:
                break
        return ReservationDecision(ReservationState.UNAVAILABLE)

    def _finish_attempt(self, reservation_id: str, actual: dict[str, int], now: float) -> ReservationDecision:
        key = self._prefix + "reservation:" + reservation_id
        with self._client.pipeline() as pipe:
            try:
                pipe.watch(key)
                raw = pipe.get(key)
                if raw is None:
                    return ReservationDecision(ReservationState.UNAVAILABLE)
                record = parse_record(raw, self._prefix, reservation_id)
                hashes, members = (
                    self._snapshot(pipe, record, require_active=True)
                    if record.state == ReservationState.RESERVED
                    else ({}, {})
                )
                mutation = calculate_finish(record, actual, now, hashes, members)
                settled = mutation.record
                if settled is None:
                    return mutation.decision
                self._write_settlement_mutation(pipe, key, record, settled, mutation)
                pipe.execute()
            except BudgetStateUnavailableError:
                # Verify that missing ownership is stable rather than a concurrent settlement snapshot.
                pipe.multi()
                pipe.execute()
                raise
            else:
                return mutation.decision

    def _write_settlement_mutation(
        self, pipe: Pipeline, key: str, record: BudgetRecord, settled: BudgetRecord, mutation: BudgetMutation
    ) -> None:
        pipe.multi()
        for update in mutation.updates:
            pipe.hset(update.quota.key, mapping={"held": update.held, "used": update.used})
            pipe.hset(update.quota.rate, mapping={"tokens": update.tokens, "at": update.at})
        for item in record.concurrency:
            pipe.srem(item.key, record.id)
        pipe.set(key, settled.serialized(), ex=record.retention)
        pipe.zrem(self._prefix + "active", record.id)

    def finish(self, reservation_id: str, actual: tuple[UnitAmount, ...], now: float) -> ReservationDecision:
        """Reconcile actual consumption only after every held resource can be verified."""
        self._require_owner()
        amounts = validate_finish_args(reservation_id, actual, now)
        for _ in range(TRANSACTION_ATTEMPTS):
            try:
                return self._finish_attempt(reservation_id, amounts, now)
            except WatchError:
                continue
            except RedisError, OSError, UnicodeError, BudgetStateUnavailableError:
                break
        return ReservationDecision(ReservationState.UNAVAILABLE)
