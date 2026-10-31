"""Combine stable accounting baselines with atomic runtime execution reservations."""

from hovorunv2.application.usage.budgets import (
    BudgetPort,
    BudgetRequest,
    Clock,
    ReservationDecision,
    UnitAmount,
)
from hovorunv2.application.usage.usage_ledger import AccountingSubject, UsageLedgerStore


class BudgetService:
    """Reserve immediately before execution; waiting never occupies a worker slot here."""

    def __init__(self, budgets: BudgetPort, ledger: UsageLedgerStore, clock: Clock) -> None:
        self._budgets = budgets
        self._ledger = ledger
        self._clock = clock

    async def reserve(self, request: BudgetRequest) -> ReservationDecision:
        """Seed absent runtime quota windows from durable totals, preserving restart budgets."""
        now = self._clock.now()
        subject = AccountingSubject(request.usage.subject.kind, request.usage.subject.telegram_id)
        totals = tuple(
            [
                await self._ledger.total(subject, policy.bucket, policy.unit, policy.window, int(now))
                for policy in request.usage.policies
            ]
        )
        return await self._budgets.reserve(request, now, totals)

    async def finish(self, reservation_id: str, actual: tuple[UnitAmount, ...]) -> ReservationDecision:
        """Settle runtime counters; durable idempotent accounting remains a separate replayable operation."""
        return await self._budgets.finish(reservation_id, actual, self._clock.now())
