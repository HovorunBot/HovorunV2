"""Valkey implementation of atomic quota, burst and concurrency reservations."""

import hashlib
import json
import math

from redis.exceptions import RedisError

from hovorunv2.application.budgets import (
    RUNTIME_RETENTION_SECONDS,
    TECHNICAL_ID,
    BudgetPort,
    BudgetRequest,
    ReservationDecision,
    ReservationState,
    RuntimeNamespace,
    UnitAmount,
)
from hovorunv2.domain.enums import TargetKind
from hovorunv2.infrastructure.budget_scripts import FINISH_BUDGET, RESERVE_BUDGET
from hovorunv2.infrastructure.cache import CacheService


class ValkeyBudgetStore(BudgetPort):
    """Fail closed on unavailable Valkey; never log request or dependency exception contents."""

    def __init__(self, cache: CacheService, namespace: RuntimeNamespace) -> None:
        self._cache = cache
        self._prefix = namespace.value + "budget:"

    def _payload(self, request: BudgetRequest, now: float, totals: tuple[int, ...]) -> dict[str, object]:
        usage = request.usage
        subject = f"{usage.subject.kind.value}:{usage.subject.telegram_id}"
        estimates = {amount.unit.value: amount.amount for amount in request.estimates}
        bypass = usage.bypass_personal_quota and usage.subject.kind == TargetKind.USER
        quotas = []
        concurrency = [{"key": self._prefix + "global", "limit": usage.safety.global_concurrency}]
        for policy, baseline in zip(usage.policies, totals, strict=True):
            scope = f"{subject}:{policy.bucket}:{policy.unit.value}:{policy.window.seconds}"
            start = policy.window.start(int(now))
            if not bypass:
                quotas.append(
                    {
                        "key": self._prefix + f"quota:{scope}:{start}",
                        "rate": self._prefix + f"rate:{scope}",
                        "unit": policy.unit.value,
                        "amount": estimates[policy.unit.value],
                        "baseline": baseline,
                        "limit": policy.limit,
                        "burst": policy.burst,
                        "seconds": policy.window.seconds,
                        "ends": start + policy.window.seconds,
                    }
                )
                concurrency.append({"key": self._prefix + f"concurrency:{scope}", "limit": policy.concurrency})
        binding = {
            "subject": subject,
            "generation": usage.subject.generation,
            "deadline": request.deadline,
            "estimates": estimates,
            "quotas": quotas,
            "concurrency": concurrency,
        }
        # Runtime time and increasing durable baselines must not change a reservation's identity.
        identity = {
            **binding,
            "quotas": [
                {key: value for key, value in quota.items() if key not in {"baseline", "key", "ends"}}
                for quota in quotas
            ],
        }
        return {
            **binding,
            "id": request.reservation_id,
            "now": now,
            "retention": RUNTIME_RETENTION_SECONDS,
            "fingerprint": hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
        }

    async def _run(self, script: str, reservation_id: str, arguments: list[str]) -> ReservationDecision:
        try:
            result = await self._cache.run_script(
                script, [self._prefix + f"reservation:{reservation_id}", self._prefix + "active"], arguments
            )
        except RedisError, OSError:
            return ReservationDecision(ReservationState.UNAVAILABLE)
        if not isinstance(result, list) or not result:
            return ReservationDecision(ReservationState.UNAVAILABLE)
        state = result[0].decode() if isinstance(result[0], bytes) else str(result[0])
        if state == "conflict":
            msg = "Reservation ID or settlement conflicts with its original operation."
            raise ValueError(msg)
        retry = float(result[1]) if len(result) > 1 else None
        return ReservationDecision(ReservationState(state), retry)

    async def reserve(self, request: BudgetRequest, now: float, durable_totals: tuple[int, ...]) -> ReservationDecision:
        """Acquire all dimensions in one script; durable totals seed missing quota windows."""
        if (
            not math.isfinite(now)
            or now < 0
            or len(durable_totals) != len(request.usage.policies)
            or any(type(total) is not int or total < 0 or total > (1 << 53) - 1 for total in durable_totals)
        ):
            msg = "Reservations require finite time and exact nonnegative durable totals for every policy."
            raise ValueError(msg)
        values = [request.usage.safety.global_concurrency]
        for policy in request.usage.policies:
            values.extend((policy.limit, policy.burst, policy.concurrency, policy.window.seconds))
        if any(value > (1 << 53) - 1 for value in values):
            msg = "Runtime policy exceeds exact Lua integer capacity."
            raise ValueError(msg)
        return await self._run(
            RESERVE_BUDGET, request.reservation_id, [json.dumps(self._payload(request, now, durable_totals))]
        )

    async def finish(self, reservation_id: str, actual: tuple[UnitAmount, ...], now: float) -> ReservationDecision:
        """Settle once; missing runtime state pauses rather than silently resetting usage."""
        if TECHNICAL_ID.fullmatch(reservation_id) is None or not math.isfinite(now) or now < 0 or not actual:
            msg = "Settlement requires a technical reservation ID, finite time and actual amounts."
            raise ValueError(msg)
        amounts = {item.unit.value: item.amount for item in actual}
        if len(amounts) != len(actual):
            msg = "Settlement units must be unique."
            raise ValueError(msg)
        return await self._run(FINISH_BUDGET, reservation_id, [json.dumps(amounts), str(now)])
